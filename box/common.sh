# shared helpers for box loops
D=/workspace/fx-glitch-ops
LOGD=$D/data/logs; mkdir -p "$LOGD"
PY=$D/.venv/bin/python
export PYTHONPATH=$D TELEGRAM_PORT=${TELEGRAM_PORT:-5222}
cap() { f=$1; [ -f "$f" ] && [ $(stat -c%s "$f") -gt 10485760 ] && tail -c 5000000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"; true; }
