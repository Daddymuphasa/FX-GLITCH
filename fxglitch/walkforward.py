"""Walk-forward validation - the test a parameter sweep cannot pass.

tools/sweep.py tells you which setting worked best on the data you have. That
number is worthless as a forecast, and the reason is not subtle: you chose the
setting AFTER seeing the answer. Every backtest that reports "we optimised the
parameters and got +1.2R" is quoting a number that was never available to the
trader who had to pick before the bars printed.

Walk-forward removes exactly that privilege. Fit on a window, then trade the
window AFTER it, having seen none of it. Roll forward and repeat. The trades
that come out the other side are the only ones in this repo produced by a rule
that did not know its own future.

Two numbers matter when you read the result:

    OPTIMISM GAP    in-sample best minus out-of-sample actual. This is what
                    optimisation flatters you by. On a real edge it is small.
                    On a curve fit the in-sample number stays glorious and the
                    out-of-sample one collapses to zero or below.

    PARAMETER DRIFT how often the winning setting changed between folds. A
                    setting that jumps 20 -> 120 -> 35 every fold is not being
                    discovered, it is being fitted to noise. There is nothing
                    to carry forward.

WHY THIS MODULE EXISTS SEPARATELY FROM THE CLI
----------------------------------------------
Two pieces of the mechanics are easy to get quietly wrong, so they live here
where tests can reach them:

1. WARM-UP. A 200-bar EMA needs 200 bars. Hand a strategy only the test window
   and its indicators start blind, so the first stretch of every fold gets
   different rules from the rest. Each fold is therefore run over
   warmup + test bars, and trades entered before the window opens are
   discarded rather than counted.

2. RE-SIZING. Position size is a percent of CURRENT equity, so the discarded
   warm-up trades would otherwise leave every later trade sized off an equity
   path that includes results we just said do not count. trim_result() rebuilds
   the sequence from the fold's own starting equity.

   That rebuild is exact, not an approximation, and only because of a property
   of this engine: size affects nothing except cash. Entries, exits, stops and
   fills are identical whatever the size, so rescaling a trade after the fact
   gives precisely the trade a differently-funded account would have taken.
   The one exception is a wipe-out - if equity reaches zero the engine stops -
   and that is handled explicitly below.

   The rebuilt equity curve steps at each trade exit rather than each bar, so
   the drawdown it reports is closed-trade drawdown. It cannot see a position
   that fell 30% intrabar and recovered before the stop. Treat fold drawdowns
   as a floor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .engine import BacktestResult, Trade


@dataclass(frozen=True)
class Fold:
    """One train/test pair, as indexes into the candle series."""

    index: int
    train_start: int
    train_end: int      # exclusive; also where the test window begins
    test_start: int
    test_end: int       # exclusive
    warmup_start: int   # test_start minus warm-up bars, clamped at 0

    @property
    def train_bars(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_bars(self) -> int:
        return self.test_end - self.test_start


def make_folds(n_bars: int, folds: int = 5, min_train_frac: float = 0.4,
               rolling: bool = False, warmup: int = 250) -> list[Fold]:
    """Split a series into sequential train/test pairs.

    The first `min_train_frac` of the data is training only - nothing is ever
    tested on it, because there is no earlier data to have fitted on. What
    remains is divided into `folds` equal test windows.

    anchored (default)  train on everything before the test window. More data
                        each fold, and the distant past keeps a vote.
    rolling             train on a fixed-length recent window. Adapts to regime
                        change, at the cost of a smaller sample every time.

    Neither is obviously right. Run both: a result that survives only one of
    them is telling you something.
    """
    if folds < 1:
        raise ValueError("folds must be at least 1")
    if not 0.0 < min_train_frac < 1.0:
        raise ValueError("min_train_frac must be between 0 and 1")
    if warmup < 0:
        raise ValueError("warmup cannot be negative")

    first_train = int(n_bars * min_train_frac)
    test_total = n_bars - first_train
    test_len = test_total // folds
    if first_train < 1 or test_len < 1:
        raise ValueError(
            f"{n_bars} bars will not split into {folds} folds with "
            f"{min_train_frac:.0%} held back for training. Use fewer folds "
            f"or more data."
        )

    out: list[Fold] = []
    for k in range(folds):
        test_start = first_train + k * test_len
        # The last fold absorbs the remainder so no bars are silently dropped.
        test_end = n_bars if k == folds - 1 else test_start + test_len
        train_start = max(0, test_start - first_train) if rolling else 0
        out.append(Fold(
            index=k + 1,
            train_start=train_start,
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
            warmup_start=max(0, test_start - warmup),
        ))
    return out


def _resize(trades: list[Trade], equity: float, risk_pct: float):
    """Replay trades onto an account of `equity`, re-sizing each as we go."""
    kept: list[Trade] = []
    curve: list[float] = []
    times: list[datetime] = []
    for t in trades:
        scale = (equity * risk_pct / 100.0) / t.risk_amount if t.risk_amount > 0 else 1.0
        sized = replace(t, size=t.size * scale, risk_amount=t.risk_amount * scale)
        equity += sized.pnl
        kept.append(sized)
        curve.append(equity)
        times.append(sized.exit_time or sized.entry_time)
        if equity <= 0:
            break  # the account is gone; the engine would have stopped here too
    return kept, curve, times, equity


def trim_result(result: BacktestResult, from_time: datetime, *,
                starting_equity: float, risk_pct: float) -> BacktestResult:
    """Keep only trades entered at or after `from_time`, re-sized from scratch.

    Used to strip the warm-up trades out of a fold. See the module docstring
    for why the rescale is exact and what the rebuilt curve can and cannot see.
    """
    inside = [t for t in result.trades
              if not t.is_open and t.entry_time >= from_time]
    kept, curve, times, _ = _resize(inside, starting_equity, risk_pct)
    return BacktestResult(
        strategy=result.strategy,
        symbol=result.symbol,
        trades=kept,
        equity_curve=curve or [starting_equity],
        times=times,
        starting_equity=starting_equity,
        params=dict(result.params),
    )


def combine(results: list[BacktestResult], *, starting_equity: float,
            risk_pct: float) -> BacktestResult:
    """Chain fold results into one continuous account.

    This is the honest headline: what the whole procedure - refit, re-pick,
    trade forward - would have done to a single balance, in order.
    """
    flat: list[Trade] = [t for r in results for t in r.trades]
    kept, curve, times, _ = _resize(flat, starting_equity, risk_pct)
    return BacktestResult(
        strategy=results[0].strategy if results else "?",
        symbol=results[0].symbol if results else "?",
        trades=kept,
        equity_curve=curve or [starting_equity],
        times=times,
        starting_equity=starting_equity,
    )


def drift(picks: list[dict]) -> float:
    """Fraction of fold-to-fold handovers where the chosen parameters changed.

    0.0 means the same setting won every time - the shape of something real.
    1.0 means it never won twice running, which means you are not carrying a
    setting forward, you are picking a new one out of noise each fold.
    """
    if len(picks) < 2:
        return 0.0
    changes = sum(1 for a, b in zip(picks, picks[1:]) if a != b)
    return changes / (len(picks) - 1)
