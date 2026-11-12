# token-tracker

A small always-on-top overlay for **Claude Desktop (Windows)** that shows how many tokens, and roughly how much money, your last request, the whole chat and today used.

- **Compact line** by default: the whole chat's cost and the last request's cost, e.g. `116,33 Kč · last 15,99 Kč`. The arrow button next to ✕ expands or collapses it.
- **Expanded panel:** cost of the last request (incl. the whole reply and tool calls), cost and total tokens of the whole chat (summed across `/clear` and compaction), **Today (all chats)**, input / output / cache read / cache write tokens with their cost, and a context window bar.
- **Limit:** set a per-chat limit and the chat's total turns red, with one flash when it crosses the limit.
- Follows the chat you have focused in Claude Desktop (checked every 250 ms; chats you have viewed switch instantly).
- Shows only while Claude Desktop is in front; drag to move (position and mode are remembered), ✕ to close.
- A `?` after an amount means a model has no price in the table, so a default was used (hover for details).

It reads the local Claude Code logs in `~/.claude/projects`. Nothing is sent anywhere (non-USD modes only download the public ECB exchange rate from frankfurter.dev). Amounts are **estimates at API list prices, not your bill**.

## Requirements

- Windows 10/11
- Python 3 with Tkinter (the standard python.org installer includes it)
- Claude Desktop with the Code tab, or Claude Code

## Install as a Claude Code plugin

```
/plugin marketplace add JustMeGoro/token-tracker
/plugin install token-tracker@token-tracker
```

The overlay then **starts by itself** whenever a Claude session starts (a `SessionStart` hook) and then keeps waiting, hidden, so it comes back by itself the next time Claude Desktop starts. If you closed it with ✕, it stays off (also after a restart) until you run `/token-tracker` again. You can also run `/token-tracker` in any chat to start it by hand. Either way a second copy never starts.

To stop it starting automatically, set `"autostart": false` in the config file (below); `/token-tracker` still works.

## Run without the plugin

```
git clone https://github.com/JustMeGoro/token-tracker
pythonw token-tracker\token_tracker.py
```

## Settings

Edit `~/.claude/token-tracker.json` (create it if it is missing). The app only reads it, it never writes to it; a missing or broken file simply means defaults. Every key is optional:

```json
{
  "currency": "CZK",
  "limit": 100,
  "prices": [{ "match": "opus-6", "input": 5, "output": 25 }],
  "default_price": [5, 25],
  "host_exe": "claude.exe",
  "autostart": true,
  "auto_exit": false
}
```

| Key | Default | Meaning |
|---|---|---|
| `currency` | `USD` | Display currency, converted with the daily ECB rate: `USD` `EUR` `GBP` `CZK` `PLN` `CHF` `JPY` `CAD` `AUD` `CNY` `INR` `SEK` `NOK` `DKK` `KRW` `BRL` `MXN` |
| `limit` | `0` (off) | Limit for one chat, **in the display currency**. The total turns red when a chat is over it, and flashes once when a chat you are watching crosses it. |
| `prices` | – | Extra prices in USD per 1M tokens. `match` is a part of the model id; these win over the built-in table (first match wins). Cache read is priced at 0.1x input, cache write at 1.25x (5 min) or 2x (1 h). |
| `default_price` | `[5, 25]` | Input / output price used for a model that matches nothing (shown with `?`). |
| `host_exe` | `claude.exe` | Window that must be in front for the overlay to show; `any` = always visible. |
| `autostart` | `true` | Whether the hook may start the overlay. |
| `auto_exit` | `false` | `true` = quit about a minute after the host app is gone, instead of waiting hidden for its next start. |

Environment variables override the file: `TOKEN_TRACKER_CURRENCY`, `TOKEN_TRACKER_HOST_EXE`, `TOKEN_TRACKER_LIMIT`. To make a currency permanent that way: `setx TOKEN_TRACKER_CURRENCY EUR`, then restart Claude Desktop. To add another currency, add a line to the `CURRENCIES` table in `token_tracker.py` (any code supported by [frankfurter.dev](https://frankfurter.dev)).

What the app itself remembers (window position, compact or expanded, and whether you closed it with ✕) lives in `~/.claude/token-tracker-state.json`. Delete it to reset the window.

## How it works

- **Chat-following:** Claude Desktop stores one metadata file per chat (`claude-code-sessions/**/local_*.json`) with `lastFocusedAt`, the log's session id and the ids of the logs from before `/clear` or compaction (`priorCliSessionIds`). The tracker shows the chat with the newest `lastFocusedAt` and falls back to the most recently written log. The Store-package, classic `%APPDATA%` and newer `%LOCALAPPDATA%\Claude-Data` locations are all checked (re-discovered every 10 s). Chats you have already viewed are kept in memory; new ones load in a background thread.
- **Today:** a background thread sums every reply since local midnight over all logs in `~/.claude/projects` (CLI sessions and subagents included), reading only what was appended since the last scan (every 30 s). A reply that appears in several logs (rewind, fork) is counted once.

## Tests

```
py -m unittest discover -s tests -v
```

The tests use throw-away config, state and log folders; the window tests need a display.

## License

MIT
