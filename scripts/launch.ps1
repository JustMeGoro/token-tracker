# Starts token_tracker.py in the background (no console window) unless already running.
$script = Join-Path (Split-Path $PSScriptRoot -Parent) 'token_tracker.py'
$running = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*token_tracker.py*' -and $_.Name -match '^pythonw?\.exe$|^pyw?\.exe$' }
if ($running) { 'already running'; exit 0 }
$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command pyw -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Error 'Python 3 (pythonw/pyw) not found on PATH'; exit 1 }
Start-Process $py -ArgumentList "`"$script`""
'started'
