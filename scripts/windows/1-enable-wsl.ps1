# Step 1 of 2 (run as Administrator): turn on WSL 2 without installing a Linux distribution.
# Right-click PowerShell -> "Run as administrator", then:  powershell -ExecutionPolicy Bypass -File D:\Docker\1-enable-wsl.ps1
# Windows asks to restart afterwards. After the restart, run 2-install-docker.ps1 (also as Administrator).
$ErrorActionPreference = "Stop"
wsl --install --no-distribution
Write-Host ""
Write-Host "WSL is enabled. Restart Windows now, then run D:\Docker\2-install-docker.ps1 as Administrator." -ForegroundColor Green
