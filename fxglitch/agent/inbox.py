"""Persist incoming Telegram / pasted signals for the dashboard."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .plans import recommend

ROOT = Path(__file__).resolve().parents[2]
PATH = Path(os.environ.get("FXGLITCH_INBOX", str(ROOT / "data" / "inbox.json")))
MAX = 80
_MEMORY: list[dict] = []


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> list[dict]:
    try:
        if PATH.exists():
            return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return list(_MEMORY)


def _save(rows: list[dict]) -> None:
    _MEMORY.clear()
    _MEMORY.extend(rows[:MAX])
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(PATH.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows[:MAX], fh, indent=2)
        os.replace(tmp, PATH)
    except OSError:
        pass


DEMO = {
    "id": "demo-kaito",
    "telegram_id": "demo-kaito",
    "source": "demo",
    "chat": "Bitunix futures group (sample)",
    "raw": "BTCUSDT\nLong position\nEntry 110000\nTp 120000\nSl 105000\nLeverage 10x",
    "received_at": "2026-09-06T00:00:00Z",
    "bitunix_symbol": "BTCUSDT",
    "binance_symbol": "BTCUSDT",
    "direction": "LONG",
    "warnings": [],
    "listed": None,
    "tradeable": None,
}


def list_signals() -> list[dict]:
    rows = _load()[:MAX]
    return rows if rows else [DEMO]


def ingest(message: str, *, source: str = "paste", telegram_id: str | None = None,
           chat: str | None = None) -> dict:
    text = (message or "").strip()
    if not text:
        raise ValueError("empty signal")
    rows = _load()
    key = telegram_id or f"{source}:{hash(text)}"
    for row in rows:
        if row.get("telegram_id") == key or (telegram_id and row.get("telegram_id") == telegram_id):
            return row
    rec = recommend(text, equity=1000.0)
    item = {
        "id": key,
        "telegram_id": telegram_id or key,
        "source": source,
        "chat": chat,
        "raw": text,
        "received_at": _now(),
        "bitunix_symbol": rec.bitunix_symbol,
        "binance_symbol": rec.binance_symbol,
        "direction": rec.direction,
        "warnings": rec.warnings,
        "listed": rec.pair.get("listed"),
        "tradeable": rec.pair.get("tradeable"),
    }
    rows.insert(0, item)
    _save(rows)
    return item
