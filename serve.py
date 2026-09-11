"""Judge dashboard: python serve.py → http://127.0.0.1:8765"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

NEEDED = (
    ("numpy", "numpy"),
    ("telethon", "telethon"),
    ("qrcode", "qrcode"),
    ("PIL", "pillow"),
    ("webauthn", "webauthn"),
)


def _load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _ensure_deps() -> None:
    missing = []
    for mod, pip_name in NEEDED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    req = os.path.join(ROOT, "requirements.txt")
    print("Installing desk packages:", ", ".join(missing))
    cmd = [sys.executable, "-m", "pip", "install"]
    if os.path.isfile(req):
        cmd.extend(["-r", req])
    else:
        cmd.extend(missing)
    subprocess.check_call(cmd)


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def main() -> None:
    p = argparse.ArgumentParser(description="FX-GLITCH Agent OS dashboard")
    p.add_argument("--host", default=os.environ.get("FXGLITCH_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT") or os.environ.get("FXGLITCH_PORT") or 8765))
    p.add_argument("--open", action="store_true", help="open the desk in a browser")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = p.parse_args()
    _ensure_deps()
    _load_env(os.path.join(ROOT, ".env"))
    vps = bool(os.environ.get("FXGLITCH_VPS"))
    want_open = False if (vps or args.no_open or args.host in ("0.0.0.0", "::")) else True
    if args.open:
        want_open = True
    public_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{public_host}:{args.port}"
    if not vps:
        os.environ.setdefault("FXGLITCH_INBOX_WEBHOOK", "https://fxglitch.xyz/api/telegram")

    from http.server import ThreadingHTTPServer

    from fxglitch.agent.http_api import Handler
    from fxglitch.agent.telegram_user import bridge

    if _port_open("127.0.0.1", args.port) or (args.host not in ("0.0.0.0", "::") and _port_open(args.host, args.port)):
        print(f"FX-GLITCH already running at {url}")
        if want_open:
            webbrowser.open(url)
        return

    public = os.path.join(ROOT, "public")
    Handler.web_root = public if os.path.isdir(public) else os.path.join(ROOT, "web")
    bridge.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FX-GLITCH Agent OS  {url}")
    print("Open http://localhost:%s for passkeys (not 127.0.0.1)." % args.port)
    print("Telegram QR login and group watch run in this process.")
    print("dry-run by default. MCP: python -m fxglitch.agent.mcp_server")
    if want_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
