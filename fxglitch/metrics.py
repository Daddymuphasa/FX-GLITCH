"""Turning a list of trades into an honest verdict.

The number most beginners chase is win rate. It is close to meaningless on its
own: a strategy that wins 90% of the time and loses 20x on the tenth trade is
a slow-motion account wipe. The numbers that decide whether you survive are
EXPECTANCY (average R per trade) and MAX DRAWDOWN (the worst stretch you would
have had to sit through without quitting).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from .engine import BacktestResult, Trade, LONG


@dataclass
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    return_pct: float = 0.0

    expectancy_r: float = 0.0       # average R per trade - the key number
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0
    total_r: float = 0.0

    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    longest_losing_streak: int = 0
    longest_winning_streak: int = 0

    starting_equity: float = 0.0
    final_equity: float = 0.0

    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _streaks(flags: list[bool]) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def max_drawdown(curve: list[float]) -> tuple[float, float]:
    """Returns (worst peak-to-trough drop in %, and in cash)."""
    if not curve:
        return 0.0, 0.0
    peak = curve[0]
    worst_pct = worst_abs = 0.0
    for v in curve:
        peak = max(peak, v)
        drop = peak - v
        worst_abs = max(worst_abs, drop)
        if peak > 0:
            worst_pct = max(worst_pct, drop / peak * 100.0)
    return worst_pct, worst_abs


def analyse(result: BacktestResult) -> Stats:
    closed: list[Trade] = [t for t in result.trades if not t.is_open]
    s = Stats(
        starting_equity=result.starting_equity,
        final_equity=result.final_equity,
    )
    s.trades = len(closed)
    if not closed:
        return s

    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    s.wins, s.losses = len(wins), len(losses)
    s.breakeven = s.trades - s.wins - s.losses
    s.win_rate = s.wins / s.trades * 100.0

    s.gross_profit = sum(t.pnl for t in wins)
    s.gross_loss = abs(sum(t.pnl for t in losses))
    s.net_profit = s.gross_profit - s.gross_loss
    s.profit_factor = (
        s.gross_profit / s.gross_loss if s.gross_loss > 0
        else float("inf") if s.gross_profit > 0 else 0.0
    )
    if s.starting_equity > 0:
        s.return_pct = (s.final_equity - s.starting_equity) / s.starting_equity * 100.0

    rs = [t.r_multiple for t in closed]
    s.total_r = sum(rs)
    s.expectancy_r = s.total_r / s.trades
    s.best_trade_r = max(rs)
    s.worst_trade_r = min(rs)
    s.avg_win_r = sum(t.r_multiple for t in wins) / len(wins) if wins else 0.0
    s.avg_loss_r = sum(t.r_multiple for t in losses) / len(losses) if losses else 0.0

    s.max_drawdown_pct, s.max_drawdown_abs = max_drawdown(result.equity_curve)
    outcomes = [t.pnl > 0 for t in closed]
    s.longest_winning_streak = _streaks(outcomes)
    s.longest_losing_streak = _streaks([not o for o in outcomes])

    longs = [t for t in closed if t.direction == LONG]
    shorts = [t for t in closed if t.direction != LONG]
    s.long_trades, s.short_trades = len(longs), len(shorts)
    if longs:
        s.long_win_rate = sum(1 for t in longs if t.pnl > 0) / len(longs) * 100.0
    if shorts:
        s.short_win_rate = sum(1 for t in shorts if t.pnl > 0) / len(shorts) * 100.0

    return s


def verdict(s: Stats) -> str:
    """A blunt read on the stats. Not advice - a sanity filter."""
    if s.trades == 0:
        return "NO TRADES - the rules never triggered. Loosen them or check your data."
    if s.trades < 30:
        return (
            f"TOO FEW TRADES ({s.trades}) - anything you conclude from this is noise. "
            "Get at least 100 before you believe a single number here."
        )
    notes = []
    if s.expectancy_r <= 0:
        notes.append(
            f"NEGATIVE EDGE: {s.expectancy_r:+.3f}R per trade. This loses money by design, "
            "not by bad luck."
        )
    elif s.expectancy_r < 0.05:
        notes.append(
            f"MARGINAL: {s.expectancy_r:+.3f}R per trade. Real spread and slippage will "
            "likely eat this."
        )
    else:
        notes.append(f"POSITIVE EDGE: {s.expectancy_r:+.3f}R per trade over {s.trades} trades.")

    if s.max_drawdown_pct > 40:
        notes.append(
            f"BRUTAL DRAWDOWN: {s.max_drawdown_pct:.1f}%. Be honest about whether you would "
            "have kept trading through that."
        )
    if s.longest_losing_streak >= 8:
        notes.append(
            f"You would have lost {s.longest_losing_streak} in a row at some point. "
            "Plan for it emotionally before it happens."
        )
    return " ".join(notes)
