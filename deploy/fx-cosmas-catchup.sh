#!/bin/bash
# Cosmas catch-up one-shot for systemd. Alerts only on fresh ACTION FILLED.
set -euo pipefail
DESK="${FX_DESK_CONTAINER:-fx-glitch-desk-1}"
LOG_DIR=/var/log/fx-glitch
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cosmas-catchup.log"

docker cp /opt/fx-glitch/tools/cosmas_live_catchup.py "$DESK:/tmp/cosmas_live_catchup.py"
# ensure module patches present
for f in venues/bitunix.py agent/auto_trade.py agent/plans.py agent/telegram_user.py; do
  if [ -f "/opt/fx-glitch/fxglitch/$f" ]; then
    docker cp "/opt/fx-glitch/fxglitch/$f" "$DESK:/app/fxglitch/$f" 2>/dev/null || true
  fi
done

OUT=$(docker exec -w /app -e PYTHONPATH=/app -e COSMAS_CATCHUP_HOURS="${COSMAS_CATCHUP_HOURS:-18}" \
  "$DESK" python /tmp/cosmas_live_catchup.py 2>&1) || {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERR $OUT" | tee -a "$LOG"
  # soft heal once
  docker compose -f /opt/fx-glitch/docker-compose.yml up -d desk || true
  sleep 5
  for f in venues/bitunix.py agent/auto_trade.py agent/plans.py agent/telegram_user.py; do
    docker cp "/opt/fx-glitch/fxglitch/$f" "$DESK:/app/fxglitch/$f" 2>/dev/null || true
  done
  systemctl restart fx-trail || true
  docker cp /opt/fx-glitch/tools/cosmas_live_catchup.py "$DESK:/tmp/cosmas_live_catchup.py"
  OUT=$(docker exec -w /app -e PYTHONPATH=/app -e COSMAS_CATCHUP_HOURS="${COSMAS_CATCHUP_HOURS:-18}" \
    "$DESK" python /tmp/cosmas_live_catchup.py 2>&1) || {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERR2 $OUT" | tee -a "$LOG"
    exit 1
  }
}

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $OUT" >> "$LOG"

if echo "$OUT" | grep -q '^ACTION FILLED'; then
  # Queue WhatsApp alert (best-effort); Grok bot also polls catchup log / API
  TEXT=$(echo "$OUT" | head -c 500)
  curl -sf -X POST http://127.0.0.1:8765/api/whatsapp \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys; print(json.dumps({'action':'alert','text':sys.argv[1]}))" "$TEXT")" \
    >/dev/null 2>&1 || true
  # Notify Grok operator bot if desk exposes hook
  curl -sf -X POST http://127.0.0.1:8765/api/desk/alert \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys; print(json.dumps({'text':sys.argv[1]}))" "$TEXT")" \
    >/dev/null 2>&1 || true
fi

echo "$OUT"
