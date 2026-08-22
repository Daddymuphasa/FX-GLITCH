"""Look at everything cheaply, so you can afford to look at a few things properly.

THE PROBLEM
-----------
The universe is 625 tradeable USDT perps. Fetching candles for each one is 625+
requests per cycle, and roughly eight requests in two seconds is enough to earn
a Cloudflare cooldown lasting minutes. Per-symbol scanning does not merely run
slowly at this size - it never completes a single pass.

THE SHAPE OF THE FIX
--------------------
One call to the tickers endpoint returns every symbol at once: price, 24h
change, volume. That is the wide net. Rank what comes back, keep a handful, and
spend the expensive per-symbol calls only on those.

    625 symbols  ->  1 request   (screen)
     10 symbols  ->  10 requests (decide)

WHAT THE RANKING IS AND IS NOT
------------------------------
The default rank is turnover, and that is a COST decision, not a prediction.
A thin perp fills badly, gaps through stops, and charges you the spread twice
for the privilege - none of which depends on whether your signal was right. So
the default filter removes symbols you cannot trade cheaply, which is a claim
this repo can defend.

Ranking by biggest 24h mover is available and is NOT the default, because it is
an unmeasured claim about edge dressed up as a filter. Nothing in this repo has
tested whether yesterday's largest movers outperform, and the macro chapter is
a standing reminder of what happens to compelling stories that nobody measured.
If you use it, treat it as a hypothesis to test with tools/event_study.py, not
as a setting that is obviously fine.

The screener chooses what to LOOK at. It never decides to trade. The strategy
still has to fire on the symbols that survive, and the guards still have to
allow it.
"""

from __future__ import annotations

from dataclasses import dataclass

# The docs for this endpoint are behind the same bot check that blocks the
# client, so field names could not be read end to end. Each value is therefore
# looked up across the plausible spellings rather than assumed. If Bitunix
# renames one, the screener loses that field rather than crashing - and
# `Ticker.usable` makes the loss visible instead of silently ranking on zeros.
PRICE_KEYS = ("lastPrice", "last", "close", "markPrice")
CHANGE_KEYS = ("priceChangePercent", "changePercent", "change", "priceChange")
BASE_VOL_KEYS = ("baseVol", "baseVolume", "volume", "vol")
QUOTE_VOL_KEYS = ("quoteVol", "quoteVolume", "turnover", "amount")


def _first(row: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last: float | None = None
    change_pct: float | None = None
    base_volume: float | None = None
    quote_volume: float | None = None

    @property
    def usable(self) -> bool:
        """Do we have enough to judge this symbol at all?

        A symbol we cannot price or size is not a candidate. Ranking it on
        zeros would quietly sort it to the bottom, which looks like a decision
        and is actually a parsing failure.
        """
        return self.last is not None and self.last > 0

    @property
    def turnover(self) -> float:
        """24h traded value in quote currency, however the venue spells it.

        Bitunix inverts its volume field names on the kline endpoint - the one
        labelled "baseVol" holds quote units - so when only one volume figure
        is available it is worth deriving turnover from price rather than
        trusting a label.
        """
        if self.quote_volume is not None and self.last:
            # Guard against the same inversion seen on klines: if "quote"
            # volume is implausibly small next to base volume x price, prefer
            # the derived figure.
            derived = (self.base_volume or 0.0) * self.last
            return max(self.quote_volume, derived) if derived else self.quote_volume
        if self.base_volume is not None and self.last:
            return self.base_volume * self.last
        return 0.0


def parse_tickers(rows) -> list[Ticker]:
    out = []
    for row in rows or []:
        symbol = row.get("symbol") or row.get("s")
        if not symbol:
            continue
        out.append(Ticker(
            symbol=symbol,
            last=_first(row, PRICE_KEYS),
            change_pct=_first(row, CHANGE_KEYS),
            base_volume=_first(row, BASE_VOL_KEYS),
            quote_volume=_first(row, QUOTE_VOL_KEYS),
        ))
    return out


@dataclass
class ScreenRules:
    """What survives the wide net.

    `min_turnover` is the one that matters. 10 million USDT of daily turnover
    is a low bar for a major and an impossible one for the long tail, which is
    the intent: the tail is where a 1% slip on entry and another on exit turns
    a positive expectancy negative before the strategy has done anything wrong.
    """

    min_turnover: float = 10_000_000.0
    top: int = 10
    rank_by: str = "turnover"        # turnover | change | abs_change
    always_include: tuple[str, ...] = ("BTCUSDT",)
    exclude: tuple[str, ...] = ()


def screen(tickers: list[Ticker], instruments: dict, rules: ScreenRules) -> list[Ticker]:
    """Cut the universe down to the handful worth a closer look.

    `always_include` exists for BTC specifically. It is the market's driver, so
    its bar has to be fetched every cycle whether or not it ranks - the regime
    gate needs it even on a day when nothing would trade it.
    """
    keys = {
        "turnover": lambda t: t.turnover,
        "change": lambda t: t.change_pct or 0.0,
        "abs_change": lambda t: abs(t.change_pct or 0.0),
    }
    if rules.rank_by not in keys:
        raise ValueError(f"rank_by must be one of {', '.join(keys)}")
    rank = keys[rules.rank_by]

    forced, pool = [], []
    for t in tickers:
        if t.symbol in rules.exclude:
            continue
        instrument = instruments.get(t.symbol)
        if instrument is None or not instrument.tradeable:
            continue
        if t.symbol in rules.always_include:
            forced.append(t)
            continue
        if not t.usable or t.turnover < rules.min_turnover:
            continue
        pool.append(t)

    pool.sort(key=rank, reverse=True)
    chosen = forced + pool[:max(0, rules.top - len(forced))]

    # BTC first, for the same reason it is first in universe(): whatever reads
    # this list gets the market's driver before it judges anything that follows it.
    chosen.sort(key=lambda t: (t.symbol not in rules.always_include, -rank(t)))
    return chosen


def summarise(tickers: list[Ticker], chosen: list[Ticker], rules: ScreenRules) -> str:
    unusable = sum(1 for t in tickers if not t.usable)
    line = (f"screened {len(tickers):,} symbols -> {len(chosen)} "
            f"(min turnover {rules.min_turnover:,.0f}, by {rules.rank_by})")
    if unusable:
        # Loud on purpose: a wave of unparseable rows means the venue renamed a
        # field, and a screener silently ranking on zeros is worse than one that
        # fails.
        line += f"  WARNING: {unusable:,} rows had no usable price - field names may have changed"
    return line
