# Copy secrets + session to the VPS. Run from the FX-GLITCH folder:
#   .\deploy\upload-from-windows.ps1 -HostName 1.2.3.4 -User root
param(
  [Parameter(Mandatory = $true)][string]$HostName,
  [string]$User = "root"
)

$root = Split-Path -Parent $PSScriptRoot
$dest = "${User}@${HostName}:/opt/fx-glitch"

Write-Host "Uploading repo to $dest ..."
ssh "${User}@${HostName}" "mkdir -p /opt/fx-glitch/data"
scp -r "$root\public" "$root\fxglitch" "$root\api" "$root\serve.py" "$root\requirements.txt" `
  "$root\Dockerfile" "$root\docker-compose.yml" "$root\Caddyfile" "$root\.dockerignore" `
  "$root\deploy" "${dest}/"

if (Test-Path "$root\.env") {
  scp "$root\.env" "${dest}/.env"
} else {
  Write-Host "WARNING: no .env on this PC"
}

Get-ChildItem "$root\data" -Filter "telegram*" | ForEach-Object {
  scp $_.FullName "${dest}/data/$($_.Name)"
}

Write-Host "Now SSH in and run:  cd /opt/fx-glitch; sh deploy/setup-vps.sh"
