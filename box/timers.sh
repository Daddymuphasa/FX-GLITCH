#!/usr/bin/env bash
# Cosmas catch-up every 5 min (200 msgs / 12h lookback / signals <=8h old).
. "$(dirname "$0")/common.sh"; cd "$D"
while true; do
  OUT=$(set -a; . ./.env; set +a; timeout 240 $PY tools/cosmas_live_catchup.py 2>&1)
  echo "$(date -u +%FT%TZ) $OUT" >>"$LOGD/cosmas-catchup.log"; cap "$LOGD/cosmas-catchup.log"
  sleep 300
done
