#!/usr/bin/env bash
# protect babysit at :05 hourly, learner at 03:15 (box-local WAT).
. "$(dirname "$0")/common.sh"; cd "$D"
last_b=""; last_l=""
while true; do
  h=$(date +%H); m=$(date +%M); d=$(date +%F)
  if [ "$m" -ge 05 ] && [ "$last_b" != "$d$h" ]; then last_b="$d$h"
    (set -a; . ./.env; set +a; timeout 600 $PY tools/protect_nil_mana.py) >>"$LOGD/babysit.log" 2>&1; cap "$LOGD/babysit.log"; fi
  if [ "$h" = 03 ] && [ "$m" -ge 15 ] && [ "$last_l" != "$d" ]; then last_l="$d"
    (set -a; . ./.env; set +a; timeout 1800 bash tools/cosmas_learn.sh) >>"$LOGD/learn.log" 2>&1; fi
  sleep 60
done
