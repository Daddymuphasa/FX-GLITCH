# Donchian breakout (trend following)

**Source:** classic systematic trend following — the Turtle Traders, 1983
**Markets tested:** BTCUSD daily, ETHUSD daily, BTCUSD hourly
**Code:** [strategies/donchian_breakout.py](../../strategies/donchian_breakout.py)
**Status:** backtested — promising on daily, dead on hourly

---

## The rules in plain English

**Entry (long):** price closes above the highest high of the previous 55 bars,
and price is above the 200 EMA.

**Entry (short):** price closes below the lowest low of the previous 55 bars,
and price is below the 200 EMA.

**Stop loss:** 2.5 × ATR(14) from entry, then **trailed** — it moves in your
favour as price advances and never moves back against you.

**Take profit:** none, deliberately. The entire edge in trend following lives in
a handful of enormous winners; a fixed target cuts exactly those off.

**Exit:** the trailing stop, or price breaking the opposite 20-bar channel.

**Position size:** 1% of equity risked per trade by default.

---

## The vague bits

| Vague instruction | What I coded instead |
|---|---|
| "trade with the trend" | price on the correct side of the 200 EMA |
| "breakout of recent highs" | highest high of the previous 55 bars, **excluding** the current one |
| "give it room" | 2.5 × ATR(14) |

That exclusion in row two matters more than it looks. The highest high of the
last N bars *including* the current bar is always ≥ the current high, so the
breakout can never fire. It is the most common bug in a breakout backtest and it
silently produces zero trades or, worse, lookahead-poisoned ones.

---

## Results

All runs use `--market binance-spot` (0.1% fee + 0.02% slippage per side,
0.24% per round trip) and default parameters unless stated.

| Market | Bars | Trades | Expectancy | PF | Max DD | Noise best | Verdict |
|---|---|---|---|---|---|---|---|
| BTC 1d, 2015–2026 | 4,016 | 69 | **+0.998 R** | 3.26 | 5.4% | +0.098 R | 10× better than luck |
| ETH 1d, 2017–2026 | 3,208 | 52 | **+0.648 R** | 3.30 | 3.9% | — | holds out-of-sample |
| BTC 1h, 2024–2026 | 16,769 | 317 | **−0.061 R** | 0.86 | 36.5% | — | fees eat it |

```bash
python run.py donchian_breakout --csv data/raw/btcusdt_1d.csv --market binance-spot --noise-test
```

### Parameter sweep — is it curve-fitted?

Entry channel length, everything else fixed:

| entry | 20 | 35 | 45 | 55 | 70 | 90 | 120 |
|---|---|---|---|---|---|---|---|
| expectancy | +0.974 | +0.958 | +1.031 | +0.998 | +0.887 | +0.905 | +0.918 |

A 2-D grid over `entry` × `atr_mult` was positive in **16 of 16** combinations.

That is a broad flat plateau, which is what a real effect looks like. A curve fit
is a lonely spike: brilliant at one setting, losing at its neighbours.

### The timeframe/fee interaction

Same rules, same asset, same costs — only the bar size changes. Daily works,
hourly does not, and the reason is arithmetic rather than anything about trends:

| | daily | hourly |
|---|---|---|
| trades | 69 | 317 |
| cost paid | ~17% of capital | ~76% of capital |
| expectancy | +0.998 R | −0.061 R |

At 0.24% per round trip, 317 trades is 76% of the account handed to the exchange
before the strategy makes a cent. **In crypto, your timeframe is a cost decision
before it is a strategy decision.**

---

## What I concluded

Trend following on daily crypto looks real. It survived every test I could throw
at it: a 10× margin over matched random walks, a flat parameter plateau, and an
untouched second asset.

Three things stop this being a green light:

1. **Sample size.** 69 trades. My own report warns below 100 for good reason —
   and a strategy whose returns come from a few large winners needs *more*
   samples than average, not fewer.

2. **BTC 2015–2026 is one sample path, and it is the friendliest one in
   financial history.** Choosing the asset that went up 36,000× and then finding
   that a long-biased trend system worked on it is partly a discovery about
   trend following and partly a discovery about my choice of asset. The ETH
   result helps but does not eliminate this.

3. **Costs modelled here are optimistic.** Funding on perpetual futures is not
   modelled at all; on positions held for weeks it can exceed trading fees.

### On the buy-and-hold comparison

At the default 1% risk the report says trading "cost you" 36,584% versus holding.
That framing is unfair to the strategy — at 1% risk it is barely deploying
capital, which is why its drawdown is 5.4% against holding's 83.4%. Scaled to
comparable risk:

| risk/trade | return | max DD |
|---|---|---|
| 1% | +94% | 5.4% |
| 5% | +1,773% | 24.4% |
| 10% | +16,382% | 43.2% |
| 15% | +85,596% | 57.9% |
| *buy and hold* | *+36,679%* | *83.4%* |

At matched pain it beats holding on both axes. Read that as *the shape is right*,
not as a recommendation to risk 15% per trade — that column assumes flawless
compounding, perfect fills, and that the next decade resembles the last one.

**Next step before believing any of it:** re-run on data this strategy has never
been fitted to — SOL, a 2021–2022 bear-market-only slice, and a walk-forward
split.
