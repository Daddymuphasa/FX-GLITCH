# Copy the desk + Telegram session to a VPS and start it.
#   .\deploy\upload-from-windows.ps1 -HostName YOUR.VPS.IP
# Uses %USERPROFILE%\.ssh\fxglitch_vps (created on this PC).
param(
  [Parameter(Mandatory = $true)][string]$HostName,
  [string]$User = "root",
  [string]$IdentityFile = (Join-Path $env:USERPROFILE ".ssh\fxglitch_vps"),
  [switch]$NoSetup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = "${User}@${HostName}:/opt/fx-glitch"
$ssh = @("-i", $IdentityFile, "-o", "StrictHostKeyChecking=accept-new", "-o", "IdentitiesOnly=yes")

if (-not (Test-Path $IdentityFile)) {
  throw "Missing SSH key $IdentityFile — generate fxglitch_vps first."
}

Write-Host "Uploading FX-GLITCH to $dest ..."
& ssh @ssh "${User}@${HostName}" "mkdir -p /opt/fx-glitch/data /opt/fx-glitch/public /opt/fx-glitch/deploy"

$dirs = @("public", "fxglitch", "api", "deploy", "data\wa-link")
foreach ($d in $dirs) {
  $remoteDir = if ($d -eq "data\wa-link") { "${dest}/data/" } else { "${dest}/" }
  & scp @ssh -r (Join-Path $root $d) $remoteDir
}

$files = @(
  "serve.py", "requirements.txt", "Dockerfile", "docker-compose.yml",
  "Caddyfile", ".dockerignore"
)
foreach ($f in $files) {
  $p = Join-Path $root $f
  if (Test-Path $p) { & scp @ssh $p "${dest}/$f" }
}

# The WhatsApp linked-device agent runs outside Docker as a systemd service.
# Upload its source and lockfile, but never upload its local auth directory.

if (Test-Path (Join-Path $root ".env")) {
  & scp @ssh (Join-Path $root ".env") "${dest}/.env"
} else {
  Write-Host "WARNING: no .env on this PC"
}

Get-ChildItem (Join-Path $root "data") -File | Where-Object {
  $_.Name -like "telegram*" -or $_.Name -eq "inbox.json"
} | ForEach-Object {
  & scp @ssh $_.FullName "${dest}/data/$($_.Name)"
}

Write-Host "Upload done."
if ($NoSetup) {
  Write-Host "SSH in and run:  cd /opt/fx-glitch; sh deploy/setup-vps.sh"
  return
}

Write-Host "Starting Docker desk on the VPS (first run installs Docker) ..."
& ssh @ssh "${User}@${HostName}" "cd /opt/fx-glitch && sh deploy/setup-vps.sh"
Write-Host "Desk should be up. Check:  http://${HostName}:8765/api/health"
Write-Host "Then point fxglitch.xyz A records at $HostName"
