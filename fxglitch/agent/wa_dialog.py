"""WhatsApp (and any chat) desk: access code → signals → risk → send.

Pure functions. The WhatsApp socket only feeds text in and replies out.
"""

from __future__ import annotations

from .access import unlock
from .inbox import recent_signals
from .plans import place_plan, plan_by_id, recommend
from ..venues.base import VenueError
from ..venues.bitunix import from_slot

PLAN_WORDS = {
    "easy": "low",
    "low": "low",
    "normal": "mid",
    "mid": "mid",
    "average": "mid",
    "average risk": "mid",
    "bold": "high",
    "high": "high",
    "max": "daredevil",
    "daredevil": "daredevil",
    "dare devil": "daredevil",
    "high risk": "high",
    "low risk": "low",
}
PLAN_LABEL = {"low": "Low risk", "mid": "Average risk", "high": "High risk", "daredevil": "Daredevil"}
HELP = (
    "Send your *access code* to open your Bitunix account.\n\n"
    "After login, you can type:\n"
    "• *available trades* — setups from Telegram\n"
    "• *running trades* — positions currently open\n"
    "• *account balance* — free, used, and total balance\n"
        "• *yes* — take the newest signal, then choose risk\n"
        "• *take* — choose a setup to trade\n"
    "• *help* — show this menu\n"
    "• *logout* — sign out"
)


def format_menu(session: dict) -> str:
    name = session.get("name") or "your account"
    equity = float(session.get("equity") or 0)
    return (
        f"You are in: *{name}*\n"
        f"Balance about {equity:.2f} USDT\n\n"
        "Choose an option (reply with the number or words):\n"
        "1️⃣ *Available trades*\n"
        "2️⃣ *Running trades*\n"
        "3️⃣ *Account balance*\n\n"
        "You can also type *take*, *help*, or *logout*."
    )


def _open_signals() -> list[dict]:
    rows = recent_signals(hours=4)
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
        return (
            "No new trades in the last 4 hours.\n"
            "I will message you when Cosmas posts one.\n\n"
            "2  Running trades\n"
            "3  Account balance"
        )
    lines = ["Available trades (last 4 hours):"]
    for i, row in enumerate(rows, 1):
        side = (row.get("direction") or "").upper()
        pair = row.get("bitunix_symbol") or row.get("binance_symbol") or "?"
        sl = row.get("stop")
        tp = row.get("take_profit") or (row.get("take_profits") or [None])[0]
        tag = "still open" if row.get("still_good") else "late"
        lines.append(f"\n{i}. {side} {pair} ({tag})")
        if sl or tp:
            lines.append(f"   Stop {sl}   Target {tp}")
    lines.append("\nReply with the number to take it, then choose risk.")
    lines.append("Menu: 2 running · 3 balance")
    return "\n".join(lines)


def _pick_signal(session: dict, index: int, rows: list[dict]) -> str:
    if index < 1 or index > len(rows):
        return "That number is not on the list. Send *signals* first."
    row = rows[index - 1]
    session["signal"] = row
    session["step"] = "risk"
    pair = row.get("bitunix_symbol") or row.get("binance_symbol")
    return (
        f"Trade {index}: {row.get('direction')} {pair}\n"
        f"Stop {row.get('stop')}   Target {row.get('take_profit')}\n\n"
        "Choose your risk/reward level:\n"
        "1  Low risk\n"
        "2  Average risk\n"
        "3  High risk\n"
        "4  Daredevil\n\n"
        "Or type low risk / average risk / high risk / daredevil."
    )


def _positions(session: dict) -> list:
    venue = from_slot(int(session.get("account") or 1))
    if not venue.authenticated:
        return []
    return venue.positions()


def format_positions(session: dict) -> str:
    rows = _positions(session)
    session["positions"] = [
        {"symbol": p.symbol, "side": p.side, "qty": p.qty, "venue_id": p.venue_id,
         "pnl": p.unrealised_pnl, "entry": p.entry_price}
        for p in rows
    ]
    if not rows:
        return "No running trades. Your account is flat.\n\n1  Available trades\n3  Account balance"
    lines = ["Running trades:"]
    for i, p in enumerate(rows, 1):
        pnl = p.unrealised_pnl
        tag = f"{pnl:+.2f} USDT" if pnl is not None else "—"
        lines.append(f"\n{i}. {p.side} {p.symbol}\n   Size {p.qty}   P/L {tag}")
    lines.append("\nReply *close 1* to close that trade, or *close all*.")
    lines.append("Menu: 1 trades · 3 balance")
    return "\n".join(lines)


def format_balance(session: dict) -> str:
    venue = from_slot(int(session.get("account") or 1))
    if not venue.authenticated:
        return "This account has no Bitunix keys on the desk."
    try:
        bal = venue.balance()
    except VenueError as exc:
        return f"Could not read balance: {exc}"
    session["equity"] = float(bal.equity or 0)
    open_n = 0
    try:
        open_n = len(venue.positions())
    except Exception:
        pass
    return (
        f"Account: *{session.get('name') or venue.account_name}*\n"
        f"Free: {float(bal.available):.2f} {bal.currency}\n"
        f"In trades: {float(bal.used):.2f} {bal.currency}\n"
        f"Total: {float(bal.equity):.2f} {bal.currency}\n"
        f"Running trades: {open_n}\n\n"
        "1  Available trades\n"
        "2  Running trades"
    )


def _close_one(session: dict, index: int) -> str:
    rows = session.get("positions") or []
    if not rows:
        format_positions(session)
        rows = session.get("positions") or []
    if index < 1 or index > len(rows):
        return "That number is not on the list. Send *positions* first."
    row = rows[index - 1]
    venue = from_slot(int(session.get("account") or 1))
    live = [p for p in venue.positions() if p.venue_id == row.get("venue_id") or (
        p.symbol == row.get("symbol") and p.side == row.get("side"))]
    if not live:
        return f"{row.get('symbol')} is already flat."
    try:
        result = venue.close(live[0], reason="whatsapp close")
    except VenueError as exc:
        return f"Bitunix said no: {exc}"
    session["step"] = "home"
    return (
        f"Closed {live[0].side} {live[0].symbol}.\n"
        f"Order {result.venue_order_id or 'ok'}."
    )


def _close_all(session: dict) -> str:
    venue = from_slot(int(session.get("account") or 1))
    rows = venue.positions()
    if not rows:
        return "Already flat."
    lines = []
    for pos in rows:
        try:
            result = venue.close(pos, reason="whatsapp close all")
            lines.append(f"Closed {pos.side} {pos.symbol} ({result.venue_order_id or 'ok'}).")
        except VenueError as exc:
            lines.append(f"{pos.symbol} failed: {exc}")
    left = venue.positions()
    if left:
        lines.append("Still open: " + ", ".join(p.symbol for p in left))
    else:
        lines.append("Book is flat.")
    session["step"] = "home"
    session.pop("positions", None)
    return "\n".join(lines)


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
            equity = 1000.0
            try:
                bal = from_slot(user["account"]).balance()
                if bal.equity:
                    equity = float(bal.equity)
            except Exception:
                pass
            session.update({
                "account": user["account"],
                "name": user["name"],
                "admin": user.get("admin"),
                "equity": equity,
                "step": "home",
            })
            return (
                f"Portfolio open: *{user['name']}* (Bitunix {user['account']}).\n"
                f"Equity ~{equity:.2f} USDT. New group trades will ping you here.\n"
                "Risk: *easy* / *normal* / *bold* / *max*.\n\n"
                + format_signals()
            )
        if low in ("login", "log in", "signals", "take"):
            return "Send your access code first."
        return "That code did not match. Ask the operator for your access code."

    rows = _open_signals()
    if low in ("signals", "setups", "list", "open", "trades", "available", "available trades", "available trade"):
        session["step"] = "home"
        return format_signals(rows)
    if low == "take":
        session["step"] = "pick"
        return format_signals(rows)
    if low in ("positions", "position", "book", "open positions", "running", "running trades", "open trades"):
        session["step"] = "close_pick"
        return format_positions(session)
    if low in ("balance", "account", "account balance", "my balance", "wallet"):
        session["step"] = "home"
        return format_balance(session)
    if low in ("close all", "flatten", "close everything"):
        return _close_all(session)
    if low.startswith("close "):
        rest = low.split(" ", 1)[1].strip()
        if rest in ("all", "everything"):
            return _close_all(session)
        if rest.isdigit():
            return _close_one(session, int(rest))
        session["step"] = "close_pick"
        return format_positions(session)
    if low == "close":
        session["step"] = "close_pick"
        return format_positions(session)

    if low.isdigit():
        if session.get("step") == "risk" and low in ("1", "2", "3", "4"):
            low = ("low", "average", "high", "daredevil")[int(low) - 1]
        else:
            if session.get("step") == "close_pick":
                return _close_one(session, int(low))
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
        if not session.get("signal"):
            if not rows:
                return "There is no recent signal to take. Type *available trades*."
            if len(rows) == 1:
                session["signal"] = rows[0]
                session["step"] = "risk"
                return _pick_signal(session, 1, rows)
            session["step"] = "pick"
            return "I found more than one recent signal. Reply with its number first.\n\n" + format_signals(rows)
        plan_id = session.get("plan")
        if not session.get("signal") or not plan_id:
            return "Nothing to send. *signals* then pick risk."
        return _execute(session, plan_id)
    if low in ("no", "n", "cancel"):
        session["step"] = "home"
        session.pop("plan", None)
        return "Cancelled. *signals* to see setups."
    return "I didn’t understand that. Type *available trades*, *running trades*, *account balance*, *take*, or *help*."
