#!/usr/bin/env python3
"""Fetch Cosmas TG, normalize typos, enrich incomplete Cosmas scalps, auto-execute.

Prints JSON summary. Exit 0 always. Caller treats ACTION lines as wakeups.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.chdir("/app")
os.environ.setdefault("PYTHONPATH", "/app")

for line in Path("/app/.env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from fxglitch.agent.auto_trade import maybe_execute, retry_pending, slots
from fxglitch.agent.telegram_user import _message_text
from fxglitch.signal_copy import parse_signal
from fxglitch.venues.bitunix import from_slot

CHAT = int(os.environ.get("COSMAS_CHAT_ID") or "-1002004401199")
MAX_AGE_H = float(os.environ.get("COSMAS_CATCHUP_HOURS") or "12")
LIMIT = int(os.environ.get("COSMAS_CATCHUP_LIMIT") or "200")

# Bare Cosmas coin nicknames → USDT
COIN_MAP = {
    "nil": "NILUSDT",
    "mana": "MANAUSDT",
    "sand": "SANDUSDT",
    "qnt": "QNTUSDT",
    "ray": "RAYUSDT",
    "mubarak": "MUBARAKUSDT",
    "marscoin": "MARSCOINUSDT",
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
}


def normalize_typos(text: str) -> str:
    t = text
    t = re.sub(r"\bShurt\b", "Short", t, flags=re.I)
    t = re.sub(r"\bLongg\b", "Long", t, flags=re.I)
    t = re.sub(r"\bbituinx\b", "bitunix", t, flags=re.I)
    return t


def enrich_cosmas(text: str) -> str | None:
    """If Cosmas-style scalp missing symbol/direction/CMP, rebuild a parseable blob."""
    fixed = normalize_typos(text)
    parsed = parse_signal(fixed)
    if parsed.symbol and parsed.direction and parsed.stop_loss and (
        parsed.entry_is_market or parsed.entry is not None
    ):
        return fixed

    lower = fixed.lower()
    # skip pure promo / travel / tutorial
    if not re.search(r"\b(tp|sl|take\s*profit|stop\s*loss|leverage|lev|scalp|strike|long|short|buy|sell)\b", lower):
        return None
    if re.search(r"https?://|youtube|tutorial|register|welcome traders|token2049|singapore|doha", lower) and not (
        re.search(r"\b(tp|sl)\b", lower) and re.search(r"\b(nil|mana|sand|qnt|ray|leverage|strike)\b", lower)
    ):
        # promo with no trade body
        if not (re.search(r"\btp\b", lower) and re.search(r"\bsl\b", lower)):
            return None

    symbol = parsed.symbol
    if not symbol:
        from fxglitch.cosmas_coins import bare_symbol
        symbol = bare_symbol(fixed)
    if not symbol:
        for nick, sym in COIN_MAP.items():
            if re.search(rf"\b{nick}\b", lower):
                symbol = sym
                break
    if not symbol:
        m = re.search(r"\b([A-Za-z]{2,15})\s*/?\s*USDT\b", fixed, re.I)
        if m:
            symbol = f"{m.group(1).upper()}USDT"
    if not symbol:
        return None

    direction = parsed.direction.value if parsed.direction else None
    if not direction:
        if re.search(r"\b(strike\s*it\s*down|dump|to\s+zero|short|sell|shurt)\b", lower):
            direction = "SHORT"
        elif re.search(r"\b(long|buy|longg|pump)\b", lower):
            direction = "LONG"
        elif parsed.stop_loss and parsed.take_profit:
            direction = "SHORT" if parsed.stop_loss > parsed.take_profit else "LONG"
    if not direction:
        return None

    sl = parsed.stop_loss
    tp = parsed.take_profit or (parsed.take_profits[0] if parsed.take_profits else None)
    if sl is None or tp is None:
        return None

    lev = parsed.leverage
    parts = [
        f"{symbol.replace('USDT', '')}/USDT",
        direction,
        "CMP",
        f"SL {sl}",
        f"TP {tp}",
    ]
    if lev is not None:
        parts.append(f"Leverage {lev}x")
    # Cosmas wick room note for downstream humans; parser ignores notes
    enriched = "\n".join(parts)
    return enriched


async def fetch_messages() -> list[dict]:
    from telethon import TelegramClient

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    src = Path("/app/data/telegram.session")
    if not src.exists():
        raise SystemExit("no telegram.session")
    stem = Path("/tmp/tg_catchup_cosmas")
    for path in Path("/app/data").glob("telegram.session*"):
        dest = Path(str(stem) + path.name[len("telegram") :])
        try:
            shutil.copy2(path, dest)
        except Exception as e:
            print("copy_warn", path.name, e)
    client = __import__('fxglitch.tgnet',fromlist=['x']).make_client(str(stem), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("tg not authorized")
    out = []
    async for msg in client.iter_messages(CHAT, limit=LIMIT):
        text = _message_text(msg) or ""
        dt = msg.date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        out.append({"id": msg.id, "at": dt, "text": text})
    await client.disconnect()
    return out


def books_snapshot() -> dict:
    snap = {}
    for s in slots() or [2, 3, 4]:
        try:
            v = from_slot(s)
            bal = v.balance()
            pos = v.positions() or []
            snap[f"s{s}"] = {
                "eq": float(getattr(bal, "available", 0) or 0)
                + float(getattr(bal, "used", 0) or 0),
                "avail": float(getattr(bal, "available", 0) or 0),
                "npos": len(pos),
                "syms": [getattr(p, "symbol", None) for p in pos],
            }
        except Exception as e:
            snap[f"s{s}"] = {"error": f"{type(e).__name__}: {e}"}
    return snap


def mark_seen() -> None:
    msgs = asyncio.run(fetch_messages())
    top = max((m["id"] for m in msgs), default=0)
    p = Path("/app/data/cosmas_seen.json")
    cur = json.loads(p.read_text()) if p.exists() else {}
    cur.update({"chat": str(CHAT), "max_id": max(top, int(cur.get("max_id") or 0)),
                "marked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    p.write_text(json.dumps(cur, indent=2))
    print("MARKED", cur)


def main() -> None:
    import sys
    if "--mark-seen" in sys.argv:
        return mark_seen()
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=MAX_AGE_H)
    try:
        retry_pending(limit=15)
    except Exception as e:
        print(f"retry_pending_err {e}")

    msgs = asyncio.run(fetch_messages())
    actions = []
    scanned = 0
    trade_like = 0
    for m in msgs:
        scanned += 1
        if m["at"] < cut:
            continue
        text = (m["text"] or "").strip()
        if not text:
            continue
        enriched = enrich_cosmas(text)
        if not enriched:
            continue
        trade_like += 1
        tid = f"{CHAT}:{m['id']}"
        posted = m["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
        parsed = parse_signal(enriched)
        try:
            res = maybe_execute(enriched, telegram_id=tid, posted_at=posted)
        except Exception as e:
            actions.append({"id": tid, "error": f"{type(e).__name__}: {e}", "parsed": str(parsed)})
            continue
        if res is None:
            continue
        row = {
            "id": tid,
            "ok": res.get("ok"),
            "reason": res.get("reason"),
            "sent": res.get("sent"),
            "errors": res.get("errors"),
            "retryable": res.get("retryable"),
            "symbol": parsed.symbol,
            "direction": str(parsed.direction) if parsed.direction else None,
            "enriched": enriched != normalize_typos(text),
        }
        # only surface as ACTION when something new sent or new skip that matters
        if res.get("sent"):
            actions.append(row)
        elif res.get("ok") is False and res.get("reason") and "already" not in str(res.get("reason") or "").lower():
            # still report first-seen skips for visibility once — keep compact
            actions.append(row)

    summary = {
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "watching_slots": slots(),
        "scanned": scanned,
        "trade_like_fresh": trade_like,
        "actions_n": len(actions),
        "actions": actions,
        "books": books_snapshot(),
    }
    Path("/tmp/cosmas_catchup_last.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    # Only ACTION when something newly sent this run; dedupe by order id so timers
    # never re-alert the same Bitunix fill.
    newly = [a for a in actions if a.get("sent")]
    alerted_path = Path("/app/data/catchup_alerted_orders.json")
    try:
        alerted = set(json.loads(alerted_path.read_text(encoding="utf-8"))) if alerted_path.exists() else set()
    except Exception:
        alerted = set()
    fresh_sent = []
    new_oids: list[str] = []
    for a in newly:
        kept = []
        for s in a.get("sent") or []:
            if not isinstance(s, dict):
                continue
            oid = str(s.get("order") or "")
            if oid and oid in alerted:
                continue
            kept.append(s)
            if oid:
                new_oids.append(oid)
        if kept:
            row = dict(a)
            row["sent"] = kept
            fresh_sent.append(row)
    if fresh_sent:
        alerted.update(new_oids)
        try:
            alerted_path.parent.mkdir(parents=True, exist_ok=True)
            alerted_path.write_text(json.dumps(sorted(alerted)[-500:], indent=2), encoding="utf-8")
        except Exception:
            pass
        print("ACTION FILLED", json.dumps(fresh_sent, default=str)[:1500])
    else:
        print(
            "SILENT",
            json.dumps(
                {
                    "scanned": scanned,
                    "trade_like_fresh": trade_like,
                    "books": summary["books"],
                    "seen": len(actions),
                },
                default=str,
            ),
        )


if __name__ == "__main__":
    main()
