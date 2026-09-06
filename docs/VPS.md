# Run FX-GLITCH 24/7 on a VPS

Vercel cannot keep the Telegram login. A small Ubuntu VPS runs the desk and the group watch all day.

**Hostinger:** VPS → KVM 1 / Ubuntu 24.04. You need the **public IP** and SSH (root + password or key).

## 1. Point the domain at the VPS

In Hostinger DNS for `fxglitch.xyz`, replace the Vercel A records:

| Type | Name | Points to |
|---|---|---|
| A | `@` | **your VPS IP** |
| A | `www` | **your VPS IP** |

Delete `76.76.21.21` / `216.198.79.1` if they are still there. Keep TTL 60.

## 2. Copy this project from your PC

In PowerShell, from the FX-GLITCH folder (use your VPS IP):

```powershell
.\deploy\upload-from-windows.ps1 -HostName YOUR.VPS.IP -User root
```

That sends the code, `.env`, and `data/telegram.session` (already linked as Daddymuphasa2).

## 3. Start it on the server

```bash
ssh root@YOUR.VPS.IP
cd /opt/fx-glitch
sh deploy/setup-vps.sh
```

Caddy gets a Let's Encrypt certificate for fxglitch.xyz. Telegram keeps watching the Cosmas group.

Admin scan: `https://fxglitch.xyz/?admin=1`

## 4. Check it is up

```bash
docker compose -f /opt/fx-glitch/docker-compose.yml ps
docker compose -f /opt/fx-glitch/docker-compose.yml logs -f desk
```

Reboot of the VPS is fine: `restart: unless-stopped` brings the desk back.

Do **not** commit `.env` or `telegram.session` to GitHub.
