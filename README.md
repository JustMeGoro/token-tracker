# token-tracker

A small always-on-top overlay for **Claude Desktop (Windows)** that shows how many tokens, and roughly how much money, your last request and the whole chat used.

- Cost of the last request, including the whole reply and tool calls
- Cost of the whole chat, summed across `/clear` and compaction (earlier logs listed in `priorCliSessionIds` are included)
- Input / output / cache read / cache write tokens, each with its cost
- Context window usage bar
- Follows the chat you have focused in Claude Desktop (switches within ~2 s)
- Shows only while Claude Desktop is in front; drag to move, double-click or right-click to close

It reads the local Claude Code logs in `~/.claude/projects`. Nothing is sent anywhere (non-USD modes only download the public ECB exchange rate from frankfurter.dev).

## Requirements

- Windows 10/11
- Python 3 with Tkinter (the standard python.org installer includes it)
- Claude Desktop with the Code tab, or Claude Code

## Install as a Claude Code plugin

```
/plugin marketplace add JustMeGoro/token-tracker
/plugin install token-tracker@token-tracker
```

Then run `/token-tracker` in any chat. If it is already running it won't start a second copy.

## Run without the plugin

```
git clone https://github.com/JustMeGoro/token-tracker
pythonw token-tracker\token_tracker.py
```

## Settings

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `TOKEN_TRACKER_CURRENCY` | `USD` | Display currency, converted with the daily ECB rate: `USD` `EUR` `GBP` `CZK` `PLN` `CHF` `JPY` `CAD` `AUD` `CNY` `INR` `SEK` `NOK` `DKK` `KRW` `BRL` `MXN` |
| `TOKEN_TRACKER_HOST_EXE` | `claude.exe` | Window that must be in front for the overlay to show; `any` = always visible |

Prices are the `PRICES` table at the top of `token_tracker.py`. They are list-price estimates, so edit them if pricing changes. Unknown models use a pessimistic default.

To make a currency permanent: `setx TOKEN_TRACKER_CURRENCY EUR`, then restart Claude Desktop. To add another currency, add a line to the `CURRENCIES` table in `token_tracker.py` (any code supported by [frankfurter.dev](https://frankfurter.dev)).

## How chat-following works

Claude Desktop stores one metadata file per chat (`claude-code-sessions/**/local_*.json`) containing `lastFocusedAt` and the log's session id. The tracker shows the log of the chat with the newest `lastFocusedAt`, and falls back to the most recently written log. Both the Store-package and classic `%APPDATA%` locations are checked.

## License

MIT
