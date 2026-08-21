"""Printing results in a form you can actually read at a glance."""

from __future__ import annotations

import csv
import os

from .data import Series
from .engine import BacktestResult
from .metrics import Stats, analyse, max_drawdown, verdict


def buy_and_hold(candles: Series, starting_equity: float = 1000.0) -> dict:
    """What you would have made doing nothing but buying on day one.

    In crypto this is the benchmark that actually matters. BTC has had multi-year
    stretches returning several hundred percent; a strategy that turns 1000 into
    1400 over that same window did not make you money, it cost you money and gave
    you a job. Always ask: did the trading beat the sitting still?

    It also reports the drawdown, because holding is not free either - you have
    to survive it.
    """
    if len(candles) < 2:
        return {"return_pct": 0.0, "max_dd_pct": 0.0, "final": starting_equity}

    first, last = candles[0].close, candles[-1].close
    units = starting_equity / first
    curve = [c.close * units for c in candles]
    dd, _ = max_drawdown(curve)
    return {
        "return_pct": (last - first) / first * 100.0,
        "max_dd_pct": dd,
        "final": last * units,
    }


def _bar(pct: float, width: int = 24) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    return "#" * filled + "." * (width - filled)


def summary(result: BacktestResult, stats: Stats | None = None,
            benchmark: dict | None = None) -> str:
    s = stats or analyse(result)
    L = []
    L.append("=" * 62)
    L.append(f" {result.strategy}  on  {result.symbol}")
    if result.params:
        L.append(f" params: {result.params}")
    if result.times:
        L.append(f" period: {result.times[0]:%Y-%m-%d} -> {result.times[-1]:%Y-%m-%d}"
                 f"  ({len(result.times)} bars)")
    L.append("=" * 62)

    L.append("")
    L.append(" THE ONLY TWO NUMBERS THAT MATTER FIRST")
    L.append(f"   Expectancy      {s.expectancy_r:+.3f} R per trade")
    L.append(f"   Max drawdown    {s.max_drawdown_pct:.1f}%   ({s.max_drawdown_abs:,.2f})")

    L.append("")
    L.append(" RESULT")
    L.append(f"   Start equity    {s.starting_equity:,.2f}")
    L.append(f"   End equity      {s.final_equity:,.2f}   ({s.return_pct:+.1f}%)")
    L.append(f"   Net profit      {s.net_profit:,.2f}")
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    L.append(f"   Profit factor   {pf}      (above 1.30 is worth a look)")
    L.append(f"   Total R         {s.total_r:+.1f}")

    L.append("")
    L.append(" TRADES")
    L.append(f"   Taken           {s.trades}")
    L.append(f"   Win rate        {s.win_rate:.1f}%  [{_bar(s.win_rate)}]")
    L.append(f"   Won / lost      {s.wins} / {s.losses}"
             + (f"  (+{s.breakeven} flat)" if s.breakeven else ""))
    L.append(f"   Avg win         {s.avg_win_r:+.2f} R")
    L.append(f"   Avg loss        {s.avg_loss_r:+.2f} R")
    L.append(f"   Best / worst    {s.best_trade_r:+.2f} R / {s.worst_trade_r:+.2f} R")
    L.append(f"   Longest streak  {s.longest_winning_streak} wins,"
             f" {s.longest_losing_streak} losses in a row")

    if s.long_trades and s.short_trades:
        L.append("")
        L.append(" DIRECTION SPLIT")
        L.append(f"   Longs           {s.long_trades} trades, {s.long_win_rate:.1f}% win")
        L.append(f"   Shorts          {s.short_trades} trades, {s.short_win_rate:.1f}% win")

    if benchmark:
        L.append("")
        L.append(" VS DOING NOTHING")
        L.append(f"   Buy and hold    {benchmark['return_pct']:+.1f}%"
                 f"   (drawdown {benchmark['max_dd_pct']:.1f}%)")
        L.append(f"   Your strategy   {s.return_pct:+.1f}%"
                 f"   (drawdown {s.max_drawdown_pct:.1f}%)")
        edge = s.return_pct - benchmark["return_pct"]
        if edge > 0:
            L.append(f"   -> trading added {edge:+.1f}% over holding")
        else:
            L.append(f"   -> trading COST you {edge:.1f}% versus just holding")

    L.append("")
    L.append(" VERDICT")
    for line in _wrap(verdict(s), 56):
        L.append(f"   {line}")
    L.append("=" * 62)
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def equity_sparkline(result: BacktestResult, width: int = 60, height: int = 12) -> str:
    """A quick ASCII equity curve. Shape tells you more than the final number."""
    curve = result.equity_curve
    if len(curve) < 2:
        return "(not enough data for a curve)"

    step = max(1, len(curve) // width)
    sampled = curve[::step][:width]
    lo, hi = min(sampled), max(sampled)
    if hi == lo:
        hi = lo + 1

    grid = [[" "] * len(sampled) for _ in range(height)]
    for x, v in enumerate(sampled):
        y = height - 1 - round((v - lo) / (hi - lo) * (height - 1))
        grid[y][x] = "*"

    # Mark the starting equity level so you can see where you were underwater.
    base_y = height - 1 - round((result.starting_equity - lo) / (hi - lo) * (height - 1))
    if 0 <= base_y < height:
        for x in range(len(sampled)):
            if grid[base_y][x] == " ":
                grid[base_y][x] = "-"

    out = [" EQUITY CURVE"]
    for y, row in enumerate(grid):
        label = f"{hi:>10,.0f}" if y == 0 else f"{lo:>10,.0f}" if y == height - 1 else " " * 10
        out.append(f" {label} |" + "".join(row))
    out.append(" " * 12 + "+" + "-" * len(sampled))
    return "\n".join(out)


def trade_log(result: BacktestResult, limit: int | None = 20) -> str:
    trades = result.trades if limit is None else result.trades[:limit]
    if not trades:
        return " (no trades)"
    L = [" TRADE LOG" + (f"  (first {len(trades)} of {len(result.trades)})"
                        if limit and len(result.trades) > limit else "")]
    L.append(f" {'#':>3} {'side':<5} {'entry time':<16} {'entry':>10} {'exit':>10} "
             f"{'R':>7}  reason")
    for n, t in enumerate(trades, 1):
        L.append(
            f" {n:>3} {t.side:<5} {t.entry_time:%Y-%m-%d %H:%M} "
            f"{t.entry_price:>10.5g} {(t.exit_price or 0):>10.5g} "
            f"{t.r_multiple:>+7.2f}  {t.exit_reason}"
        )
    return "\n".join(L)


def save_trades_csv(result: BacktestResult, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["#", "side", "entry_time", "entry_price", "exit_time", "exit_price",
                    "size", "sl", "tp", "pnl", "r_multiple", "entry_reason", "exit_reason"])
        for n, t in enumerate(result.trades, 1):
            w.writerow([n, t.side, t.entry_time, f"{t.entry_price:.6f}", t.exit_time,
                        f"{(t.exit_price or 0):.6f}", f"{t.size:.6f}", t.sl, t.tp,
                        f"{t.pnl:.2f}", f"{t.r_multiple:.4f}",
                        t.entry_reason, t.exit_reason])
    return path


def full_report(result: BacktestResult) -> str:
    s = analyse(result)
    return "\n\n".join([summary(result, s), equity_sparkline(result), trade_log(result)])
