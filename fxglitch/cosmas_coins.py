"""Bare coin-name recognition for Cosmas ("Aster" instead of "ASTER/USDT").

Only coins Cosmas has already posted as X/USDT count. Common words are excluded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN_FILE = ROOT / "data" / "cosmas_known_coins.json"
SEED = {"ASTER", "LUMIA", "SOON", "MANA", "NIL", "SAND", "QNT", "RAY", "MUBARAK",
        "MARSCOIN", "BTC", "ETH", "SOL"}
EXCLUDE = {"HOT", "ON", "US", "THE", "IN", "IT", "AT", "TO", "BE", "ME", "MY", "NO", "SO",
           "UP", "GO", "OK", "AI", "ONE", "NOW", "ALL", "FOR", "AND", "OR", "TP", "SL",
           "CMP", "LONG", "SHORT", "BUY", "SELL", "USDT", "ENTRY", "STOP", "LOSS", "TAKE",
           "PROFIT", "LEVERAGE", "LEV", "HOLD", "PUMP", "DUMP", "MOON", "WIN", "GAS", "TRUMP"}
_PAIR = re.compile(r"\b([A-Za-z0-9]{2,20})\s*/\s*USDT\b|\b([A-Za-z0-9]{2,20})USDT\b", re.I)


def known() -> set[str]:
    out = set(SEED)
    try:
        out |= {str(x).upper() for x in json.loads(KNOWN_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        pass
    return out - EXCLUDE


def learn(text: str) -> None:
    """Record any X/USDT pair Cosmas posts so later bare mentions resolve."""
    found = {(a or b).upper() for a, b in _PAIR.findall(text or "")}
    found -= EXCLUDE
    if not found:
        return
    cur = known()
    if found <= cur:
        return
    try:
        KNOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        KNOWN_FILE.write_text(json.dumps(sorted(cur | found)), encoding="utf-8")
    except OSError:
        pass


def bare_symbol(text: str) -> str | None:
    coins = known()
    for word in re.findall(r"\$?\b([A-Za-z][A-Za-z0-9]{1,19})\b", text or ""):
        w = word.upper()
        if w in coins:
            return f"{w}USDT"
    return None
