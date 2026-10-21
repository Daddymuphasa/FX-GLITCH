#!/usr/bin/env bash
# Start every FX-GLITCH loop on the box. Idempotent; rebuilds venv/node/app link if lost.
set -u
. "$(dirname "$0")/common.sh"
cd "$D"
[ -L /app ] || sudo ln -sfn "$D" /app 2>/dev/null || ln -sfn "$D" /app
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip -q install -r requirements.txt; }
[ -d data/wa-link/node_modules ] || (cd data/wa-link && npm ci --silent || npm install --silent)
[ -f .env ] || { echo "NO .env - restore secrets first"; exit 1; }
run() { name=$1; shift; pgrep -f "$name" >/dev/null && { echo "up  $name"; return; }
  nohup setsid "$@" >>"$LOGD/$(basename "$name" .sh).log" 2>&1 & echo "start $name"; }
run "serve.py" bash -c "set -a; . $D/.env; set +a; exec $PY $D/serve.py"
run "box/trail-loop.sh" bash "$D/box/trail-loop.sh"
run "box/timers.sh" bash "$D/box/timers.sh"
run "box/babysit-loop.sh" bash "$D/box/babysit-loop.sh"
run "box/wa.sh" bash "$D/box/wa.sh"
