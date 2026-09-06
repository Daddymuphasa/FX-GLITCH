#!/bin/sh
# Run on the Ubuntu VPS after you have copied the repo, .env, and Telegram session.
set -eu
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
  echo "Missing .env — copy it from your PC (TELEGRAM_API_ID / HASH)."
  exit 1
fi
if [ ! -f data/telegram.session ]; then
  echo "Missing data/telegram.session — copy the linked Telegram session from your PC."
  exit 1
fi

docker compose up -d --build
docker compose ps
echo "Desk is up. Point fxglitch.xyz A records to this VPS IP, then wait for HTTPS."
echo "Admin: https://fxglitch.xyz/?admin=1"
