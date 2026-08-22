"""What has to survive the process dying.

THE PROBLEM THIS SOLVES
-----------------------
A trading process will be killed mid-flight. Laptop sleeps, power goes, an
exception escapes, you press Ctrl-C at the wrong second. The dangerous moment
is between "order sent" and "confirmation received": the position may or may
not exist, and the process has no way to know which.

Restart naively and it re-reads the same closed bar, sees the same breakout,
and sends the same order again. Now you have two positions and half the stops.

Two mechanisms prevent that, and neither relies on getting the shutdown right:

1. DETERMINISTIC CLIENT IDS. The id for an action is derived from what the
   action IS - symbol, bar timestamp, intent - never from a counter or a clock.
   So the retry after a crash computes the *same* id as the attempt that may
   have landed, and the venue rejects the duplicate. Idempotency by
   construction rather than by remembering.

2. A HIGH-WATER MARK PER SYMBOL. Once a bar has been acted on, its timestamp is
   recorded. A restart will not act on that bar again even if the order record
   was lost, because the question "have I already decided on this bar?" is
   answered by durable state rather than by memory.

WHY JSON ON DISK AND NOT A DATABASE
-----------------------------------
The state is a few hundred bytes and one process writes it. A database here
would be ceremony. What it does need is to never be half-written - a truncated
state file after a power cut is worse than none - so writes go to a temp file
and are renamed, which is atomic on every filesystem we care about.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# Explicit codes, not a truncation. "enter" and "exit" both start with 'e',
# and a collision here is not cosmetic: the close would carry the same id the
# entry already used, the venue would reject it as a duplicate, and the
# position would quietly stay open while the log said it had been closed.
ACTION_CODES = {"enter": "n", "exit": "x", "trail": "t", "close": "x"}


def client_id(symbol: str, bar_time: datetime, action: str) -> str:
    """A stable id for one decision.

    Same symbol, same bar, same intent -> same id, forever. That is the whole
    point: it must NOT contain a timestamp of when we sent it, a random suffix,
    or a sequence number, because then a retry would look like a new order and
    the venue would happily fill it twice.

    Kept short and alphanumeric-ish; venues get unhappy about long ids.
    """
    if action not in ACTION_CODES:
        raise ValueError(
            f"Unknown action {action!r}. Add it to ACTION_CODES with a code "
            f"nothing else uses - two actions sharing a code means one of them "
            f"silently fails as a duplicate."
        )
    stamp = int(bar_time.timestamp())
    return f"fxg{stamp}{ACTION_CODES[action]}{symbol.replace('USDT', '')[:8]}".lower()


@dataclass
class State:
    """Everything the runner must not forget."""

    # symbol -> unix seconds of the last bar we acted on
    decided: dict[str, int] = field(default_factory=dict)
    # symbol -> the stop we last placed, as a fallback when the venue's
    # TP/SL endpoint cannot be read
    stops: dict[str, float] = field(default_factory=dict)
    # the equity the trading day opened at, for the daily loss guard
    day: str = ""
    day_open_equity: float = 0.0
    # a halt survives restart on purpose - see `halt`
    halted: bool = False
    halt_reason: str = ""
    path: str = field(default="", repr=False)

    # --- persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "State":
        if not os.path.exists(path):
            return cls(path=path)
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not be silently replaced with an empty
            # one - that would re-enable trading on bars already acted upon.
            # Halt and make a human look.
            return cls(path=path, halted=True,
                       halt_reason=f"state file at {path} is unreadable")
        raw.pop("path", None)
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(path=path, **known)

    def save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if k != "path"}
        # Write-then-rename: a crash mid-write leaves the old file intact
        # rather than a truncated one.
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # --- the questions the runner asks ---------------------------------

    def already_decided(self, symbol: str, bar_time: datetime) -> bool:
        """Have we already acted on this bar for this symbol?"""
        return self.decided.get(symbol, 0) >= int(bar_time.timestamp())

    def mark_decided(self, symbol: str, bar_time: datetime) -> None:
        self.decided[symbol] = int(bar_time.timestamp())

    def roll_day(self, equity: float, now: datetime | None = None) -> bool:
        """Anchor the daily loss guard. True if a new day just started.

        A new day also clears a halt that was caused by the daily loss limit -
        that is the limit's whole design, a cool-off rather than a permanent
        stop. Halts for any other reason survive, because they were not about
        the calendar.
        """
        today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        if self.day == today:
            return False
        self.day = today
        self.day_open_equity = equity
        if self.halted and self.halt_reason.startswith("daily loss"):
            self.halted = False
            self.halt_reason = ""
        return True

    def halt(self, reason: str) -> None:
        """Stop trading, and keep being stopped after a restart.

        Persisting the halt is the point. A guard that trips, kills the
        process, and is then cleared by the restart it caused is not a guard -
        it is a speed bump. Clearing it is a human decision.
        """
        self.halted = True
        self.halt_reason = reason
        self.save()

    def resume(self) -> None:
        self.halted = False
        self.halt_reason = ""
        self.save()
