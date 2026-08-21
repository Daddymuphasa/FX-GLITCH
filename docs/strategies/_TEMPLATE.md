# Strategy name

**Source:** where this came from (YouTube channel, mentor, book, your own idea)
**Markets:** V75 / Boom 1000 / XAUUSD / ...
**Timeframe:** M1 / M5 / M15 / H1
**Status:** untested | coded | backtested | rejected | demo | live

---

## The rules in plain English

Write it as if explaining to someone who has never seen a chart. If you cannot write it
this clearly, it cannot be coded, which usually means the rules are vaguer than they felt.

**Entry (long):**
1.
2.

**Entry (short):**
1.
2.

**Stop loss:** exactly where, measured how

**Take profit:** exactly where

**Position size:** percent of account risked per trade

**Filters — when do I NOT take this trade?**
-

---

## The vague bits

Every strategy from a video has at least one instruction that sounds precise and is not:
"when the trend is strong", "at a key level", "when momentum shifts". List them here and
write down the specific rule you chose to stand in for each. This is where a strategy
secretly becomes a different strategy.

| Vague instruction | What I coded instead |
|---|---|
| | |

---

## Results

| Market | Bars | Trades | Expectancy | Max DD | Noise best | Verdict |
|---|---|---|---|---|---|---|
| | | | | | | |

Command used:

```bash
python run.py my_strategy --sim V75 --bars 20000 --noise-test
```

---

## What I concluded

Be blunt. "This is a coin flip after spread" is a genuinely useful result and saves real
money. A rejected strategy is a successful test.
