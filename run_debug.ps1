Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force logs | Out-Null
& ".\venv\Scripts\Activate.ps1"
python -u -m jarvis.main *>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "logs\run_debug.log")
