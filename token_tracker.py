"""Small always-on-top watcher showing token usage and cost from Claude Code logs.

Follows the chat that is focused in Claude Desktop (falls back to the most
recently written log) and shows what the last request cost (your message plus
the whole reply, including tool calls) and the whole session.

Controls: drag = move, double-click / right-click / x = close.
Hover a label or value for an explanation.

Settings (environment variables):
  TOKEN_TRACKER_CURRENCY   USD (default), EUR, GBP, CZK, PLN, CHF, JPY, CAD, AUD,
                           CNY, INR, SEK, NOK, DKK, KRW, BRL, MXN (daily ECB rate)
  TOKEN_TRACKER_HOST_EXE   exe whose window must be in front for the tracker to
                           show (default claude.exe). Set to "any" to always show.
"""
import ctypes
import glob
import json
import os
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path

LOG_ROOT = Path.home() / ".claude" / "projects"
POLL_MS = 500
RESCAN_EVERY = 4  # every Nth poll looks for the newest/focused log (~2 s)

# code: (symbol, decimals, symbol after the number, rough fallback rate per 1 USD)
CURRENCIES = {
    "USD": ("$", 2, False, 1.0),
    "EUR": ("€", 2, False, 0.90),
    "GBP": ("£", 2, False, 0.77),
    "CZK": ("Kč", 2, True, 22.0),
    "PLN": ("zł", 2, True, 3.8),
    "CHF": ("CHF", 2, True, 0.80),
    "JPY": ("¥", 0, False, 150.0),
    "CAD": ("C$", 2, False, 1.37),
    "AUD": ("A$", 2, False, 1.50),
    "CNY": ("CN¥", 2, False, 7.2),
    "INR": ("₹", 2, False, 84.0),
    "SEK": ("kr", 2, True, 10.5),
    "NOK": ("kr", 2, True, 10.8),
    "DKK": ("kr", 2, True, 6.8),
    "KRW": ("₩", 0, False, 1380.0),
    "BRL": ("R$", 2, False, 5.5),
    "MXN": ("MX$", 2, False, 18.5),
}
CURRENCY = os.environ.get("TOKEN_TRACKER_CURRENCY", "USD").upper()
if CURRENCY not in CURRENCIES:
    CURRENCY = "USD"
HOST_EXE = os.environ.get("TOKEN_TRACKER_HOST_EXE", "claude.exe").lower()
VIS_POLL_MS = 250


def _session_roots():
    """Claude Desktop metadata dirs. The Store build keeps %APPDATA% data in a package."""
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pattern = os.path.join(local, "Packages", "Claude_*", "LocalCache",
                               "Roaming", "Claude", "claude-code-sessions")
        roots += [Path(p) for p in glob.glob(pattern)]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Claude" / "claude-code-sessions")
    return roots


SESSIONS_ROOTS = _session_roots()

# --- Prices (USD per 1M tokens): (input, output). Cache read 0.1x, write 1.25x (5 min) / 2x (1 h)
# Edit these when Anthropic changes pricing. First substring match wins.
PRICES = [
    ("fable", 10.0, 50.0),
    ("mythos", 10.0, 50.0),
    ("opus-5-5", 4.0, 20.0),
    ("opus", 5.0, 25.0),
    ("sonnet-4", 3.0, 15.0),
    ("sonnet", 2.0, 10.0),
    ("haiku", 1.0, 5.0),
]
DEFAULT_PRICE = (5.0, 25.0)  # unknown model = pessimistic estimate
RATE_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols="  # ECB rates, no key
RATE_REFRESH_S = 3600

# --- Palette (Claude Desktop dark mode)
BG = "#262624"
BG_SOFT = "#30302E"
BORDER = "#3D3D3A"
FG = "#FAF9F5"
MUTED = "#9C9A92"
ORANGE = "#D97757"
TRACK = "#3A3A37"

SERIF = ("Georgia", 11)
SANS = "Segoe UI"
MONO = "Consolas"

# (key, label, explanation)
ROWS = [
    ("input", "Input",
     "New tokens the model processed from scratch (not cached)\n"
     "in this request. A token is roughly 3-4 characters."),
    ("output", "Output",
     "Tokens Claude generated\n"
     "(including thinking and tool calls)."),
    ("cache_read", "Cache read",
     "Earlier conversation loaded from cache.\n"
     "Fast and about 10x cheaper than processing again."),
    ("cache_write", "Cache write",
     "New tokens stored in cache so later messages\n"
     "can read them cheaply. Slightly pricier than input."),
]


def price_for(model):
    m = (model or "").lower()
    for key, pin, pout in PRICES:
        if key in m:
            return pin, pout
    return DEFAULT_PRICE


def context_limit(model):
    return 200_000 if "haiku" in (model or "").lower() else 1_000_000


def split_usage(usage):
    """Split usage into (input, output, cache read, cache write 5 min, cache write 1 h)."""
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    cc = usage.get("cache_creation_input_tokens", 0) or 0
    detail = usage.get("cache_creation")
    if isinstance(detail, dict):
        c1h = detail.get("ephemeral_1h_input_tokens", 0) or 0
        c5m = detail.get("ephemeral_5m_input_tokens", 0) or 0
        if c1h + c5m == 0 and cc:
            c5m = cc
    else:
        c1h, c5m = 0, cc
    return inp, out, cr, c5m, c1h


def cost_parts_usd(model, usage):
    """USD for (input, output, cache read, cache write)."""
    inp, out, cr, c5m, c1h = split_usage(usage)
    pin, pout = price_for(model)
    return (inp * pin / 1e6,
            out * pout / 1e6,
            cr * pin * 0.1 / 1e6,
            (c5m * 1.25 + c1h * 2.0) * pin / 1e6)


def foreground_exe():
    """(lowercase exe name, PID) of the foreground window; ('', 0) on failure."""
    try:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong)]
        pid = ctypes.c_ulong(0)
        u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "", pid.value
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = ctypes.c_ulong(520)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower(), pid.value
        finally:
            k32.CloseHandle(h)
        return "", pid.value
    except Exception:
        return "", 0


def fmt(n):
    return f"{n:,}".replace(",", " ")


def fmt_money(usd, rate):
    sym, dec, after, _fb = CURRENCIES[CURRENCY]
    amount = usd * rate
    if CURRENCY == "USD" and amount < 0.1:
        dec = 4
    text = f"{amount:,.{dec}f}"
    if after:  # European style: 1 234,56 Kč
        return text.replace(",", " ").replace(".", ",") + " " + sym
    return sym + text


class Rate:
    """USD->selected currency rate from the ECB (via frankfurter.dev), fetched in the background."""

    def __init__(self):
        self.value = CURRENCIES[CURRENCY][3]
        self.live = False
        self._last = 0.0

    def maybe_refresh(self):
        if CURRENCY == "USD" or time.time() - self._last < RATE_REFRESH_S:
            return
        self._last = time.time()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            req = urllib.request.Request(RATE_URL + CURRENCY,
                                         headers={"User-Agent": "token-tracker"})
            with urllib.request.urlopen(req, timeout=8) as r:
                self.value = float(json.load(r)["rates"][CURRENCY])
            self.live = True
        except Exception:
            self._last = time.time() - RATE_REFRESH_S + 120  # retry in 2 min


class Tooltip:
    def __init__(self, widgets, text):
        self.text = text
        self.tip = None
        for w in widgets:
            w.bind("<Enter>", self.show, add="+")
            w.bind("<Leave>", self.hide, add="+")
            w.bind("<ButtonPress-1>", self.hide, add="+")

    def show(self, e):
        if self.tip:
            return
        self.tip = tk.Toplevel()
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, justify="left", bg="#1B1B1A", fg=FG,
                 font=(SANS, 9), padx=9, pady=6,
                 highlightthickness=1, highlightbackground=BORDER).pack()
        self.tip.geometry(f"+{e.x_root + 14}+{e.y_root + 16}")

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def newest_log():
    newest, newest_mtime = None, -1.0
    if not LOG_ROOT.is_dir():
        return None
    for dirpath, _dirs, files in os.walk(LOG_ROOT):
        for name in files:
            if name.endswith(".jsonl"):
                p = os.path.join(dirpath, name)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if m > newest_mtime:
                    newest, newest_mtime = p, m
    return newest


_sess_cache = {}  # metadata path -> (mtime, cliSessionId, lastFocusedAt)
_log_by_id = {}   # cliSessionId -> path to .jsonl


def focused_log():
    """Log of the chat last focused in Claude Desktop, or None."""
    best = None  # (lastFocusedAt, cliSessionId)
    seen = set()
    walks = (w for root in SESSIONS_ROOTS if root.is_dir() for w in os.walk(root))
    for dirpath, _dirs, files in walks:
        for name in files:
            if not (name.startswith("local_") and name.endswith(".json")):
                continue
            p = os.path.join(dirpath, name)
            seen.add(p)
            try:
                m = os.path.getmtime(p)
                if _sess_cache.get(p, (None,))[0] != m:
                    with open(p, "rb") as f:
                        d = json.load(f)
                    _sess_cache[p] = (m, d.get("cliSessionId"),
                                      d.get("lastFocusedAt") or 0)
            except (OSError, ValueError):
                continue
            _m, cid, focus = _sess_cache[p]
            if cid and (best is None or focus > best[0]):
                best = (focus, cid)
    for p in set(_sess_cache) - seen:
        del _sess_cache[p]
    if not best:
        return None
    cid = best[1]
    path = _log_by_id.get(cid)
    if not path or not os.path.exists(path):
        path = None
        for d in LOG_ROOT.glob(f"*/{cid}.jsonl"):
            path = str(d)
            _log_by_id[cid] = path
            break
    return path


def is_new_prompt(obj):
    """True for a log line holding a new user message (not a tool result)."""
    msg = obj.get("message")
    if obj.get("type") != "user" or not isinstance(msg, dict):
        return False
    if obj.get("isSidechain"):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in content)
    return True


class Tracker:
    def __init__(self, root):
        self.root = root
        self.rate = Rate()
        self.path = None
        self.offset = 0
        self.tick = 0
        self.turn = {}      # message id -> (model, usage) for the current request
        self.session = {}   # message id -> USD cost for the whole session
        self.last_model = None
        self.last_usage = None
        self.values = {}
        self.costs = {}

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.97)
        root.configure(bg=BORDER)

        frame = tk.Frame(root, bg=BG)
        frame.pack(padx=1, pady=1)
        self.frame = frame

        # --- header
        head = tk.Frame(frame, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(head, text="✻", bg=BG, fg=ORANGE,
                 font=(SANS, 14, "bold")).pack(side="left")
        self.title = tk.Label(head, text="Claude", bg=BG, fg=FG, font=SERIF)
        self.title.pack(side="left", padx=(6, 0))
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(SANS, 9), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.root.destroy())
        close.bind("<Enter>", lambda _e: close.config(fg=FG))
        close.bind("<Leave>", lambda _e: close.config(fg=MUTED))
        self.model_lbl = tk.Label(head, text="", bg=BG, fg=MUTED, font=(SANS, 8))
        self.model_lbl.pack(side="right", padx=(0, 10))
        Tooltip([self.title], "Token usage and cost from the focused chat's log\n"
                              "in ~/.claude/projects. Prices are an estimate\n"
                              "based on API list prices.")

        # --- hero number: cost of the last request
        hero = tk.Frame(frame, bg=BG)
        hero.pack(fill="x", padx=16, pady=(12, 0))
        self.hero_val = tk.Label(hero, text="–", bg=BG, fg=ORANGE,
                                 font=("Georgia", 24), anchor="w")
        self.hero_val.pack(anchor="w")
        self.hero_sub = tk.Label(hero, text="last request incl. reply", bg=BG,
                                 fg=MUTED, font=(SANS, 8), anchor="w")
        self.hero_sub.pack(anchor="w")
        Tooltip([self.hero_val, self.hero_sub],
                "Cost of your last message plus everything Claude did in\n"
                "the reply (output, tool calls, cache reads/writes).")

        # --- whole session
        sess = tk.Frame(frame, bg=BG_SOFT)
        sess.pack(fill="x", padx=16, pady=(12, 0))
        s_name = tk.Label(sess, text="Whole session", bg=BG_SOFT, fg=MUTED,
                          font=(SANS, 9), anchor="w")
        s_name.pack(side="left", padx=(10, 0), pady=6)
        self.sess_val = tk.Label(sess, text="–", bg=BG_SOFT, fg=FG,
                                 font=(MONO, 10, "bold"), anchor="e")
        self.sess_val.pack(side="right", padx=(0, 10))
        Tooltip([sess, s_name, self.sess_val],
                "Sum of all replies in this log (session).")

        # --- token and cost rows
        grid = tk.Frame(frame, bg=BG)
        grid.pack(fill="x", padx=16, pady=(10, 0))
        for key, label, tip in ROWS:
            row = tk.Frame(grid, bg=BG)
            row.pack(fill="x", pady=1)
            name = tk.Label(row, text=label, bg=BG, fg=MUTED, font=(SANS, 9),
                            width=12, anchor="w")
            name.pack(side="left")
            money = tk.Label(row, text="", bg=BG, fg=MUTED, font=(SANS, 8),
                             width=9, anchor="e")
            money.pack(side="right")
            val = tk.Label(row, text="–", bg=BG, fg=FG, font=(MONO, 10, "bold"),
                           width=9, anchor="e")
            val.pack(side="right")
            self.values[key] = val
            self.costs[key] = money
            Tooltip([row, name, val, money], f"{label}\n{tip}")

        # --- context
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(10, 8))
        ctx_head = tk.Frame(frame, bg=BG)
        ctx_head.pack(fill="x", padx=16)
        ctx_name = tk.Label(ctx_head, text="Context", bg=BG, fg=FG,
                            font=(SANS, 9, "bold"), anchor="w")
        ctx_name.pack(side="left")
        self.ctx_val = tk.Label(ctx_head, text="–", bg=BG, fg=FG,
                                font=(MONO, 10, "bold"), anchor="e")
        self.ctx_val.pack(side="right")
        self.bar = tk.Canvas(frame, width=240, height=5, bg=TRACK,
                             highlightthickness=0)
        self.bar.pack(padx=16, pady=(5, 0))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 5, fill=ORANGE, width=0)
        self.foot = tk.Label(frame, text="", bg=BG, fg=MUTED, font=(SANS, 8), anchor="w")
        self.foot.pack(fill="x", padx=16, pady=(8, 12))
        Tooltip([ctx_head, ctx_name, self.ctx_val, self.bar],
                "How many tokens the model saw in the last reply in total\n"
                "(input + cache read + cache write) versus the window limit.")

        self._bind_window(root)
        root.update_idletasks()
        self._round_corners()
        self.visible = True
        self.poll()
        self.watch_host()

    # --- window
    def watch_host(self):
        """Show the window only while Claude Desktop (or the tracker) is in front."""
        exe, pid = foreground_exe()
        want = HOST_EXE == "any" or exe == HOST_EXE or pid == os.getpid() or pid == 0
        if want and not self.visible:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.visible = True
        elif not want and self.visible:
            self.root.withdraw()
            self.visible = False
        self.root.after(VIS_POLL_MS, self.watch_host)

    def _round_corners(self):
        """Windows 11: rounded corners and a dark border (silently ignored on older)."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)          # corners: round
            dwm(hwnd, 34, ctypes.byref(ctypes.c_int(0x3A3D3D)), 4)   # border #3D3D3A
        except Exception:
            pass

    def _bind_window(self, w):
        w.bind("<ButtonPress-1>", self._drag_start, add="+")
        w.bind("<B1-Motion>", self._drag_move, add="+")
        w.bind("<Double-Button-1>", lambda _e: self.root.destroy(), add="+")
        w.bind("<Button-3>", lambda _e: self.root.destroy(), add="+")
        for c in w.winfo_children():
            self._bind_window(c)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # --- data
    def reset(self):
        self.turn.clear()
        self.session.clear()
        self.last_model = None
        self.last_usage = None
        self.ctx_override = None   # context size right after a /compact

    def feed(self, line):
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if is_new_prompt(obj):
            self.turn = {}
            return
        if obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
            post = (obj.get("compactMetadata") or {}).get("postTokens")
            if isinstance(post, int):
                self.ctx_override = post   # until the next reply reports real usage
            return
        msg = obj.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            return
        usage = msg.get("usage")
        model = msg.get("model")
        if not isinstance(usage, dict) or model == "<synthetic>":
            return
        mid = msg.get("id") or obj.get("uuid") or str(len(self.session))
        self.turn[mid] = (model, usage)  # same message is logged repeatedly; last wins
        self.session[mid] = sum(cost_parts_usd(model, usage))
        self.last_model, self.last_usage = model, usage
        self.ctx_override = None

    def render(self):
        rate = self.rate.value
        sums = [0.0, 0.0, 0.0, 0.0]   # USD
        toks = [0, 0, 0, 0]
        for model, usage in self.turn.values():
            for i, c in enumerate(cost_parts_usd(model, usage)):
                sums[i] += c
            inp, out, cr, c5m, c1h = split_usage(usage)
            toks[0] += inp
            toks[1] += out
            toks[2] += cr
            toks[3] += c5m + c1h
        has = bool(self.turn)
        for (key, _l, _t), t, c in zip(ROWS, toks, sums):
            self.values[key].config(text=fmt(t) if has else "–")
            self.costs[key].config(text=fmt_money(c, rate) if has else "")
        self.hero_val.config(text=fmt_money(sum(sums), rate) if has else "–")
        n = len(self.turn)
        self.hero_sub.config(
            text=f"last request incl. reply · {n} model calls" if n > 1
            else "last request incl. reply")
        self.sess_val.config(text=fmt_money(sum(self.session.values()), rate)
                             if self.session else "–")

        if self.last_usage:
            inp, _o, cr, c5m, c1h = split_usage(self.last_usage)
            ctx = inp + cr + c5m + c1h
            if self.ctx_override is not None:
                ctx = self.ctx_override
            limit = context_limit(self.last_model)
            self.ctx_val.config(text=f"{fmt(ctx)} / {fmt(limit // 1000)}k")
            self.bar.coords(self.bar_fill, 0, 0, 240 * min(1.0, ctx / limit), 5)
            self.model_lbl.config(text=(self.last_model or "").replace("claude-", ""))
        if CURRENCY == "USD":
            self.foot.config(text="prices: API list estimate, USD")
        else:
            self.foot.config(
                text=f"1 USD = {rate:,.2f} {CURRENCY} "
                + ("(ECB)" if self.rate.live else "(estimate, rate not fetched)"))

    def switch_to(self, path):
        """Switch to a log file and replay its whole history."""
        self.path = path
        self.reset()
        try:
            with open(path, "rb") as f:
                data = f.read()
                self.offset = f.tell()
            for line in data.decode("utf-8", "replace").splitlines():
                self.feed(line)
        except OSError:
            self.offset = 0
        self.render()

    def read_new(self):
        try:
            size = os.path.getsize(self.path)
            if size < self.offset:  # file truncated/rewritten
                self.switch_to(self.path)
                return
            if size == self.offset:
                return
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
            end = chunk.rfind(b"\n")  # complete lines only
            if end == -1:
                return
            self.offset += end + 1
            for line in chunk[:end].decode("utf-8", "replace").splitlines():
                self.feed(line)
            self.render()
        except OSError:
            pass

    def poll(self):
        self.rate.maybe_refresh()
        if self.path is None or self.tick % RESCAN_EVERY == 0:
            latest = focused_log() or newest_log()
            if latest and latest != self.path:
                self.switch_to(latest)
        self.tick += 1
        if self.path:
            self.read_new()
        if self.tick % 4 == 0:
            self.render()  # also shows a freshly fetched rate
        self.root.after(POLL_MS, self.poll)


if __name__ == "__main__":
    r = tk.Tk()
    r.geometry("+40+40")
    Tracker(r)
    r.mainloop()
