"""Operator desk tools for the Grok bot / HTTP API. Never returns secrets."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..venues.bitunix import SLOTS, from_slot, listed_accounts
from .auto_trade import maybe_execute, slots as auto_slots
from .protection import ROOT as PROT_ROOT

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "data" / "cosmas_strategy_knowledge.json"
TRAIL = ROOT / "data" / "trail_orig.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _equity(bal) -> float:
    return float(bal.available) + float(bal.used) + float(bal.unrealised_pnl)


def _roi(pos) -> float | None:
    try:
        if not (pos.entry_price and pos.qty and pos.leverage):
            return None
        margin = abs(float(pos.entry_price) * float(pos.qty)) / float(pos.leverage)
        if margin <= 0 or pos.unrealised_pnl is None:
            return None
        return round(100.0 * float(pos.unrealised_pnl) / margin, 1)
    except Exception:
        return None


def status_books() -> dict[str, Any]:
    books = []
    total_eq = total_upnl = 0.0
    open_n = 0
    for s in SLOTS:
        try:
            v = from_slot(s)
            bal = v.balance()
            eq = float(bal.available) + float(bal.used)
            upnl = float(bal.unrealised_pnl)
            total_eq += eq
            total_upnl += upnl
            positions = []
            for p in v.positions() or []:
                open_n += 1
                pid = str(p.venue_id or "")
                try:
                    mark = float(v.mark_price(p.symbol))
                except Exception:
                    mark = None
                side = "LONG" if int(getattr(p, "direction", 0) or 0) == 1 else "SHORT"
                if p.symbol == "ASTERUSDT":
                    side = "LONG"
                positions.append(
                    {
                        "symbol": p.symbol,
                        "side": side,
                        "qty": float(p.qty),
                        "entry": float(p.entry_price) if p.entry_price is not None else None,
                        "mark": mark,
                        "lev": int(p.leverage) if p.leverage is not None else None,
                        "sl": v.stops(p.symbol).get(pid),
                        "tp": v.targets(p.symbol).get(pid),
                        "upnl": float(p.unrealised_pnl or 0),
                        "roi_pct": _roi(p),
                        "venue_id": pid,
                    }
                )
            books.append(
                {
                    "slot": s,
                    "name": v.account_name,
                    "equity": round(eq, 4),
                    "available": round(float(bal.available), 4),
                    "used": round(float(bal.used), 4),
                    "upnl": round(upnl, 4),
                    "positions": positions,
                }
            )
        except Exception as e:
            books.append({"slot": s, "error": f"{type(e).__name__}: {e}"})
    return {
        "ok": True,
        "at": _now(),
        "auto_slots": auto_slots(),
        "listed": listed_accounts(),
        "books": books,
        "total_equity": round(total_eq, 4),
        "total_upnl": round(total_upnl, 4),
        "open_positions": open_n,
    }


def available_setups() -> dict[str, Any]:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
        avail = data.get("available") or []
        return {
            "ok": True,
            "watching": data.get("watching"),
            "watch_title": data.get("watch_title"),
            "available": avail,
            "n": len(avail),
        }
    except Exception as e:
        # Fallback: rebuild from inbox without importing http_api (avoid cycles)
        try:
            from .inbox import recent_signals
            from .plans import fetch_mark, live_entry_check, recommend

            avail = []
            for row in recent_signals(hours=18)[:20]:
                raw = row.get("raw") or ""
                if not raw:
                    continue
                try:
                    rec = recommend(raw, equity=1000.0)
                    mark = rec.mark or fetch_mark(rec.binance_symbol or rec.symbol or "")
                    check = live_entry_check(
                        direction=rec.direction,
                        entry=rec.entry,
                        stop=rec.stop,
                        take_profits=rec.take_profits,
                        mark=mark,
                    )
                    if not check.get("ok"):
                        continue
                    avail.append(
                        {
                            "id": row.get("id") or row.get("telegram_id"),
                            "symbol": rec.symbol or row.get("symbol"),
                            "side": str(rec.direction) if rec.direction else row.get("side"),
                            "stop": rec.stop,
                            "take_profit": (rec.take_profits or [None])[0],
                            "raw": raw,
                            "still_good": True,
                            "reason": check.get("reason"),
                            "posted_at": row.get("posted_at"),
                        }
                    )
                except Exception:
                    continue
            return {"ok": True, "available": avail, "n": len(avail), "source": "inbox_fallback"}
        except Exception as e2:
            return {"ok": False, "error": f"{type(e).__name__}: {e}; fallback {e2}"}


def execute_available(*, confirm: bool = False) -> dict[str, Any]:
    """Execute still-good Cosmas available setups with daredevil auto_trade."""
    if not confirm:
        return {
            "ok": False,
            "need_confirm": True,
            "hint": "Pass confirm=true to place daredevil fills on AUTO_SLOTS.",
        }
    info = available_setups()
    results = []
    for row in info.get("available") or []:
        if not row.get("still_good", True):
            continue
        raw = row.get("raw") or ""
        if not raw:
            continue
        tid = str(row.get("id") or f"desk:{row.get('symbol')}")
        sidecar = f"{tid}:desk_tools"
        posted = row.get("posted_at") or _now()
        try:
            res = maybe_execute(raw, telegram_id=sidecar, posted_at=posted)
        except Exception as e:
            results.append({"id": tid, "error": f"{type(e).__name__}: {e}"})
            continue
        if res is None:
            results.append({"id": tid, "skipped": True})
            continue
        results.append(
            {
                "id": tid,
                "symbol": res.get("symbol") or row.get("symbol"),
                "ok": res.get("ok"),
                "sent": res.get("sent"),
                "errors": (res.get("errors") or [])[:8],
                "reason": res.get("reason"),
            }
        )
        # seed trail 200 for new fills
        for sent in res.get("sent") or []:
            if isinstance(sent, dict) and sent.get("slot"):
                _seed_trail_for_slot(int(sent["slot"]), res.get("symbol") or row.get("symbol"))
    return {"ok": True, "at": _now(), "results": results, "books": status_books().get("books")}


def _seed_trail_for_slot(slot: int, symbol: str | None) -> None:
    if not symbol:
        return
    try:
        v = from_slot(slot)
        trail = json.loads(TRAIL.read_text(encoding="utf-8")) if TRAIL.exists() else {}
        for p in v.positions() or []:
            if p.symbol != symbol:
                continue
            pid = str(p.venue_id or "")
            if not pid:
                continue
            side = "LONG" if int(p.direction) == 1 else "SHORT"
            if symbol == "ASTERUSDT":
                side = "LONG"
            trail[pid] = {
                "symbol": symbol,
                "side": side,
                "slot": slot,
                "entry": float(p.entry_price) if p.entry_price is not None else None,
                "sl": v.stops(p.symbol).get(pid),
                "tp": v.targets(p.symbol).get(pid),
                "step_size": 200,
                "mode": "trail_orig",
                "source": "desk_tools",
                "step": 0,
                "be": False,
            }
        TRAIL.parent.mkdir(parents=True, exist_ok=True)
        TRAIL.write_text(json.dumps(trail, indent=2), encoding="utf-8")
    except Exception:
        pass


def trail_stops(
    *,
    symbol: str | None = None,
    usd: float = 5.0,
    confirm: bool = False,
) -> dict[str, Any]:
    """Tighten stops by ~usd from mark (SHORT: mark+usd/qty; LONG: mark-usd/qty). Never loosen."""
    if not confirm:
        return {
            "ok": False,
            "need_confirm": True,
            "hint": f"Pass confirm=true to trail {symbol or 'all'} stops by ~${usd}.",
        }
    sym_filter = (symbol or "").upper().replace("/", "")
    if sym_filter and not sym_filter.endswith("USDT"):
        sym_filter = f"{sym_filter}USDT"
    changes = []
    for s in auto_slots() or list(SLOTS):
        v = from_slot(s)
        for pos in v.positions() or []:
            if sym_filter and pos.symbol != sym_filter:
                continue
            pid = str(pos.venue_id or "")
            qty = float(pos.qty)
            if qty <= 0:
                continue
            mark = float(v.mark_price(pos.symbol))
            cur_sl = v.stops(pos.symbol).get(pid)
            direction = int(getattr(pos, "direction", 0) or 0)
            # ASTER Cosmas is long geometry
            is_long = direction == 1 or pos.symbol == "ASTERUSDT"
            gap = abs(usd) / qty
            proposed = mark - gap if is_long else mark + gap
            if cur_sl is not None:
                if is_long:
                    new_sl = max(float(cur_sl), proposed)  # never lower LONG SL
                else:
                    new_sl = min(float(cur_sl), proposed)  # never raise SHORT SL
            else:
                new_sl = proposed
            inst = v.instruments().get(pos.symbol)
            if inst:
                new_sl = float(inst.round_price(new_sl))
            if cur_sl is not None and abs(float(cur_sl) - new_sl) < 1e-12:
                changes.append({"slot": s, "symbol": pos.symbol, "status": "no_change", "sl": cur_sl})
                continue
            if is_long and cur_sl is not None and float(cur_sl) > new_sl + 1e-12:
                changes.append({"slot": s, "symbol": pos.symbol, "status": "skip_loosen", "sl": cur_sl})
                continue
            if (not is_long) and cur_sl is not None and float(cur_sl) < new_sl - 1e-12:
                changes.append({"slot": s, "symbol": pos.symbol, "status": "skip_loosen", "sl": cur_sl})
                continue
            try:
                v.set_stop(pos, new_sl)
                after = v.stops(pos.symbol).get(pid)
                changes.append(
                    {
                        "slot": s,
                        "name": v.account_name,
                        "symbol": pos.symbol,
                        "status": "set",
                        "old_sl": cur_sl,
                        "new_sl": after,
                        "mark": mark,
                    }
                )
            except Exception as e:
                changes.append({"slot": s, "symbol": pos.symbol, "status": "error", "error": str(e)})
    return {"ok": True, "at": _now(), "usd": usd, "symbol": sym_filter or None, "changes": changes}


def protect_open() -> dict[str, Any]:
    script = ROOT / "tools" / "protect_nil_mana.py"
    if not script.exists():
        return {"ok": False, "error": "protect_nil_mana.py missing"}
    try:
        # Prefer in-process import path via subprocess in container-friendly way
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        out = subprocess.run(
            [os.environ.get("PYTHON", "python"), str(script)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "ok": out.returncode == 0,
            "stdout": (out.stdout or "")[-1500:],
            "stderr": (out.stderr or "")[-500:],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def clear_daily_halt(*, slot: int | None = None, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {
            "ok": False,
            "need_confirm": True,
            "hint": "Pass confirm=true to reset day_open_equity to live equity and clear halt.",
        }
    targets = [slot] if slot else list(auto_slots() or [2, 3, 4, 5])
    cleared = []
    for s in targets:
        if s is None:
            continue
        path = Path(PROT_ROOT) / f"bitunix-{int(s)}.json"
        try:
            v = from_slot(int(s))
            eq = _equity(v.balance())
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            before = {
                "halted": data.get("halted"),
                "halt_reason": data.get("halt_reason"),
                "day_open_equity": data.get("day_open_equity"),
            }
            data["halted"] = False
            data["halt_reason"] = ""
            data["day_open_equity"] = float(eq)
            data["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            cleared.append({"slot": int(s), "before": before, "day_open_equity": eq})
        except Exception as e:
            cleared.append({"slot": int(s), "error": f"{type(e).__name__}: {e}"})
    return {"ok": True, "at": _now(), "cleared": cleared}


def learn_summary() -> dict[str, Any]:
    if not KNOWLEDGE.exists():
        return {"ok": False, "error": "cosmas_strategy_knowledge.json missing"}
    try:
        data = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    lp = data.get("learning_progress") or {}
    persona = data.get("trader_persona") or {}
    return {
        "ok": True,
        "version": data.get("version") or data.get("knowledge_version"),
        "learning_progress": lp if isinstance(lp, dict) else {},
        "persona_keys": list(persona.keys())[:12] if isinstance(persona, dict) else [],
        "top_keys": list(data.keys())[:20],
    }


def run_learn(*, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "need_confirm": True, "hint": "Pass confirm=true to run cosmas_learn.sh"}
    script = ROOT / "tools" / "cosmas_learn.sh"
    if not script.exists():
        return {"ok": False, "error": "cosmas_learn.sh missing"}
    try:
        out = subprocess.run(
            ["bash", str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        return {
            "ok": out.returncode == 0,
            "stdout": (out.stdout or "")[-2000:],
            "stderr": (out.stderr or "")[-800:],
            "summary": learn_summary(),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "status_books",
            "description": "Live Bitunix equities and open positions for all slots.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "available_setups",
            "description": "Cosmas available setups still between SL and TP.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_available",
            "description": "Execute still-good Cosmas setups with daredevil (15x floor). Requires confirm=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm": {"type": "boolean", "description": "Must be true to place orders"},
                },
                "required": ["confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trail_stops",
            "description": "Tighten stops by ~usd from mark. Never loosens. Requires confirm=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "e.g. NIL or NILUSDT; omit for all"},
                    "usd": {"type": "number", "default": 5},
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protect_open",
            "description": "Re-apply Cosmas NIL/MANA SL/TP and seed trail 200.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_daily_halt",
            "description": "Reset daily-loss halt by anchoring day_open to live equity. Requires confirm=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {"type": "integer"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "learn_summary",
            "description": "Summarize Cosmas strategy knowledge pack progress.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_learn",
            "description": "Run daily Cosmas strategy learner. Requires confirm=true.",
            "parameters": {
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
            },
        },
    },
]


def call_tool(name: str, arguments: dict | None = None) -> dict[str, Any]:
    args = arguments or {}
    if name == "status_books":
        return status_books()
    if name == "available_setups":
        return available_setups()
    if name == "execute_available":
        return execute_available(confirm=bool(args.get("confirm")))
    if name == "trail_stops":
        return trail_stops(
            symbol=args.get("symbol"),
            usd=float(args.get("usd") or 5),
            confirm=bool(args.get("confirm")),
        )
    if name == "protect_open":
        return protect_open()
    if name == "clear_daily_halt":
        slot = args.get("slot")
        return clear_daily_halt(
            slot=int(slot) if slot is not None else None,
            confirm=bool(args.get("confirm")),
        )
    if name == "learn_summary":
        return learn_summary()
    if name == "run_learn":
        return run_learn(confirm=bool(args.get("confirm")))
    return {"ok": False, "error": f"unknown tool {name}"}
