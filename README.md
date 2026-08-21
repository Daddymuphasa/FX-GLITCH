# FX-GLITCH

A lab for testing trading strategies on **crypto**, **synthetic indices** (V10–V100,
Boom, Crash) and **XAUUSD**, before any money is involved.

You bring the strategies and the market intelligence. This repo turns each one into
code, runs it over thousands of bars, and reports whether it actually has an edge — or
whether it just got lucky.

Two layers:

- **Price action** decides *whether* to trade. Backtested, noise-tested, cost-modelled.
- **Intelligence** (news, macro, flows, positioning) decides *how much*. See
  [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md).

The second layer can only scale position size — it can never invent a trade. Its worst
case is degrading to the first layer, which is the point.

---

## The one thing to understand first

Here is the example strategy, run six times. Same rules, same market, same settings.
The only difference is the random seed used to generate the market:

```
seed 1      expectancy +0.002 R      -3.0%
seed 7      expectancy -0.124 R     -38.4%
seed 42     expectancy +0.145 R     +60.2%
seed 99     expectancy +0.087 R     +28.9%
seed 123    expectancy +0.088 R     +29.2%
seed 500    expectancy -0.040 R     -16.2%
```

That is one strategy looking like a goldmine and a disaster at the same time. Nothing
changed but luck.

This matters more for synthetic indices than for anything else you could trade, because
of what they are. Deriv's volatility indices are **published as random walks** — a random
number generator with a stated annual volatility. V75 is a random walk with 75% volatility.
Boom and Crash are random walks with rare spikes attached.

On a random walk, past prices contain no information about future prices. Support,
resistance, trendlines, head-and-shoulders — all of them *appear* in this data. You can
generate a fake market in [fxglitch/simulate.py](fxglitch/simulate.py) and go find a
textbook chart pattern in it inside a minute. They appear because eyes find shapes in
noise.

So when someone sells you a "V75 strategy," the question is never *does it win?* Everything
wins on some stretch of data. The question is *does it win more than the same rules win on
pure noise?* This repo has a command for exactly that.

None of this means don't trade. It means: measure first, and know which numbers can lie.

---

## Quick start

Nothing to install. It runs on Python 3.10+ and numpy, which you already have.

Get real crypto data — no API key, no account:

```bash
python tools/fetch_crypto.py BTCUSDT --interval 1d --years 11
```

Run a strategy on it with real exchange fees:

```bash
python run.py donchian_breakout --csv data/raw/btcusdt_1d.csv --market binance-spot --noise-test
```

Or with no data at all, on a simulated market:

```bash
python run.py ema_cross --sim V75 --bars 20000
```

Then the honest version of the same test:

```bash
python run.py ema_cross --sim V75 --bars 20000 --noise-test
```

`--noise-test` reruns your rules on 10 randomly generated markets and shows you the range
of results luck alone produces. If your result sits inside that range, you have not found
an edge.

---

## Crypto costs will decide your strategy before your strategy does

Same rules, same asset, same period. Only the fee changes:

| cost model | expectancy | end equity | max DD |
|---|---|---|---|
| no costs (a lie) | −0.029 R | 901 | 27% |
| 0.02% maker | −0.089 R | 766 | 35% |
| 0.1% Binance taker | −0.246 R | 501 | 53% |
| 0.6% Coinbase retail | −0.429 R | 306 | 71% |

And the same interaction, by timeframe — trend following on BTC:

| | daily | hourly |
|---|---|---|
| trades | 69 | 317 |
| fees paid | ~17% of capital | ~76% of capital |
| expectancy | **+0.998 R** | **−0.061 R** |

At 0.24% per round trip, 317 trades hands 76% of the account to the exchange before
you make a cent. **In crypto, your timeframe is a cost decision before it is a
strategy decision.**

Use `--market` so you never guess:

```bash
python run.py --markets          # list every cost preset
```

## Running on real data

Put an OHLC csv in `data/raw/` and point at it. Column names are flexible — `time/date/
timestamp`, `open/high/low/close`, MT5's `<DATE>` style headers all work.

```bash
python run.py ema_cross --csv data/raw/xauusd_m15.csv --spread 0.30
```

**Always pass `--spread`.** It is charged on entry and exit of every trade, and it is
what kills most strategies that looked profitable without it. Use your broker's real
number: roughly `0.20`–`0.30` for XAUUSD in price units.

Other flags:

| Flag | Meaning |
|---|---|
| `--equity 1000` | starting balance |
| `--risk 1.0` | percent of equity risked per trade |
| `--start` / `--end` | trim to a date window, e.g. `--start 2024-06-01` |
| `--set fast=10` | override a strategy parameter, repeatable |
| `--save reports/t.csv` | write every trade to a csv |

---

## How to read a report

Ignore win rate first. It is the most quoted and least useful number here — a strategy can
win 90% of trades and still empty the account on the tenth.

**Expectancy (R per trade)** is the number that decides everything. 1R is the amount you
risked. `+0.15 R` means that over many trades, each one is worth 15% of what you put at
risk. Positive and stable is a business. Negative is a leak, no matter how good the win
rate looks.

**Max drawdown** is the worst peak-to-trough stretch. Look at it and ask honestly whether
you would have kept following the rules through it. Most people would not, which means
the backtest result was never available to them.

**Longest losing streak** is the one to brace for emotionally. Eleven losses in a row is
normal for a perfectly good strategy with a 38% win rate.

Under 100 trades, treat every number as noise.

---

## Adding a strategy

Copy [strategies/ema_cross.py](strategies/ema_cross.py). Each file defines a class with two
methods and ends with `strategy = YourClass`:

```python
class MyStrategy(Strategy):
    name = "What I'm calling this"

    def prepare(self):
        # runs once - precompute indicators over self.candles
        self.ma = ema(closes(self.candles), 50)

    def on_bar(self, i):
        # runs every bar - you may look at bars 0..i and no further
        if self.position is not None:
            return
        if some_condition:
            self.buy(sl=..., tp=..., reason="why")

strategy = MyStrategy
```

Then write the plain-English version in `docs/strategies/` so you know what you were
thinking six months from now.

---

## What the engine will not let you fake

Three rules are enforced in [fxglitch/engine.py](fxglitch/engine.py), because breaking
them is how backtests end up beautiful and useless:

1. **No lookahead.** A signal formed on the close of a bar is filled at the *open of the
   next one*. You cannot trade a candle you have not finished watching.
2. **Worst case wins.** If a bar's range covers both your stop and your target, the engine
   records the **stop**. Reality is not generous about which came first.
3. **Costs are real.** Spread is charged both ways, every trade.

A strategy that still looks good under all three has earned a demo account.

---

## Layout

```
fxglitch/          the engine
  data.py          csv loading, candles
  indicators.py    ema, rsi, atr, bollinger, crosses
  engine.py        backtest loop, position sizing, fills
  metrics.py       expectancy, drawdown, profit factor
  report.py        the report, equity curve, buy-and-hold benchmark
  simulate.py      synthetic market generators
  markets.py       realistic cost presets per exchange
  signals.py       the intelligence layer, point-in-time safe
  factors.py       signals derived from price and volume
  derivs.py        funding rates and open interest - positioning, not price
  news.py          catalyst logging and priors
strategies/        one file per strategy (the code)
tools/
  fetch_crypto.py  real OHLCV, no API key (binance/bybit/yahoo fallback)
  fetch_derivs.py  perp funding + open interest, and a live positioning snapshot
  sweep.py         parameter sweep - the curve-fit detector
  event_study.py   does this catalyst actually have edge?
docs/strategies/   one file per strategy (the explanation)
docs/INTELLIGENCE.md   how the news/macro layer works and why
data/raw/          your csv files (gitignored)
data/events/       catalyst logs
tests/             65 tests - run before trusting anything
run.py             the runner
```

Run the tests:

```bash
python -m unittest discover tests
```

---

## Honest scope

This is a research and learning tool. It does not connect to a broker, it does not place
trades, and a good backtest is evidence — not a promise. Results here are what *would*
have happened on data that already exists, which is the easiest thing in the world to
fit and the hardest thing to repeat.
