"""WhatsApp (and any chat) desk: access code → signals → risk → send.

Pure functions. The WhatsApp socket only feeds text in and replies out.
"""

from __future__ import annotations

from .access import unlock
from .inbox import list_signals
from .plans import place_plan, plan_by_id, recommend
from ..venues.base import VenueError
from ..venues.bitunix import from_slot

PLAN_WORDS = {
    "easy": "low",
    "low": "low",
    "normal": "mid",
    "mid": "mid",
    "bold": "high",
    "high": "high",
    "max": "daredevil",
    "daredevil": "daredevil",
}
PLAN_LABEL = {"low": "Easy", "mid": "Normal", "high": "Bold", "daredevil": "Max"}
HELP = (
    "FX-GLITCH desk on WhatsApp.\n\n"
    "Send your *access code* to log in.\n"
    "Then:\n"
    "• *signals* — open setups\n"
    "• *take* — pick a trade\n"
    "• *1* / *2* / *3* — choose that setup\n"
    "• *easy* / *normal* / *bold* / *max* — risk\n"
    "• *yes* — send it on your Bitunix account\n"
    "• *logout*"
)


def _open_signals() -> list[dict]:
    rows = [s for s in list_signals() if s.get("still_good") is not False]
    out = []
    seen = set()
    for row in rows:
        key = row.get("id") or row.get("raw")
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 8:
            break
    return out


def format_signals(rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else _open_signals()
    if not rows:
        return "No open setups right now. I'll ping you when the group posts one."
    lines = ["Open now:"]
    for i, row in enumerate(rows, 1):
        side = (row.get("direction") or "").upper()
        pair = row.get("bitunix_symbol") or row.get("binance_symbol") or "?"
        tag = "OPEN" if row.get("still_good") else "late"
        lines.append(f"{i}. {side} {pair} ({tag})")
    lines.append("Reply *take* then the number, or just the number.")
    return "\n".join(lines)


def _pick_signal(session: dict, index: int, rows: list[dict]) -> str:
    if index < 1 or index > len(rows):
        return "That number is not on the list. Send *signals* first."
    row = rows[index - 1]
    session["signal"] = row
    session["step"] = "risk"
    pair = row.get("bitunix_symbol") or row.get("binance_symbol")
    return (
        f"{row.get('direction')} {pair}\n"
        f"Entry {row.get('entry')}  SL {row.get('stop')}\n"
        "How hard? Reply *easy*, *normal*, *bold*, or *max*."
    )


def _execute(session: dict, plan_id: str) -> str:
    row = session.get("signal") or {}
    raw = row.get("raw") or ""
    slot = int(session.get("account") or 1)
    rec = recommend(raw, equity=float(session.get("equity") or 1000), mark=row.get("mark"))
    plan = plan_by_id(rec, plan_id)
    if plan is None or not plan.get("policy_ok"):
        return (plan or {}).get("policy_reason") or "That risk is not available on this setup."
    venue = from_slot(slot)
    if not venue.authenticated:
        return f"{session.get('name') or 'This account'} has no Bitunix keys yet."
    try:
        placed = place_plan(venue, rec, plan)
    except VenueError as exc:
        return f"Bitunix said no: {exc}"
    session["step"] = "home"
    session.pop("signal", None)
    session.pop("plan", None)
    label = PLAN_LABEL.get(plan_id, plan_id)
    return (
        f"Sent *{label}* on {venue.account_name}.\n"
        f"{rec.direction} {placed.get('symbol')} {plan['leverage']}x\n"
        f"SL {plan['stop']}  TP {plan['take_profit']}\n"
        f"Order {placed.get('order_id')}"
    )


def handle(session: dict, text: str) -> str:
    """Mutates session. Returns WhatsApp reply text."""
    msg = (text or "").strip()
    low = msg.lower()
    if not msg:
        return HELP
    if low in ("help", "hi", "hello", "start", "menu"):
        if session.get("account"):
            return f"Logged in as {session.get('name')}.\n\n" + HELP
        return HELP
    if low in ("logout", "log out", "sign out"):
        session.clear()
        return "Logged out. Send your access code when you want back in."

    if not session.get("account"):
        user = unlock(msg)
        if user:
            session.update({
                "account": user["account"],
                "name": user["name"],
                "admin": user.get("admin"),
                "step": "home",
            })
            return (
                f"In. {user['name']} (Bitunix account {user['account']}).\n\n"
                + format_signals()
            )
        if low in ("login", "log in", "signals", "take"):
            return "Send your access code first."
        return "That code did not match. Ask the operator for your access code."

    rows = _open_signals()
    if low in ("signals", "setups", "list", "open"):
        session["step"] = "home"
        return format_signals(rows)
    if low == "take":
        session["step"] = "pick"
        return format_signals(rows)

    if low.isdigit():
        return _pick_signal(session, int(low), rows)

    if low in PLAN_WORDS:
        if not session.get("signal"):
            session["step"] = "pick"
            return "Pick a setup first. " + format_signals(rows)
        plan_id = PLAN_WORDS[low]
        session["plan"] = plan_id
        session["step"] = "confirm"
        plan = None
        rec = recommend(session["signal"].get("raw") or "", mark=session["signal"].get("mark"))
        plan = plan_by_id(rec, plan_id)
        label = PLAN_LABEL[plan_id]
        extra = ""
        if plan:
            extra = f"\n{plan['leverage']}x · risk {plan['risk_pct']}% · lose ~{plan['loss_if_sl']:.2f}"
        return f"{label} on {session['signal'].get('bitunix_symbol') or session['signal'].get('binance_symbol')}.{extra}\nReply *yes* to send, or *no*."

    if low in ("yes", "y", "send", "confirm", "execute"):
        plan_id = session.get("plan")
        if not session.get("signal") or not plan_id:
            return "Nothing to send. *signals* then pick risk."
        return _execute(session, plan_id)
    if low in ("no", "n", "cancel"):
        session["step"] = "home"
        session.pop("plan", None)
        return "Cancelled. *signals* to see setups."
    return "Send *signals*, a number, *easy/normal/bold/max*, or *help*."
