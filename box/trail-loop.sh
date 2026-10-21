#!/usr/bin/env bash
# trail_200 every 3 min; it only ever tightens stops.
. "$(dirname "$0")/common.sh"; cd "$D"
while true; do (set -a; . ./.env; set +a; $PY tools/trail_200.py) >>"$LOGD/trail.log" 2>&1; cap "$LOGD/trail.log"; sleep 180; done
