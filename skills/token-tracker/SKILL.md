---
name: token-tracker
description: >
  Start the token tracker window, a small always-on-top overlay showing token
  usage and estimated cost of the focused Claude Desktop chat. Trigger: /token-tracker.
disable-model-invocation: true
---

# Token tracker

Make exactly ONE PowerShell call and nothing else (no prior checks, no sleep, no verification):

```powershell
& "${CLAUDE_PLUGIN_ROOT}/scripts/launch.ps1"
```

Then reply with one sentence: started, or already running. The window closes with a double-click or right-click.
