# Starts token_tracker.py in the background (no console window) unless already running.
# -Quiet is for the SessionStart hook: no output (a hook's stdout would reach the chat),
# no process scan (the tracker itself exits when another copy holds its mutex) and
# no error when Python is missing.
param([switch]$Quiet)
$script = Join-Path (Split-Path $PSScriptRoot -Parent) 'token_tracker.py'
if (-not $Quiet) {
    $running = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like '*token_tracker.py*' -and $_.Name -match '^pythonw?\.exe$|^pyw?\.exe$' }
    if ($running) { 'already running'; exit 0 }
}
$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command pyw -ErrorAction SilentlyContinue).Source }
if (-not $py) {
    if ($Quiet) { exit 0 }
    Write-Error 'Python 3 (pythonw/pyw) not found on PATH'; exit 1
}
$arguments = "`"$script`""
if ($Quiet) { $arguments += ' --autostart' }
Start-Process $py -ArgumentList $arguments
if (-not $Quiet) { 'started' }
