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
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the desk in a browser")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = p.parse_args()
    want_open = args.open or not args.no_open
    url = f"http://{args.host}:{args.port}"

    _ensure_deps()
    _load_env(os.path.join(ROOT, ".env"))

    from http.server import ThreadingHTTPServer

    from fxglitch.agent.http_api import Handler
    from fxglitch.agent.telegram_user import bridge

    if _port_open(args.host, args.port):
        print(f"FX-GLITCH already running at {url}")
        if want_open:
            webbrowser.open(url)
        return

    public = os.path.join(ROOT, "public")
    Handler.web_root = public if os.path.isdir(public) else os.path.join(ROOT, "web")
    bridge.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FX-GLITCH Agent OS  {url}")
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
