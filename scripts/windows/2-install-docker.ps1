# Step 2 of 2 (run as Administrator, after the restart): install Docker Desktop with everything on drive D:.
#   powershell -ExecutionPolicy Bypass -File D:\Docker\2-install-docker.ps1
# - program files  -> D:\Docker\DockerDesktop
# - WSL disk image (images, containers, volumes) -> D:\Docker\wsl
# - WSL memory capped at 5 GB (this laptop has 7.3 GB), swap file on D:
$ErrorActionPreference = "Stop"
$installer = "D:\Docker\installer\Docker Desktop Installer.exe"
if (-not (Test-Path $installer)) { throw "Installer not found: $installer" }

$sig = Get-AuthenticodeSignature $installer
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Docker Inc") {
    throw "Installer signature is not a valid Docker Inc signature: $($sig.Status) $($sig.SignerCertificate.Subject)"
}

# WSL memory limit + swap on D: (applies to Docker's WSL VM)
$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
@"
[wsl2]
memory=5GB
swap=6GB
swapFile=D:\\Docker\\wsl\\swap.vhdx
"@ | Set-Content -Path $wslconfig -Encoding ascii
wsl --shutdown

Start-Process -Wait -FilePath $installer -ArgumentList @(
    "install", "--quiet", "--accept-license",
    "--installation-dir=D:\Docker\DockerDesktop",
    "--wsl-default-data-root=D:\Docker\wsl",
    "--backend=wsl-2"
)

# let the current user run docker without admin
$group = "docker-users"
if (Get-LocalGroup -Name $group -ErrorAction SilentlyContinue) {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    if (-not (Get-LocalGroupMember -Group $group -Member $user -ErrorAction SilentlyContinue)) {
        Add-LocalGroupMember -Group $group -Member $user
    }
}
Write-Host ""
Write-Host "Docker Desktop installed on D:. Sign out and back in (docker-users group), start Docker Desktop," -ForegroundColor Green
Write-Host "then check: docker run --rm hello-world" -ForegroundColor Green
