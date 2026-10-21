"""Offline historical weekly-target audit; never connects to an exchange."""

import argparse
import importlib
import json
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fxglitch.data import load_csv
from fxglitch.engine import Backtest
from fxglitch.metrics import analyse


def weekly_returns(result) -> list[float]:
    """ISO-week close-to-close equity changes, excluding both partial edges."""
    closes = {}
    for stamp, equity in zip(result.times, result.equity_curve):
        closes[stamp.isocalendar()[:2]] = equity
    values = list(closes.values())
    return [(end / start - 1) * 100 for start, end in zip(values[:-2], values[1:-1]) if start > 0]


def audit(paths, *, fee=0.06, slippage=0.02, risk=1.0, target=40.0) -> dict:
    rows = []
    for path in paths:
        candles = load_csv(str(path))
        for name in ("ema_cross", "donchian_breakout", "squeeze_breakout", "confluence"):
            cls = importlib.import_module("strategies." + name).strategy
            # No dated intelligence feed was supplied, so compare fixed risk.
            strategy = cls(min_risk=risk, max_risk=risk) if name == "confluence" else cls()
            result = Backtest(candles, strategy, risk_pct=risk, fee_pct=fee,
                              slippage_pct=slippage, symbol=path.stem).run()
            stats = analyse(result)
            weeks = weekly_returns(result)
            rows.append({
                "data": str(path), "strategy": name,
                "start": candles[0].time.isoformat(), "end": candles[-1].time.isoformat(),
                "trades": stats.trades, "return_pct": stats.return_pct,
                "max_drawdown_pct": stats.max_drawdown_pct, "expectancy_r": stats.expectancy_r,
                "weeks": len(weeks), "profitable_weeks": sum(w > 0 for w in weeks),
                "target_weeks": sum(w >= target for w in weeks),
                "worst_week_pct": min(weeks, default=0), "best_week_pct": max(weeks, default=0),
                "median_week_pct": median(weeks) if weeks else 0,
            })
    return {
        "weekly_target_pct": target, "risk_pct": risk,
        "assumed_fee_pct_per_side": fee, "assumed_slippage_pct_per_side": slippage,
        "limitations": [
            "Historical replay of research strategies, not the live Telegram signal strategy.",
            "Fees and slippage are assumptions, not account-specific exchange rates.",
            "Funding, liquidity constraints, liquidations and intrabar execution are not modelled.",
            "No optimisation or unseen-data validation; confluence has no intelligence feed.",
            "Weekly returns include marked open P/L; first and last weeks are excluded.",
            "Historical returns do not guarantee any future weekly target.",
        ],
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fee", type=float, default=0.06)
    parser.add_argument("--slippage", type=float, default=0.02)
    parser.add_argument("--risk", type=float, default=1.0)
    args = parser.parse_args()
    result = audit(args.csv, fee=args.fee, slippage=args.slippage, risk=args.risk)
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    for row in result["results"]:
        print(f"{Path(row['data']).stem:16} {row['strategy']:19} return {row['return_pct']:+8.2f}% "
              f"DD {row['max_drawdown_pct']:6.2f}% target {row['target_weeks']}/{row['weeks']} weeks "
              f"best {row['best_week_pct']:+6.2f}%")


if __name__ == "__main__":
    main()
