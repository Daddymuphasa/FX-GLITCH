#!/bin/sh
# Run on the Ubuntu VPS after the repo, .env, and Telegram session are copied.
set -eu
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git ufw
  curl -fsSL https://get.docker.com | sh
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  apt-get update
  apt-get install -y nodejs npm
fi

if [ ! -f .env ]; then
  echo "Missing .env — copy it from your PC (TELEGRAM_API_ID / HASH)."
  exit 1
fi
if [ ! -f data/telegram.session ]; then
  echo "Missing data/telegram.session — copy the linked Telegram session from your PC."
  exit 1
fi

grep -q '^FXGLITCH_VPS=' .env 2>/dev/null || echo "FXGLITCH_VPS=1" >> .env

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw allow 8765/tcp >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
fi

docker compose up -d --build
docker compose ps

if [ -f data/wa-link/agent.mjs ]; then
  (cd data/wa-link && npm ci --omit=dev)
  install -m 0644 deploy/whatsapp-agent.service /etc/systemd/system/fxglitch-whatsapp.service
  systemctl daemon-reload
  systemctl enable --now fxglitch-whatsapp.service
  echo "WhatsApp agent installed. Link it at: http://$(hostname -I | awk '{print $1}'):8765/wa.html"
fi

echo "Waiting for /api/health ..."
i=0
while [ "$i" -lt 36 ]; do
  if curl -fsS http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
    curl -fsS http://127.0.0.1:8765/api/health
    echo
    echo "Desk is up. Point fxglitch.xyz A records to this VPS IP, then wait for HTTPS."
    echo "Until DNS: http://$(hostname -I | awk '{print $1}'):8765"
    echo "Admin: https://fxglitch.xyz/?admin=1"
    exit 0
  fi
  i=$((i + 1))
  sleep 5
done

echo "Desk did not become healthy. Logs:"
docker compose logs --tail 80 desk
exit 1
