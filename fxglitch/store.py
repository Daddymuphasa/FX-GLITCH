"""Candles on disk: fetch once, keep forever.

TWO PROBLEMS, ONE ANSWER
------------------------
1. RATE LIMITS. The runner asks for 400 bars per symbol per cycle. On a
   ten-symbol shortlist that is ten multi-page requests every pass, re-fetching
   history that has not changed since the last one - and roughly eight requests
   in two seconds is enough to earn a Cloudflare cooldown lasting minutes. With
   a store, the second cycle fetches the handful of bars that are actually new.

2. HISTORY WE DO NOT HAVE. The exposure layer treats every alt as beta 1.0 to
   BTC because measuring a real beta needs alt price history, and free
   endpoints hand back a few hundred bars. That is not a coding problem and no
   cleverness fixes it. The only fix is to start keeping what we see, which
   makes it a problem that shrinks every day the syncer runs and never shrinks
   on any day it does not.

   Same shape as the open-interest constraint in derivs.py: history only
   accrues from the day you start.

WHY A CLOSED BAR IS TREATED AS IMMUTABLE
----------------------------------------
Once a candle has closed, its four prices are a fact. If a re-fetch reports a
different close for a bar already stored, that is not a correction to accept
quietly - it means something is wrong: the wrong symbol, a different interval,
a venue serving mark price where it served last price before, or a bug here.

So stored bars win, and a disagreement is reported rather than merged. A store
that silently rewrites its own history is worse than no store, because every
backtest run against it afterwards is unreproducible and nothing says so.

FORMAT
------
One CSV per symbol per interval, the same format tools/fetch_crypto.py writes
and fxglitch.data.load_csv reads. Deliberately boring: you can open it in a
spreadsheet, feed it straight to run.py, and diff two copies. A binary format
would be faster and would cost the ability to look.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .data import Candle, Series, load_csv

DEFAULT_ROOT = os.path.join("data", "candles")

INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
    "3d": 259200, "1w": 604800,
}

# Bars re-fetched beyond what is strictly missing. They exist to be compared
# against what we already hold - the overlap is how a mismatch gets caught at
# all. Without it the store would append blindly and never notice it had
# started collecting a different series.
OVERLAP_BARS = 3


class StoreMismatch(RuntimeError):
    """The venue disagrees with history we already recorded."""


@dataclass
class SyncReport:
    symbol: str
    interval: str
    had: int = 0
    fetched: int = 0
    added: int = 0
    total: int = 0
    mismatches: int = 0

    def __str__(self) -> str:
        line = (f"{self.symbol:<14} {self.interval:<4} "
                f"had {self.had:>5}  fetched {self.fetched:>4}  "
                f"new {self.added:>4}  total {self.total:>5}")
        if self.mismatches:
            line += f"   WARNING: {self.mismatches} bars disagreed with history"
        return line


class CandleStore:
    """A directory of candle CSVs, keyed by symbol and interval."""

    def __init__(self, root: str = DEFAULT_ROOT) -> None:
        self.root = root

    def path(self, symbol: str, interval: str) -> str:
        return os.path.join(self.root, interval, f"{symbol.lower()}.csv")

    def symbols(self, interval: str) -> list[str]:
        directory = os.path.join(self.root, interval)
        if not os.path.isdir(directory):
            return []
        return sorted(f[:-4].upper() for f in os.listdir(directory)
                      if f.endswith(".csv"))

    # --- reading and writing -------------------------------------------

    def load(self, symbol: str, interval: str) -> Series:
        path = self.path(symbol, interval)
        if not os.path.exists(path):
            return []
        return load_csv(path)

    def write(self, symbol: str, interval: str, candles: Series) -> str:
        """Replace the file with `candles`, sorted and deduplicated.

        Writes to a temp file and renames, so an interrupted write leaves the
        previous history intact rather than a half-file that load_csv would
        happily read as a shorter series.
        """
        path = self.path(symbol, interval)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ordered = _dedupe(candles)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["time", "open", "high", "low", "close", "volume"])
            for c in ordered:
                writer.writerow([
                    c.time.strftime("%Y-%m-%d %H:%M:%S"),
                    c.open, c.high, c.low, c.close, c.volume,
                ])
        os.replace(tmp, path)
        return path

    def merge(self, symbol: str, interval: str,
              incoming: Series) -> tuple[Series, int, int]:
        """Fold new candles into stored ones.

        Returns (merged series, bars added, bars that disagreed). Stored bars
        always win a conflict - see the module note on immutability.
        """
        existing = self.load(symbol, interval)
        by_time = {c.time: c for c in existing}
        added = mismatches = 0

        for candle in incoming:
            held = by_time.get(candle.time)
            if held is None:
                by_time[candle.time] = candle
                added += 1
            elif not _same_bar(held, candle):
                mismatches += 1

        merged = sorted(by_time.values(), key=lambda c: c.time)
        return merged, added, mismatches

    # --- the operation the runner and the syncer both use ---------------

    def sync(self, venue, symbol: str, interval: str, want: int, *,
             now: datetime | None = None) -> Series:
        """Return `want` bars, fetching only what is missing.

        A cold store fetches the lot. A warm one asks for the bars since its
        newest, plus a small overlap to check the two agree. On a daily
        strategy polled hourly that is the difference between 400 bars fetched
        twenty-four times a day and 400 fetched once.
        """
        report = self.sync_report(venue, symbol, interval, want, now=now)
        series = self.load(symbol, interval)
        return series[-want:] if want else series

    def sync_report(self, venue, symbol: str, interval: str, want: int, *,
                    now: datetime | None = None) -> SyncReport:
        now = now or datetime.now(timezone.utc)
        existing = self.load(symbol, interval)
        report = SyncReport(symbol=symbol, interval=interval, had=len(existing))

        need = self._bars_needed(existing, interval, want, now)
        if need <= 0:
            report.total = len(existing)
            return report

        incoming = venue.candles(symbol, interval, limit=need)
        report.fetched = len(incoming)

        merged, added, mismatches = self.merge(symbol, interval, incoming)
        report.added, report.mismatches, report.total = added, mismatches, len(merged)
        if added or not existing:
            self.write(symbol, interval, merged)
        return report

    def _bars_needed(self, existing: Series, interval: str, want: int,
                     now: datetime) -> int:
        """How many bars to ask the venue for."""
        if not existing:
            return want
        if len(existing) < want:
            # We hold fewer than the strategy needs, so reach back for the lot
            # rather than topping up the recent end and leaving it short. A
            # strategy running on less history than it was tested on is the
            # quiet failure this avoids.
            return want
        seconds = INTERVAL_SECONDS.get(interval)
        if seconds is None:
            return want
        gap = (now - existing[-1].time).total_seconds()
        missing = int(gap // seconds)
        return max(0, missing) + OVERLAP_BARS if missing > 0 else 0


def _same_bar(a: Candle, b: Candle, tolerance: float = 1e-6) -> bool:
    """Do two records of the same timestamp agree on the prices?

    Compared relatively, because a 0.01 absolute difference is noise on a
    78,000 BTC print and a total disagreement on a 0.00003 memecoin.
    """
    for x, y in ((a.open, b.open), (a.high, b.high), (a.low, b.low),
                 (a.close, b.close)):
        scale = max(abs(x), abs(y), 1e-12)
        if abs(x - y) / scale > tolerance:
            return False
    return True


def _dedupe(candles: Series) -> Series:
    by_time = {c.time: c for c in candles}
    return sorted(by_time.values(), key=lambda c: c.time)


def coverage(store: CandleStore, interval: str) -> list[tuple[str, int, datetime, datetime]]:
    """What history exists, per symbol. For deciding what is measurable yet."""
    out = []
    for symbol in store.symbols(interval):
        series = store.load(symbol, interval)
        if series:
            out.append((symbol, len(series), series[0].time, series[-1].time))
    return out
