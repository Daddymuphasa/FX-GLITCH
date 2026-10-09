# FX-GLITCH ops status

Updated: 2026-10-09 (Africa/Lagos)

## Live trader of record

Hostinger VPS `187.124.113.6` (`srv1979749`) is the live Cosmas → Bitunix auto-trade host.

- Desk: Docker `fx-glitch-desk-1` on port 8765 (healthy)
- Cosmas Telegram watch: active (user Daddymuphasa2, chat `-1002004401199`)
- Auto slots: 2–5 (merrijeh, ekeson, kazi, KarlaGod)
- Timers: catch-up every 5m, babysit hourly, learn daily, trail service running
- Public DNS: `fxglitch.xyz` and `www` A → `187.124.113.6` (set in Vercel Domains, TTL 60)
- Do **not** run a second live auto-trader elsewhere with the same Bitunix keys (duplicate fills)

## Agent computer

Grok Bot box holds a code unpack at `/workspace/fx-glitch-ops` (no `.env`, no Telegram session) for study/monitor. It is **not** the live sender.

## Knowledge

- `docs/cosmas-strategy.json` — portable Cosmas strategy handoff
- `docs/cosmas-admin-playbook.md` / `docs/cosmas-patterns.json` — thin-data study notes

## Note

Hostinger VPS subscription was shown renewing soon in hPanel. Renew or migrate before expiry so Cosmas auto-trade does not stop.
