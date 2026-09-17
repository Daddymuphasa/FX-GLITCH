# Run FX-GLITCH 24/7 on a VPS

Vercel cannot keep the Telegram login. A small Ubuntu VPS runs the desk and the Cosmas group watch all day.

**Hostinger:** VPS → **KVM 1** → **Ubuntu 24.04**. You need the **public IP**.

Do **not** change DNS until the VPS answers `http://YOUR.VPS.IP:8765/api/health`. fxglitch.xyz can stay on Vercel until then.

## 1. Buy / open the VPS

1. https://www.hostinger.com/vps/ubuntu-hosting — KVM 1 is enough.
2. OS: **Ubuntu 24.04**. No control panel needed.
3. In hPanel → VPS → SSH keys, paste this PC's public key:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJXP2npLVNAEhdM6ED2DjWgPwhsvVXIC5nRfH1aXqgWP fxglitch-desk
```

4. Copy the **public IP**. Send it so this repo can be uploaded.

## 2. Copy this project from your PC

PowerShell, from the FX-GLITCH folder:

```powershell
.\deploy\upload-from-windows.ps1 -HostName YOUR.VPS.IP
```

That sends the code, `.env`, and `data/telegram.session` (already linked as Daddymuphasa2), installs Docker and the WhatsApp agent, and starts the desk.

Until DNS: `http://YOUR.VPS.IP:8765`

Open `http://YOUR.VPS.IP:8765/wa.html`, scan the QR with WhatsApp → Settings → Linked devices, then check the agent with:

```bash
systemctl status fxglitch-whatsapp
```

## 3. Point the domain at the VPS

In Hostinger DNS for `fxglitch.xyz`, replace the Vercel A records:

| Type | Name | Points to |
|---|---|---|
| A | `@` | **your VPS IP** |
| A | `www` | **your VPS IP** |

Delete `76.76.21.21` / `216.198.79.1` if they are still there. Keep TTL 60.

Caddy then gets a Let's Encrypt certificate. Admin: `https://fxglitch.xyz/?admin=1`

## 4. Check it is up

```bash
ssh -i %USERPROFILE%\.ssh\fxglitch_vps root@YOUR.VPS.IP
docker compose -f /opt/fx-glitch/docker-compose.yml ps
docker compose -f /opt/fx-glitch/docker-compose.yml logs -f desk
```

Reboot of the VPS is fine: `restart: unless-stopped` brings the desk back.

Do **not** commit `.env` or `telegram.session` to GitHub.
