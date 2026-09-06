"""Judge dashboard: python serve.py → http://127.0.0.1:8765"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


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

from fxglitch.agent.http_api import Handler  # noqa: E402
from fxglitch.agent.telegram_user import bridge  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="FX-GLITCH Agent OS dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    _load_env(os.path.join(ROOT, ".env"))
    public = os.path.join(ROOT, "public")
    Handler.web_root = public if os.path.isdir(public) else os.path.join(ROOT, "web")
    bridge.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FX-GLITCH Agent OS  http://{args.host}:{args.port}")
    print("Scan the QR on the desk to link a Telegram account already in the group.")
    print("dry-run by default. MCP: python -m fxglitch.agent.mcp_server")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
