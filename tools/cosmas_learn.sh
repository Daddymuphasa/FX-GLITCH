#!/bin/bash
set -e
for f in cosmas_study.py cosmas_playbook_refresh.py cosmas_strategy_knowledge.py; do
  if [ -f /opt/fx-glitch/tools/$f ]; then
    docker cp /opt/fx-glitch/tools/$f fx-glitch-desk-1:/app/tools/$f || true
  fi
done
docker exec -w /app -e PYTHONPATH=/app fx-glitch-desk-1 python /app/tools/cosmas_study.py >/opt/fx-glitch/data/cosmas_study_last.log 2>&1
docker exec -w /app -e PYTHONPATH=/app fx-glitch-desk-1 python /app/tools/cosmas_playbook_refresh.py >>/opt/fx-glitch/data/cosmas_study_last.log 2>&1
docker exec -w /app -e PYTHONPATH=/app fx-glitch-desk-1 python /app/tools/cosmas_strategy_knowledge.py >>/opt/fx-glitch/data/cosmas_study_last.log 2>&1
# host-visible copies (data is bind-mounted)
echo DONE $(date -u +%Y-%m-%dT%H:%M:%SZ) >>/opt/fx-glitch/data/cosmas_study_last.log