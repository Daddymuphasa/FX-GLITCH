#!/usr/bin/env bash
# WhatsApp Baileys agent, auto-restart.
. "$(dirname "$0")/common.sh"; cd "$D/data/wa-link"
while true; do (set -a; . "$D/.env"; set +a; node agent.mjs) >>"$LOGD/wa.log" 2>&1; cap "$LOGD/wa.log"; sleep 15; done
