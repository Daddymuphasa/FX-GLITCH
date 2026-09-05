"""Stdio MCP server for Binance Agent OS / any MCP client.

Track B of the Binance Agent OS Mini Hackathon is "Connect your MCPs and
trade." This process is the FX-GLITCH MCP. Pair it with Binance's hosted
MCP at https://agent.binance.com/mcp/agentic for market data and account
scopes; this server is the policy + positioning layer those tools do not
have.

Protocol: JSON-RPC 2.0 over newline-delimited stdin/stdout (MCP stdio).
No third-party MCP SDK — stdlib only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..live.guards import Limits
from ..live.portfolio import ExposureLimits
from ..signal_copy import parse_signal
from ..venues.binance import Binance
from .policy import ProposedTrade, evaluate
from .positioning import fetch_briefing
from .session import from_briefing, from_signal, judge_demo, paper_balance, run_cycle

PROTOCOL = "2024-11-05"
SERVER_NAME = "fx-glitch"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "get_positioning",
        "description": (
            "Public Binance USD-M positioning briefing: funding, open interest "
            "quadrant, long/short ratio. Not technical analysis. No API key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "e.g. BTCUSDT", "default": "BTCUSDT"},
            },
        },
    },
    {
        "name": "propose_from_positioning",
        "description": (
            "Turn a positioning briefing into a trade proposal. Default is "
            "stand_aside — this layer does not invent chart entries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
            },
        },
    },
    {
        "name": "parse_external_signal",
        "description": "Parse a Telegram-style futures signal into a candidate. Does not trade.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "check_policy",
        "description": (
            "Run FX-GLITCH guards on a proposed futures trade: stop required, "
            "leverage cap, daily loss, margin reserve, BTC-equivalent exposure, "
            "crowded-book veto. Refuses; never silently resizes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "action": {"type": "string", "enum": ["stand_aside", "open", "close", "reduce"]},
                "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
                "leverage": {"type": "integer", "default": 5},
                "risk_pct": {"type": "number", "default": 1.0},
                "stop_pct": {"type": "number", "default": 2.0},
                "entry": {"type": "number"},
                "stop": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["symbol", "action"],
        },
    },
    {
        "name": "run_cycle",
        "description": (
            "Full cycle: briefing → proposal → policy. Dry-run unless live=true "
            "AND BINANCE_API_KEY/SECRET are set. Default live=false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "message": {"type": "string", "description": "optional external signal text"},
                "live": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "judge_demo",
        "description": "Offline 60-second demo of four policy cases. No network, no keys.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _ok(value: Any) -> dict:
    text = json.dumps(value, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _call(name: str, arguments: dict) -> dict:
    if name == "get_positioning":
        return _ok(fetch_briefing(arguments.get("symbol") or "BTCUSDT").to_dict())
    if name == "propose_from_positioning":
        briefing = fetch_briefing(arguments.get("symbol") or "BTCUSDT")
        return _ok({"briefing": briefing.to_dict(), "proposal": from_briefing(briefing).to_dict()})
    if name == "parse_external_signal":
        parsed = parse_signal(arguments["message"])
        proposal = from_signal(arguments["message"])
        return _ok({"parsed": {
            "symbol": parsed.symbol, "direction": None if parsed.direction is None else parsed.direction.value,
            "can_open": parsed.can_open, "warnings": list(parsed.warnings),
        }, "proposal": proposal.to_dict()})
    if name == "check_policy":
        proposal = ProposedTrade(
            symbol=str(arguments["symbol"]).upper(),
            action=arguments["action"],
            direction=arguments.get("direction"),
            leverage=int(arguments.get("leverage") or 5),
            risk_pct=float(arguments.get("risk_pct") or 1.0),
            stop_pct=float(arguments.get("stop_pct") or 2.0),
            entry=arguments.get("entry"),
            stop=arguments.get("stop"),
            reason=arguments.get("reason") or "mcp check_policy",
            source="mcp",
        )
        briefing = None
        try:
            briefing = fetch_briefing(proposal.symbol)
        except RuntimeError:
            pass
        decision = evaluate(
            proposal,
            balance=paper_balance(1000.0),
            positions=[],
            briefing=briefing,
            limits=Limits(),
            exposure_limits=ExposureLimits(),
        )
        return _ok(decision.to_dict())
    if name == "run_cycle":
        live = bool(arguments.get("live"))
        venue = Binance() if live else None
        result = run_cycle(
            arguments.get("symbol") or "BTCUSDT",
            message=arguments.get("message"),
            live=live,
            venue=venue,
        )
        return _ok(result.to_dict())
    if name == "judge_demo":
        return _ok(judge_demo())
    raise ValueError(f"unknown tool {name}")


def handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "FX-GLITCH is a policy MCP for Binance USD-M futures. "
                    "Pair with https://agent.binance.com/mcp/agentic. "
                    "Default is dry-run. Positioning can veto; it cannot invent a chart entry."
                ),
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = _call(params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"error: {exc}"}],
                    "isError": True,
                },
            }
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
