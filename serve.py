"""Judge dashboard: python serve.py → http://127.0.0.1:8765"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from fxglitch.agent.http_api import Handler  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="FX-GLITCH Agent OS dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    public = os.path.join(ROOT, "public")
    Handler.web_root = public if os.path.isdir(public) else os.path.join(ROOT, "web")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FX-GLITCH Agent OS  http://{args.host}:{args.port}")
    print("dry-run by default. MCP: python -m fxglitch.agent.mcp_server")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
