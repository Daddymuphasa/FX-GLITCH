# The intelligence layer

Design notes for the part of FX-GLITCH that is not price action.

---

## What 20 August 2026 actually taught us

BTC ran 62,687 → 79,244 in four sessions (+26.4%). Your research identified the
causes, and it looks right:

- **Trigger:** Treasury doubled longer-dated buybacks (~$2bn → ≥$4bn/op) after the
  30-year yield spiked. Liquidity/risk-on signal.
- **Trigger:** White House crypto summit, push for the Clarity Act.
- **Amplifier:** ~$2.7bn of shorts liquidated — forced buying cascade.
- **Amplifier:** spot ETF inflows ~$500M+/day, strongest in months.

Now the uncomfortable part. **Our existing price-action system caught this trade
already, knowing none of it.**

```
08-19  close 69,266 — broke the 55-day high of 66,910 on 2.4× volume
08-20  entry filled at 69,351
08-21  +2.00R open, price 77,325
```

That result reframes what the intelligence layer is for. It is *not* for
predicting the move — nobody outside the room knew the Treasury's decision, and
the 30-year repriced in under a second once it was public. The tape, meanwhile,
told you something was happening for three consecutive days, in public, for free.

So the architecture is:

> **Price action decides WHETHER. Intelligence decides HOW MUCH.**

The signal layer can only scale position size between `min_risk` and `max_risk`,
plus one veto when evidence is strongly opposed. It cannot invent a trade and it
cannot cancel a valid technical signal on a hunch.

That constraint is the feature. The worst case for the intelligence layer is that
it degrades to the plain breakout system, which measures **+0.998R** on daily BTC.
A design whose failure mode is *still works* beats a cleverer one that can talk
itself into a position.

---

## Point-in-time integrity: the thing that kills news systems

Every `Signal` carries two timestamps:

| field | meaning |
|---|---|
| `at` | when the event happened |
| `available_at` | when you could actually have **traded** it |

`SignalFeed.as_of(t)` physically cannot return anything with `available_at > t`.
Safety by construction, not by discipline.

This is not pedantry. It is where these systems die:

- **ETF flows** are dated Tuesday, *published* Wednesday evening. Backtest them
  against Tuesday's close and you get a spectacular, entirely fake edge.
- **Macro prints** get revised months later. A vendor's revised series contains
  the future in every historical bar.
- **A headline timestamped 09:00** hit the wire at 08:59:58 and the algos were
  done by 08:59:58.04.

Bar-derived factors follow the same rule: a bar's volume isn't final until it
closes, so `volume_surge` on Wednesday's bar is `available_at` Thursday. That's
why the system was still bearish (−0.300) on 8/19 and only flipped on 8/20 — it
is not allowed to trade the bar it is looking at.

Nine tests in [tests/test_signals.py](../tests/test_signals.py) enforce this.
If they ever fail, every intelligence result is worthless.

---

## Survivorship bias in narratives

Your summary of the rally is well-sourced and probably correct. It is also, on
its own, worth nothing to a systematic trader — and the reason is worth being
blunt about:

> You are reading an explanation constructed **after** the move, selected
> **because** the move happened.

Nobody publishes *"Treasury doubles buybacks, Bitcoin does nothing."* That
article doesn't exist, so you never see the base rate. Every narrative you
encounter is drawn from the winners' bracket.

The fix isn't ignoring news — news genuinely moves markets. The fix is logging
catalysts **prospectively**, in a fixed schema, **including the duds**, then
measuring. That's [tools/event_study.py](../tools/event_study.py): it compares
forward returns after your catalysts against a random day in the same period.

The trap it exists to catch: BTC's average 3-day return 2015–2026 is positive and
large. Find your catalyst gives +2% over three days, and a random day gives
+2.1%, and your catalyst has *negative* information value.

Today's four events are logged in `data/events/btc.csv`. The tool correctly
refuses to conclude anything from n=1. Log 30+ before believing a number.

---

## On "insider info"

I've built the legal, public version of this, and I want to be straightforward
about the line rather than quietly ignore part of your brief.

**Not built:** anything that sources or acts on material non-public information
obtained from someone breaching a duty of confidence — a treasury announcement
leaked early, an unannounced exchange listing, an ETF decision passed by an
insider. This is prosecuted in crypto, not just equities: *US v. Wahi* (Coinbase
listings, 2022) and the OpenSea case both ended in convictions. It's also
operationally terrible — it concentrates enormous legal risk into exactly the
trades you'd size largest.

**Built instead** — and this is what most traders actually mean by the term:
public data that most retail participants ignore. It's genuinely where the edge
lives, and none of it is illegal.

| Category | What it is | Where it comes from |
|---|---|---|
| `FLOW` | ETF creations/redemptions, exchange netflow, stablecoin mints | Public filings, on-chain |
| `POSITIONING` | Funding rates, open interest, long/short ratios, liquidation levels | Exchange APIs, free |
| `ONCHAIN` | Whale wallet moves, exchange balances, dormancy, unlock schedules | Public ledger |
| `MACRO` | Treasury ops, yields, liquidity, DXY | Public releases |

The 8/19 squeeze is the perfect example: **~$2.7bn of short liquidations was
visible in public open-interest and funding data before and during the cascade.**
Crowded short positioning is not a secret — it's published continuously and most
people don't look. That's the asymmetry worth building on.

`squeeze_fuel` in [fxglitch/factors.py](../fxglitch/factors.py) approximates this
from price alone, confidence capped at 0.6 because it's a proxy. The real
version now exists in [fxglitch/derivs.py](../fxglitch/derivs.py) — see below.

---

## Does the bias actually predict anything?

Measured on 69 BTC daily trades:

| | trades | mean result |
|---|---|---|
| evidence **agreed** with the trade | 48 | **+1.344 R** |
| evidence **disagreed** | 21 | **+0.171 R** |
| difference | | +1.173 R (standard error ±0.655 R) |

About 1.8 standard errors — suggestive, not conclusive. Two caveats that matter
more than the number:

1. **n=69.** Nowhere near enough.
2. **Confounded.** Every factor in that test is price-derived, so the "evidence"
   is partly correlated with the breakout it's judging. `trend_regime` especially.
   Genuinely independent signals are the real test. Funding and open interest
   are now wired (see below); ETF flows and on-chain are not yet.

Note also that Confluence returned +256% vs Donchian's +94.5% but with
*identical* expectancy (+0.987 vs +0.998). **That extra return is position
sizing, not skill.** Bigger average bets, bigger drawdown (9.6% vs 5.4%). Don't
mistake leverage for edge.

---

## Positioning data (built)

`fxglitch/derivs.py` + `tools/fetch_derivs.py`.

This is the first input that is **not** derived from price, which is what makes
it worth more than everything above it. Funding and open interest describe
*positioning*. They can tell you a rally happened on shorts covering rather than
new buyers arriving — something price alone cannot say.

### Reading funding

Positive funding = longs pay shorts = crowd is leaning long.

| funding /8h | meaning |
|---|---|
| +0.01% | Binance baseline. Means nothing. |
| > +0.05% | crowd aggressively long, paying to stay |
| > +0.10% | euphoric — long liquidations become the fuel |
| < −0.05% | heavily short — this is squeeze fuel |

Contrarian at the extremes, meaningless in the middle. Crowded positioning is
not a prediction that price falls; it says that *if price rises anyway*,
somebody is forced to buy.

### Open interest against price — the four quadrants

| price | OI | reading |
|---|---|---|
| UP | UP | new longs — genuine trend, real money |
| UP | **DOWN** | **short squeeze** — violent but exhausts itself |
| DOWN | UP | new shorts — genuine downtrend |
| DOWN | DOWN | longs liquidating — capitulation, often a low |

Row two is the whole point of the module. A rally on rising OI and a rally on
falling OI look **identical on a chart** and mean opposite things.

### Live snapshot

```bash
python tools/fetch_derivs.py --snapshot
```

Works with no API key via CoinGecko, reachable on almost any network. Reading it
today, right after the +26% run:

```
TOTAL open interest        40,375,599,698
Average funding   +0.0081% per 8h  (+8.9% annualised)

READ: funding is at baseline. Positioning is NOT crowded either
way, so it carries no contrarian information right now.
```

That is genuinely useful: a 26% rally that did **not** leave the book crowded
long. Had funding been at +0.10% after that run, it would read as a long-squeeze
warning instead.

### The data constraint you cannot engineer around

| | availability |
|---|---|
| funding history | Binance back to 2019, free and complete |
| **open interest** | **free endpoints return ~30 days only** |

So funding factors can be backtested properly today. **OI factors cannot.** Any
OI result from 30 days is an anecdote, and `run.py` prints a warning below six
months rather than letting you forget. `fetch_derivs.py` appends on each run —
schedule it daily and you build real history from today forward.

The 20 tests in [tests/test_derivs.py](../tests/test_derivs.py) verify the
classification logic on synthetic series. They prove the code is *correct*.
They prove nothing about edge — that needs real data.

---

## Next, in priority order

1. **Schedule `fetch_derivs.py --oi` daily.** Nothing else unlocks OI backtesting;
   history only accrues from the day you start.
2. **Backtest the funding factors on real history** — this is possible *now*, back
   to 2019, and is the first genuinely independent test of the whole thesis.
3. **ETF flow log.** Published daily, `available_at` = publication + 1 day.
4. **Prospective catalyst logging** — every candidate, *including* the duds.
5. **Replace the priors in `news.py`** with measured values from event studies.
6. **Walk-forward validation** — fit weights on 2015–2022, test on 2023–2026.
