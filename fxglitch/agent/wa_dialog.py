"""WhatsApp (and any chat) desk: access code → signals → risk → send.

Pure functions. The WhatsApp socket only feeds text in and replies out.
"""

from __future__ import annotations

from .access import unlock
from .inbox import recent_signals
from .plans import place_plan, plan_by_id, recommend
from .protection import ACCOUNT_LOCK, observe_account
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
    "• *available trades* — current setups\n"
    "• *running trades* — positions currently open\n"
    "• *account balance* — free, used, and total balance\n"
    "• *yes* — take the newest signal, then choose risk\n"
    "• *take* — choose a setup to trade\n"
    "• *auto mate* — automatically take new setups after safety checks\n"
    "• *auto off* — stop automatic entries\n"
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


def _enrich_signal(row: dict) -> dict:
    """Parse the original setup again so WhatsApp never relies on stale fields."""
    raw = row.get("raw") or ""
    if not raw:
        return row
    try:
        rec = recommend(raw, mark=row.get("mark"))
    except Exception:
        return row
    if row.get("direction") is None and rec.direction:
        row["direction"] = rec.direction
    if row.get("bitunix_symbol") is None and rec.bitunix_symbol:
        row["bitunix_symbol"] = rec.bitunix_symbol
    if row.get("entry") is None and rec.entry is not None:
        row["entry"] = rec.entry
    if row.get("stop") is None and rec.stop is not None:
        row["stop"] = rec.stop
    if not row.get("take_profits") and rec.take_profits:
        row["take_profits"] = rec.take_profits
    if row.get("take_profit") is None and rec.take_profits:
        row["take_profit"] = rec.take_profits[0]
    return row


def _recommend_signal(row: dict, **kwargs):
    mark = kwargs.pop("mark", row.get("mark"))
    return recommend(
        row.get("raw") or "",
        mark=mark,
        entry_override=mark if mark is not None else row.get("entry"),
        stop_override=row.get("stop"),
        take_profits_override=row.get("take_profits"),
        **kwargs,
    )


def _live_recommendation(session: dict):
    venue = from_slot(int(session["account"]))
    if not venue.authenticated:
        raise VenueError("bitunix", "no-keys", "this account has no exchange keys")
    balance = venue.balance()
    session["equity"] = balance.equity
    row = session["signal"]
    symbol = row.get("bitunix_symbol") or row.get("binance_symbol")
    mark = venue.mark_price(symbol)
    if mark is None or mark <= 0:
        raise VenueError("bitunix", "no-price", "could not read a live price; try again")
    return _recommend_signal(row, mark=mark, balance=balance, instruments=venue.instruments())


def _target_fit_label(rec) -> str:
    for plan in reversed(rec.plans):
        if plan.get("policy_ok") and plan.get("target_ok"):
            return plan.get("label") or PLAN_LABEL.get(plan.get("id"), plan.get("id", ""))
    return ""


def _unavailable_plan(session: dict, rec, plan: dict | None) -> str:
    session.pop("plan", None)
    session.pop("quote", None)
    session["step"] = "risk"
    if not plan or not plan.get("below_minimum"):
        return (plan or {}).get("policy_reason") or "That risk is not available on this setup."
    symbol = rec.bitunix_symbol or rec.binance_symbol
    base = symbol.removesuffix("USDT")
    lines = [
        "I did not send this trade.",
        "",
        f"The issue: Bitunix will not accept a {symbol} order smaller than "
        f"{plan['minimum_qty']:g} {base}.",
        f"Your selected level ({plan['label']}) only allowed about "
        f"{plan['qty']:g} {base}.",
        "",
        "If I forced the exchange minimum, your possible loss at the stop would "
        f"be about {plan['minimum_loss_if_sl']:.2f} USDT "
        f"({plan['minimum_risk_pct']:.2f}% of your account), which is higher "
        "than the risk level you chose.",
        "",
        "So I skipped it to avoid taking a bigger trade than you approved.",
    ]
    available = [p for p in rec.plans if p.get("policy_ok") and not p.get("below_minimum")]
    if available:
        alternative = min(available, key=lambda p: p["risk_pct"])
        lines.append(
            f"Smallest valid choice right now: {alternative['label']} "
            f"({alternative['risk_pct']:g}% risk), about {alternative['qty']:g} "
            f"{base}, estimated stop loss {alternative['loss_if_sl']:.2f} USDT."
        )
        lines.append(
            f"Reply *{alternative['label'].lower()}* to review it, then *yes* "
            "if you still want to send. Nothing has been sent."
        )
    else:
        lines.append(
            "No risk level is valid for this setup with the current account "
            "balance and exchange minimum. Nothing has been sent."
        )
    return "\n".join(lines)


def _auto_plan_id(session: dict) -> str:
    plan = str(session.get("auto_plan") or "mid").lower()
    return plan if plan in PLAN_LABEL else "mid"


def _auto_status(session: dict) -> str:
    if not session.get("auto_trade"):
        return (
            "Auto trade is OFF.\n"
            "Type *auto mate* to turn it on, or *auto mate low risk* / "
            "*auto mate average risk* / *auto mate high risk*."
        )
    label = PLAN_LABEL.get(_auto_plan_id(session), "Average risk")
    return (
        f"Auto trade is ON for {session.get('name') or 'this account'}.\n"
        f"Risk level: {label}.\n"
        "I will try new group setups automatically, but only after these "
        "safety checks pass: live price, balance, exchange minimum, duplicate "
        "signal, daily-loss, stop-loss, and take-profit.\n"
        "Stops are checked and trailed by the desk in the background.\n"
        "Type *auto off* to stop automatic entries."
    )


def _set_auto(session: dict, text: str) -> str:
    low = text.lower()
    for phrase, plan in sorted(PLAN_WORDS.items(), key=lambda item: -len(item[0])):
        if phrase in low:
            session["auto_plan"] = plan
            break
    session.setdefault("auto_plan", "mid")
    session["auto_trade"] = True
    session["step"] = "home"
    return _auto_status(session)


def auto_take_signal(session: dict, row: dict) -> str:
    """Try one automatic entry for one user session.

    This is intentionally the same path as a manual WhatsApp confirmation:
    live price and account are reread, sizing is recomputed, the exchange
    minimum is respected, and `place_plan` supplies the duplicate-signal guard.
    """
    if not session.get("account") or not session.get("auto_trade"):
        return ""
    enriched = _enrich_signal(dict(row))
    previous_signal = session.get("signal")
    previous_plan = session.get("plan")
    previous_quote = session.get("quote")
    try:
        session["signal"] = enriched
        plan_id = _auto_plan_id(session)
        venue = from_slot(int(session.get("account") or 1))
        if not venue.authenticated:
            return "Auto skipped: this account has no Bitunix keys."
        try:
            rec = _live_recommendation(session)
        except (VenueError, OSError) as exc:
            return f"Auto skipped {enriched.get('bitunix_symbol') or enriched.get('binance_symbol') or 'setup'}: could not verify live price/account ({exc})."
        plan = plan_by_id(rec, plan_id)
        if plan is None or not plan.get("policy_ok"):
            reason = _unavailable_plan(session, rec, plan)
            return "Auto skipped:\n" + reason
        try:
            placed = place_plan(venue, rec, plan, request_id=str(enriched.get("id") or enriched.get("telegram_id") or enriched.get("raw") or ""))
        except VenueError as exc:
            return f"Auto skipped {rec.bitunix_symbol or rec.binance_symbol}: {exc}"
        label = PLAN_LABEL.get(plan_id, plan_id)
        return (
            f"Auto sent *{label}* on {venue.account_name}.\n"
            f"{rec.direction} {placed.get('symbol')} {plan['leverage']}x\n"
            f"SL {plan['stop']}  TP {plan['take_profit']}\n"
            f"Order {placed.get('order_id') or 'ok'}"
        )
    finally:
        if previous_signal is None:
            session.pop("signal", None)
        else:
            session["signal"] = previous_signal
        if previous_plan is None:
            session.pop("plan", None)
        else:
            session["plan"] = previous_plan
        if previous_quote is None:
            session.pop("quote", None)
        else:
            session["quote"] = previous_quote


def format_signals(rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else _open_signals()
    if not rows:
        return (
            "No new trades in the last 4 hours.\n"
            "I will message you when a new setup is available.\n\n"
            "2  Running trades\n"
            "3  Account balance"
        )
    lines = ["Available trades (last 4 hours):"]
    for i, row in enumerate(rows, 1):
        row = _enrich_signal(row)
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
    row = _enrich_signal(rows[index - 1])
    stop = row.get("stop")
    targets = row.get("take_profits") or []
    target = row.get("take_profit") or (targets[0] if targets else None)
    if stop in (None, "", 0) or target in (None, "", 0):
        session.pop("signal", None)
        session["step"] = "home"
        return (
            f"{row.get('bitunix_symbol') or row.get('binance_symbol') or 'This setup'} "
            "is missing a stop-loss or target, so I will not send it.\n"
            "Please wait for a complete setup."
        )
    session["signal"] = row
    session.pop("plan", None)
    session.pop("quote", None)
    session["step"] = "risk"
    pair = row.get("bitunix_symbol") or row.get("binance_symbol")
    return (
        f"Trade {index}: {row.get('direction')} {pair}\n"
        f"Stop {stop}   Target {target}\n\n"
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
    with ACCOUNT_LOCK:
        progress = observe_account(venue, bal).progress(bal.equity)
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
        f"Today: {progress['daily_gain_pct']:+.2f}% / {progress['daily_target_pct']:.2f}% target\n"
        f"Week: {progress['weekly_gain_pct']:+.2f}% / {progress['weekly_target_pct']:.0f}% target\n"
        "Equity change includes open P/L and transfers; not all profit is realised.\n"
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
    slot = int(session.get("account") or 1)
    venue = from_slot(slot)
    if not venue.authenticated:
        return f"{session.get('name') or 'This account'} has no Bitunix keys yet."
    try:
        rec = _live_recommendation(session)
    except (VenueError, OSError) as exc:
        return f"Trade not sent: could not verify the live account or price: {exc}"
    plan = plan_by_id(rec, plan_id)
    if plan is None or not plan.get("policy_ok"):
        return _unavailable_plan(session, rec, plan)
    quote = session.get("quote")
    if quote and quote.get("id") == plan_id:
        plan["qty"] = min(plan["qty"], quote["qty"])
    instrument = venue.instruments().get(plan.get("symbol") or rec.bitunix_symbol)
    if instrument and float(plan.get("qty") or 0) < float(instrument.min_qty):
        return _unavailable_plan(session, rec, dict(plan, below_minimum=True))
    try:
        placed = place_plan(venue, rec, plan, request_id=str(row.get("id") or ""))
    except VenueError as exc:
        return f"Bitunix said no: {exc}"
    session["step"] = "home"
    session.pop("signal", None)
    session.pop("plan", None)
    session.pop("quote", None)
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
    if low in ("help", "hi", "hello", "hey", "start", "menu"):
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
                f"Equity ~{equity:.2f} USDT. New setups will be sent here.\n"
                "Risk: *easy* / *normal* / *bold* / *max*.\n\n"
                + format_signals()
            )
        if low in ("login", "log in", "signals", "take"):
            return "Send your access code first."
        return "That code did not match. Ask the operator for your access code."

    rows = _open_signals()
    if low in ("auto", "automate", "auto mate", "automatic", "auto trading", "autotrade", "auto trade"):
        return _set_auto(session, low)
    if low.startswith(("auto mate ", "automate ", "auto trade ", "autotrade ")):
        return _set_auto(session, low)
    if low in ("auto status", "automation status"):
        return _auto_status(session)
    if low in ("auto off", "automation off", "stop auto", "stop automate", "manual mode"):
        session["auto_trade"] = False
        return "Auto trade is OFF. I will still trail/protect running trades in the background."
    # WhatsApp users should not have to guess exact command words. Keep this
    # intentionally small and deterministic: these are navigation requests,
    # not an invitation to interpret trading instructions loosely.
    if any(word in low for word in ("signal", "setup", "trade available", "available trade")):
        session["step"] = "home"
        return format_signals(rows)
    if any(word in low for word in ("running trade", "open trade", "position", "positions")):
        session["step"] = "close_pick"
        return format_positions(session)
    if any(word in low for word in ("balance", "wallet", "account balance")):
        session["step"] = "home"
        return format_balance(session)
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
        session.pop("plan", None)
        session.pop("quote", None)
        session["step"] = "risk"
        try:
            rec = _live_recommendation(session)
        except (VenueError, OSError) as exc:
            return f"Cannot price this trade yet: {exc}"
        plan = plan_by_id(rec, plan_id)
        if plan is None or not plan.get("policy_ok"):
            return _unavailable_plan(session, rec, plan)
        session["plan"] = plan_id
        session["quote"] = dict(plan)
        session["step"] = "confirm"
        label = PLAN_LABEL[plan_id]
        extra = ""
        if plan:
            target_fit = _target_fit_label(rec)
            target_line = (
                f"\nEstimated TP gain {plan['profit_if_tp_pct']:.2f}% vs daily target "
                f"{plan['target_daily_profit_pct']:.2f}%."
            )
            if target_fit and not plan.get("target_ok"):
                target_line += f"\nSmallest target-fit tier: {target_fit}."
            elif not plan.get("target_ok"):
                target_line += "\nNo allowed tier reaches the daily target on this setup."
            extra = (
                f"\n{plan['leverage']}x · risk {plan['risk_pct']}% · lose ~{plan['loss_if_sl']:.2f}"
                f"{target_line}"
            )
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
        session.pop("signal", None)
        session.pop("quote", None)
        return "Cancelled. *signals* to see setups."
    return "I didn’t understand that. Type *available trades*, *running trades*, *account balance*, *take*, or *help*."
