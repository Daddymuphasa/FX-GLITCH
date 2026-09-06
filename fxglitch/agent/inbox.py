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


LIVE_PATH = ROOT / "public" / "live-signals.json"


def _load_live() -> list[dict]:
    try:
        if LIVE_PATH.exists():
            payload = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
            rows = payload.get("signals") if isinstance(payload, dict) else payload
            if isinstance(rows, list):
                return rows
    except (OSError, json.JSONDecodeError):
        pass
    return []


def publish_live() -> list[dict]:
    good = [row for row in _load() if row.get("still_good") is True][:20]
    try:
        LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LIVE_PATH.write_text(
            json.dumps({"updated": _now(), "signals": good}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return good


def list_signals() -> list[dict]:
    rows = _load()[:MAX]
    if not rows:
        rows = _load_live() or [DEMO]

    def _key(row: dict) -> tuple:
        good = 0 if row.get("still_good") is True else 1
        stamp = str(row.get("posted_at") or row.get("received_at") or "")
        return (good, stamp)

    return sorted(rows, key=_key)


def ingest(message: str, *, source: str = "paste", telegram_id: str | None = None,
           chat: str | None = None, posted_at: str | None = None,
           extra: dict | None = None) -> dict:
    text = (message or "").strip()
    if not text:
        raise ValueError("empty signal")
    rows = _load()
    key = telegram_id or f"{source}:{hash(text)}"
    for row in rows:
        if row.get("telegram_id") == key or (telegram_id and row.get("telegram_id") == telegram_id):
            if extra:
                row.update(extra)
                _save(rows)
            return row
    rec = recommend(text, equity=1000.0)
    item = {
        "id": key,
        "telegram_id": telegram_id or key,
        "source": source,
        "chat": chat,
        "raw": text,
        "received_at": posted_at or _now(),
        "posted_at": posted_at,
        "bitunix_symbol": rec.bitunix_symbol,
        "binance_symbol": rec.binance_symbol,
        "direction": rec.direction,
        "warnings": rec.warnings,
        "listed": rec.pair.get("listed"),
        "tradeable": rec.pair.get("tradeable"),
    }
    if extra:
        item.update(extra)
    rows.insert(0, item)
    _save(rows)
    return item
