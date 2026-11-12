"""Tests for token_tracker. Run: py -m unittest discover -s tests -v"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV_KEYS = ("TOKEN_TRACKER_CONFIG", "TOKEN_TRACKER_STATE", "TOKEN_TRACKER_LOGS",
            "TOKEN_TRACKER_CURRENCY", "TOKEN_TRACKER_HOST_EXE", "TOKEN_TRACKER_LIMIT")


class Sandbox(unittest.TestCase):
    """Fresh module import with its own config / state / log folders."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tt_test_"))
        self.logs = self.dir / "projects"
        self.logs.mkdir()
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, config=None, raw_config=None, **env):
        if config is not None:
            (self.dir / "cfg.json").write_text(json.dumps(config), encoding="utf-8")
        if raw_config is not None:
            (self.dir / "cfg.json").write_text(raw_config, encoding="utf-8")
        os.environ["TOKEN_TRACKER_CONFIG"] = str(self.dir / "cfg.json")
        os.environ["TOKEN_TRACKER_STATE"] = str(self.dir / "state.json")
        os.environ["TOKEN_TRACKER_LOGS"] = str(self.logs)
        for k in ("TOKEN_TRACKER_CURRENCY", "TOKEN_TRACKER_HOST_EXE", "TOKEN_TRACKER_LIMIT"):
            os.environ.pop(k, None)
        os.environ.update(env)
        sys.modules.pop("token_tracker", None)
        self.tt = importlib.import_module("token_tracker")
        return self.tt


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def reply(mid, epoch, model="claude-opus-5-5", inp=0, out=0):
    """An assistant log line (same key order as the real logs: timestamp last)."""
    return json.dumps({"type": "assistant", "message": {
        "id": mid, "role": "assistant", "model": model,
        "usage": {"input_tokens": inp, "output_tokens": out}},
        "uuid": "u-" + mid, "timestamp": iso(epoch)}, separators=(",", ":")) + "\n"


def prompt(epoch, text="hi"):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text},
                       "uuid": "p-%s" % epoch, "timestamp": iso(epoch)},
                      separators=(",", ":")) + "\n"


def tool_result(epoch):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        "uuid": "r-%s" % epoch, "timestamp": iso(epoch)}, separators=(",", ":")) + "\n"


class ConfigTests(Sandbox):
    def test_defaults_without_a_config_file(self):
        tt = self.load()
        self.assertEqual((tt.CURRENCY, tt.HOST_EXE, tt.LIMIT), ("USD", "claude.exe", 0.0))
        self.assertFalse(tt.AUTO_EXIT)   # waits hidden for Claude to come back

    def test_values_come_from_the_config_file(self):
        tt = self.load(config={"currency": "czk", "host_exe": "Other.EXE", "limit": 150,
                               "auto_exit": True})
        self.assertEqual((tt.CURRENCY, tt.HOST_EXE, tt.LIMIT), ("CZK", "other.exe", 150.0))
        self.assertTrue(tt.AUTO_EXIT)

    def test_environment_overrides_the_config(self):
        tt = self.load(config={"currency": "CZK", "limit": 100},
                       TOKEN_TRACKER_CURRENCY="EUR", TOKEN_TRACKER_LIMIT="7")
        self.assertEqual((tt.CURRENCY, tt.LIMIT), ("EUR", 7.0))

    def test_broken_or_missing_config_falls_back_to_defaults(self):
        for raw in ("{not json", "[1, 2]", "", "null"):
            tt = self.load(raw_config=raw)
            self.assertEqual((tt.CURRENCY, tt.LIMIT), ("USD", 0.0), raw)

    def test_bad_values_are_ignored(self):
        tt = self.load(config={"currency": "XXX", "limit": -5, "prices": "nope",
                               "default_price": [1]})
        self.assertEqual((tt.CURRENCY, tt.LIMIT), ("USD", 0.0))
        self.assertEqual(tt.DEFAULT_PRICE, (5.0, 25.0))
        self.assertEqual(tt.PRICES, tt.BUILTIN_PRICES)
        for bad in ("abc", float("nan"), None, [3], "-1"):
            self.assertEqual(tt._number(bad, 9), 9)

    def test_user_prices_win_and_bad_entries_are_skipped(self):
        tt = self.load(config={"prices": [
            {"match": "Opus-5-5", "input": 1, "output": 2},
            ["nova", 3, 4],
            {"match": "x"}, ["y", "a", 1], {"match": "", "input": 1, "output": 1},
            {"match": "neg", "input": -1, "output": 1}],
            "default_price": [1.5, 7]})
        self.assertEqual(tt.price_for("claude-opus-5-5"), (1.0, 2.0))    # beats the built-in 4 / 20
        self.assertEqual(tt.price_for("claude-nova-1"), (3.0, 4.0))
        self.assertEqual(tt.price_for("claude-unheard-of"), (1.5, 7.0))  # default price
        self.assertEqual(len(tt.PRICES), 2 + len(tt.BUILTIN_PRICES))

    def test_known_model(self):
        tt = self.load()
        self.assertTrue(tt.known_model("claude-sonnet-5-5"))
        self.assertTrue(tt.known_model(None))
        self.assertFalse(tt.known_model("claude-nova-1"))


class StateTests(Sandbox):
    def test_state_round_trip_and_garbage(self):
        tt = self.load()
        self.assertEqual(tt.load_state(), {})
        tt.save_state({"x": 5, "y": 6, "compact": False})
        self.assertEqual(tt.load_state(), {"x": 5, "y": 6, "compact": False})
        (self.dir / "state.json").write_text("garbage")
        self.assertEqual(tt.load_state(), {})

    def test_fit_on_screen(self):
        tt = self.load()
        screen = (0, 0, 1920, 1080)
        self.assertEqual(tt.fit_on_screen(100, 100, 300, 200, screen), (100, 100))
        self.assertEqual(tt.fit_on_screen(-500, -40, 300, 200, screen), (0, 0))
        self.assertEqual(tt.fit_on_screen(1900, 1000, 300, 200, screen), (1620, 880))
        self.assertEqual(tt.fit_on_screen(10, 10, 300, 200, (-1920, 0, 3840, 1080)), (10, 10))
        self.assertEqual(tt.fit_on_screen(-2500, 0, 300, 200, (-1920, 0, 3840, 1080)), (-1920, 0))
        self.assertEqual(tt.fit_on_screen(7, 8, 300, 200, None), (7, 8))


class LogStateTests(Sandbox):
    def feed(self, *lines):
        tt = self.tt
        st = tt.LogState("x.jsonl")
        for line in lines:
            st.feed(line.encode().rstrip(b"\n"))
        return st

    def test_costs_tokens_and_turns(self):
        self.load()
        st = self.feed(prompt(1), reply("a", 2, inp=1_000_000, out=0),       # $4
                       tool_result(3), reply("b", 4, out=1_000_000),          # $20
                       prompt(5), reply("c", 6, inp=500_000))                 # $2
        self.assertAlmostEqual(sum(v[0] for v in st.session.values()), 26.0)
        self.assertEqual(sum(v[1] for v in st.session.values()), 2_500_000)
        self.assertEqual(list(st.turn), ["c"])                                # new prompt starts a turn

    def test_tool_result_does_not_start_a_new_turn(self):
        self.load()
        st = self.feed(prompt(1), reply("a", 2, inp=1), tool_result(3), reply("b", 4, inp=1))
        self.assertEqual(sorted(st.turn), ["a", "b"])

    def test_repeated_message_id_counts_once_last_wins(self):
        self.load()
        st = self.feed(reply("a", 1, out=1_000_000), reply("a", 2, out=2_000_000))
        self.assertAlmostEqual(st.session["a"][0], 40.0)

    def test_unknown_models_are_collected_and_synthetic_skipped(self):
        self.load()
        st = self.feed(reply("a", 1, model="claude-nova-1", inp=10),
                       reply("b", 2, model="<synthetic>", inp=10),
                       reply("c", 3, model="claude-haiku-4-5-20251001", inp=10))
        self.assertEqual(st.unknown, {"claude-nova-1"})
        self.assertEqual(sorted(st.session), ["a", "c"])

    def test_compact_boundary_sets_context_until_next_reply(self):
        self.load()
        boundary = json.dumps({"type": "system", "subtype": "compact_boundary",
                               "compactMetadata": {"postTokens": 4321}}) + "\n"
        st = self.feed(reply("a", 1, inp=1), boundary)
        self.assertEqual(st.ctx_override, 4321)
        st.feed(reply("b", 2, inp=1).encode().rstrip(b"\n"))
        self.assertIsNone(st.ctx_override)

    def test_tailing_reads_only_complete_lines(self):
        self.load()
        p = self.dir / "t.jsonl"
        p.write_bytes(reply("a", 1, out=1_000_000).encode())
        st = self.tt.LogState(str(p))
        self.assertTrue(st.read_new())
        self.assertFalse(st.read_new())
        half = reply("b", 2, out=1_000_000).encode()
        with open(p, "ab") as f:
            f.write(half[:30])
        self.assertFalse(st.read_new())          # incomplete line is left alone
        with open(p, "ab") as f:
            f.write(half[30:])
        self.assertTrue(st.read_new())
        self.assertEqual(sorted(st.session), ["a", "b"])
        p.write_bytes(b"")                        # file rewritten shorter
        self.assertIsNone(st.read_new())

    def test_log_info_sums_earlier_logs_and_caches(self):
        tt = self.load()
        p = self.dir / "old.jsonl"
        p.write_text(reply("a", 1, inp=1_000_000) + reply("b", 2, model="claude-nova-1", inp=1),
                     encoding="utf-8")
        costs, unknown = tt.log_info(str(p))
        self.assertAlmostEqual(costs["a"][0], 4.0)
        self.assertEqual(unknown, {"claude-nova-1"})
        self.assertIs(tt.log_info(str(p))[0], costs)     # cached by mtime and size


class TodayTests(Sandbox):
    NOW = time.mktime((2026, 6, 15, 15, 0, 0, 0, 0, -1))

    def write(self, rel, text, mtime):
        p = self.logs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def today(self):
        tt = self.load()
        return tt, tt.Today(start=False)

    def test_counts_only_replies_since_local_midnight(self):
        tt, today = self.today()
        midnight = tt.local_midnight(self.NOW)
        self.write("p/a.jsonl",
                   reply("old", midnight - 60, out=1_000_000)         # $20, a minute before midnight
                   + reply("new1", midnight + 60, out=1_000_000)       # $20
                   + reply("new2", self.NOW - 10, inp=1_000_000),      # $4
                   self.NOW - 10)
        self.write("p/stale.jsonl", reply("x", midnight - 5000, out=1_000_000), midnight - 4000)
        today.scan(self.NOW)
        usd, tokens, unknown = today.snapshot
        self.assertAlmostEqual(usd, 24.0)
        self.assertEqual(tokens, 2_000_000)
        self.assertFalse(unknown)

    def test_forks_and_subagents(self):
        tt, today = self.today()
        line = reply("shared", self.NOW - 100, out=1_000_000)
        self.write("p/a.jsonl", line, self.NOW - 50)
        self.write("p/b.jsonl", line + reply("own", self.NOW - 90, inp=1_000_000), self.NOW - 50)
        self.write("p/sess/subagents/agent-1.jsonl", reply("sub", self.NOW - 80, inp=1_000_000), self.NOW - 50)
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 20.0 + 4.0 + 4.0)   # the shared reply once

    def test_incremental_reads_do_not_double_count(self):
        tt, today = self.today()
        p = self.write("p/a.jsonl", reply("a", self.NOW - 100, inp=1_000_000), self.NOW - 50)
        today.scan(self.NOW)
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 4.0)
        more = reply("b", self.NOW - 10, inp=1_000_000).encode()
        with open(p, "ab") as f:
            f.write(more[:25])                   # half a line
        os.utime(p, (self.NOW - 5, self.NOW - 5))
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 4.0)
        with open(p, "ab") as f:
            f.write(more[25:])
        os.utime(p, (self.NOW - 4, self.NOW - 4))
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 8.0)

    def test_unknown_model_is_reported(self):
        tt, today = self.today()
        self.write("p/a.jsonl", reply("a", self.NOW - 10, model="claude-nova-1", inp=1), self.NOW - 5)
        today.scan(self.NOW)
        self.assertEqual(today.snapshot[2], {"claude-nova-1"})

    def test_midnight_rollover_drops_yesterday(self):
        tt, today = self.today()
        self.write("p/a.jsonl", reply("a", self.NOW - 100, inp=1_000_000), self.NOW - 50)
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 4.0)
        today.scan(self.NOW + 86400)             # the next day: nothing happened yet
        self.assertEqual(today.snapshot[:2], (0, 0))

    def test_rewritten_file_is_not_double_counted(self):
        tt, today = self.today()
        p = self.write("p/a.jsonl", reply("a", self.NOW - 100, inp=1_000_000)
                       + reply("b", self.NOW - 90, inp=1_000_000), self.NOW - 50)
        today.scan(self.NOW)
        self.write("p/a.jsonl", reply("a", self.NOW - 100, inp=1_000_000), self.NOW - 40)
        today.scan(self.NOW)
        self.assertAlmostEqual(today.snapshot[0], 8.0)   # "b" was seen already, "a" not again


def have_display():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(have_display(), "needs a display")
class WindowTests(Sandbox):
    """Drives a real Tk window against synthetic logs."""

    def start(self, config=None, state=None, **env):
        import tkinter as tk
        tt = self.load(config=config or {}, TOKEN_TRACKER_HOST_EXE="any", **env)
        if state is not None:
            (self.dir / "state.json").write_text(json.dumps(state))
        self.chat = self.logs / "proj" / "chat.jsonl"
        self.chat.parent.mkdir(exist_ok=True)
        if not self.chat.exists():
            self.chat.write_text(prompt(1) + reply("a", 2, inp=500_000), encoding="utf-8")   # $2
        tt.focused_log = lambda: None          # follow the newest log of the sandbox
        self.root = tk.Tk()
        self.tr = tt.Tracker(self.root)
        self.addCleanup(self.close)
        self.flashes = []
        real = self.tr.flash
        # the flash re-schedules itself with a smaller n: count only the first call
        self.tr.flash = lambda n=6: (self.flashes.append(1) if n == 6 else None, real(n))
        return tt

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def pump(self, until, limit=10.0):
        t0 = time.time()
        while time.time() - t0 < limit:
            self.root.update()
            if until():
                return True
            time.sleep(0.01)
        return False

    def append(self, text):
        with open(self.chat, "ab") as f:
            f.write(text.encode())

    def text(self, w):
        return w.cget("text")

    def test_compact_is_the_default_and_click_toggles_and_is_remembered(self):
        tt = self.start()
        self.assertTrue(self.tr.compact)
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) != "–"))
        self.assertEqual(self.text(self.tr.c_val), "$2.00")
        self.assertTrue(self.text(self.tr.c_sub).startswith("last $"))
        self.assertTrue(self.tr.cbox.winfo_ismapped())
        self.assertFalse(self.tr.full.winfo_ismapped())
        compact_h = self.root.winfo_reqheight()

        class E:   # a click on the arrow button: press and release without moving
            x_root, y_root = 50, 50
        e = E()
        e.widget = self.tr.c_toggle
        self.tr._press(e)
        self.tr._release(e)
        self.root.update()
        self.assertFalse(self.tr.compact)
        self.assertTrue(self.tr.full.winfo_ismapped())
        self.assertGreater(self.root.winfo_reqheight(), compact_h)
        self.assertFalse(tt.load_state()["compact"])
        self.assertEqual(self.text(self.tr.sess_val), "$2.00")
        self.assertIn(self.text(self.tr.today_val), ("–", "$0.0000"))   # scan may not have run yet

    def click(self, widget, rootx=300, rooty=300, moves=()):
        """Real Tk events: press, optional drag steps, release (what the mouse does)."""
        widget.event_generate("<ButtonPress-1>", x=3, y=3, rootx=rootx, rooty=rooty)
        for dx, dy in moves:
            widget.event_generate("<B1-Motion>", x=3, y=3, rootx=rootx + dx, rooty=rooty + dy)
        last = moves[-1] if moves else (0, 0)
        widget.event_generate("<ButtonRelease-1>", x=3, y=3, rootx=rootx + last[0], rooty=rooty + last[1])
        self.root.update()

    def test_only_the_arrow_buttons_toggle_and_each_click_toggles_exactly_once(self):
        tt = self.start(state={"compact": True, "x": 100, "y": 100})
        self.assertTrue(self.pump(lambda: self.tr.c_val.winfo_ismapped()))
        for w in (self.tr.c_val, self.tr.c_sub, self.tr.cbox):       # the compact header does nothing
            self.click(w)
        self.assertTrue(self.tr.compact)
        self.click(self.tr.c_toggle)
        self.assertFalse(self.tr.compact)                    # expanded, not toggled twice
        self.assertTrue(self.tr.full.winfo_ismapped())
        self.assertFalse(tt.load_state()["compact"])
        self.pump(lambda: self.tr.h_toggle.winfo_ismapped())
        for w in (self.tr.title, self.tr.model_lbl):         # nor does the expanded header
            self.click(w)
        self.assertFalse(self.tr.compact)
        self.click(self.tr.h_toggle)
        self.assertTrue(self.tr.compact)
        self.assertTrue(self.tr.cbox.winfo_ismapped())
        self.assertTrue(tt.load_state()["compact"])

    def test_the_arrow_button_sits_next_to_the_x(self):
        self.start(state={"compact": True, "x": 100, "y": 100})
        self.pump(lambda: self.tr.cbox.winfo_ismapped())
        close = [w for w in self.tr.crow.winfo_children() if w.cget("text") == "✕"][0]
        self.assertEqual(self.text(self.tr.c_toggle), "▾")
        self.assertLess(self.tr.c_toggle.winfo_rootx(), close.winfo_rootx())
        self.assertLess(close.winfo_rootx() - self.tr.c_toggle.winfo_rootx(), 40)   # right beside it
        self.tr.toggle()
        self.pump(lambda: self.tr.h_toggle.winfo_ismapped())
        close = [w for w in self.tr.full.winfo_children()[0].winfo_children() if w.cget("text") == "✕"][0]
        self.assertEqual(self.text(self.tr.h_toggle), "▴")
        self.assertLess(self.tr.h_toggle.winfo_rootx(), close.winfo_rootx())
        self.assertLess(close.winfo_rootx() - self.tr.h_toggle.winfo_rootx(), 40)

    def test_a_real_click_on_the_body_does_nothing(self):
        self.start(state={"compact": False, "x": 100, "y": 100})
        self.pump(lambda: self.tr.sess_val.winfo_ismapped())
        self.click(self.tr.sess_val)
        self.click(self.tr.hero_val)
        self.assertFalse(self.tr.compact)

    def test_a_real_drag_moves_and_saves_without_toggling(self):
        tt = self.start(state={"compact": True, "x": 100, "y": 100})
        self.pump(lambda: self.tr.c_val.winfo_ismapped())
        self.root.update()
        x0, y0 = self.root.winfo_x(), self.root.winfo_y()
        self.click(self.tr.c_val, moves=[(5, 5), (40, 30), (80, 50)])
        self.assertTrue(self.tr.compact)
        self.assertEqual((self.root.winfo_x(), self.root.winfo_y()), (x0 + 80, y0 + 50))
        saved = tt.load_state()
        self.assertEqual((saved["x"], saved["y"]), (x0 + 80, y0 + 50))

    def test_the_window_grows_with_its_content_but_its_width_is_fixed(self):
        self.start(state={"compact": False, "x": 100, "y": 100, "width": 420})   # old key ignored
        self.pump(lambda: self.tr.sess_val.winfo_ismapped())
        self.root.update()
        self.assertEqual(self.root.winfo_width(), self.root.winfo_reqwidth())
        h = self.root.winfo_height()
        self.tr.foot.config(text="a\nb\nc\nd")   # a taller footer
        self.tr.fit()
        self.root.update()
        self.assertGreater(self.root.winfo_height(), h)
        self.assertFalse(hasattr(self.tr, "grip"))

    def test_the_x_remembers_it_was_closed_and_a_start_clears_that(self):
        tt = self.start(state={"compact": True, "x": 100, "y": 100})
        self.pump(lambda: self.tr.cbox.winfo_ismapped())
        self.assertFalse(tt.load_state()["closed"])
        self.tr.close()
        self.assertTrue(tt.load_state()["closed"])
        self.close()
        self.start(state=tt.load_state())                        # started by hand again
        self.assertFalse(tt.load_state()["closed"])

    def test_a_tiny_wobble_during_a_click_still_toggles(self):
        self.start(state={"compact": True, "x": 100, "y": 100})
        self.pump(lambda: self.tr.c_val.winfo_ismapped())
        self.click(self.tr.c_toggle, moves=[(1, 1)])
        self.assertFalse(self.tr.compact)

    def test_the_x_button_closes_the_window_and_nothing_else_does(self):
        self.start(state={"compact": True, "x": 100, "y": 100})
        self.pump(lambda: self.tr.cbox.winfo_ismapped())
        for w in (self.tr.c_val, self.tr.c_sub):
            w.event_generate("<Button-3>", x=3, y=3)             # right-click: no effect
        self.root.update()
        self.assertTrue(self.root.winfo_exists())
        close = [w for w in self.tr.crow.winfo_children() if w.cget("text") == "✕"][0]
        close.event_generate("<Button-1>", x=2, y=2)
        self.pump(lambda: False, 0.3)
        with self.assertRaises(Exception):
            self.root.winfo_x()

    def test_click_on_the_body_or_a_drag_does_not_toggle(self):
        self.start(state={"compact": False, "x": 100, "y": 100})
        self.assertFalse(self.tr.compact)

        class E:
            x_root, y_root = 50, 50
        e = E()
        e.widget = self.tr.sess_val          # not part of the header
        self.tr._press(e)
        self.tr._release(e)
        self.assertFalse(self.tr.compact is True)
        e.widget = self.tr.title             # header, but dragged
        self.tr._press(e)
        e.x_root = 90
        self.tr._motion(e)
        self.tr._release(e)
        self.root.update()
        self.assertFalse(self.tr.compact is True)

    def test_position_and_mode_are_restored_and_kept_on_screen(self):
        tt = self.start(state={"compact": False, "x": 321, "y": 123})
        self.root.update()
        self.assertFalse(self.tr.compact)
        self.assertEqual((self.root.winfo_x(), self.root.winfo_y()), (321, 123))
        self.close()
        tt = self.start(state={"compact": True, "x": -9000, "y": 99999})   # an unplugged monitor
        self.root.update()
        screen = tt.virtual_screen()
        self.assertGreaterEqual(self.root.winfo_x(), screen[0])
        self.assertLessEqual(self.root.winfo_y() + self.root.winfo_reqheight(), screen[1] + screen[3])

    def test_garbage_state_gives_defaults(self):
        self.start(state={"x": "left", "y": None, "compact": "maybe"})
        self.root.update()
        self.assertEqual((self.root.winfo_x(), self.root.winfo_y()), (40, 40))
        self.assertTrue(self.tr.compact)

    def test_limit_turns_red_and_flashes_once_on_crossing(self):
        tt = self.start(config={"limit": 10})                              # $10 in USD
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) == "$2.00"))
        self.assertEqual(self.tr._fgc[self.tr.c_val], tt.FG)
        self.append(reply("b", 3, out=500_000))                            # +$10 -> $12
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) == "$12.00"))
        self.assertEqual(self.tr._fgc[self.tr.c_val], tt.RED)
        self.assertEqual(len(self.flashes), 1)
        self.append(reply("c", 4, inp=250_000))                            # still over: no new flash
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) == "$13.00"))
        self.pump(lambda: False, 0.6)
        self.assertEqual(len(self.flashes), 1)

    def test_chat_already_over_the_limit_is_red_without_a_flash(self):
        tt = self.start(config={"limit": 1})
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) == "$2.00"))
        self.pump(lambda: False, 0.5)
        self.assertEqual(self.tr._fgc[self.tr.c_val], tt.RED)
        self.assertEqual(self.flashes, [])

    def test_unknown_model_shows_a_question_mark(self):
        self.start()
        self.chat.write_text(prompt(1) + reply("a", 2, model="claude-nova-1", inp=500_000))
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val).endswith(" ?")))
        self.assertIn("claude-nova-1", self.tr._session_note())

    def test_currency_and_footer_note(self):
        self.start(config={"currency": "CZK"})
        self.assertTrue(self.pump(lambda: self.text(self.tr.c_val) != "–"))
        self.assertTrue(self.text(self.tr.c_val).endswith(" Kč"))
        self.tr.toggle()
        self.assertTrue(self.pump(lambda: "not your bill" in self.text(self.tr.foot)))
        self.assertIn("1 USD =", self.text(self.tr.foot))

    def test_today_row_fills_from_the_background_scan(self):
        tt = self.start()
        self.tr.toggle()
        now = time.time()
        self.append(reply("t", now - 5, inp=1_000_000))                    # $4 today
        self.tr.today.scan()
        self.assertTrue(self.pump(lambda: self.text(self.tr.today_val) not in ("–", "")))
        self.assertIn("$", self.text(self.tr.today_val))

    def test_host_exit_flag_closes_the_window(self):
        self.start()
        self.tr._quit = True
        self.root.update()
        self.pump(lambda: False, 0.6)
        with self.assertRaises(Exception):   # the application has been destroyed
            self.root.winfo_x()


if __name__ == "__main__":
    unittest.main()
