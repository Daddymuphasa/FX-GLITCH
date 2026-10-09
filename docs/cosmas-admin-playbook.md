# Cosmas admin playbook (FX GLITCH knowledge base)

**Status:** Still thin. Upgraded 2026-10-01 after a second pass over public Telegram, the live desk, GitHub, and the local FX-GLITCH inbox. **0 trades are proven winners** (TP touched before SL). Do not treat this as an edge.

**Built:** 2026-09-29, revised 2026-10-01 (Africa/Lagos)  
**Project:** FX-GLITCH (`/workspace/fx-glitch`, live desk https://fxglitch.xyz)  
**Watched chat:** `Cosmas trading/forex signals.` (`-1002004401199`)  
**Public handle:** https://t.me/cosmasanalyst — title matches, about **6,290 members**. It is a **group**, not a channel. Telegram's public preview (`t.me/s/cosmasanalyst` and `/cosmasanalyst/<id>`) returns the welcome blurb only. **No message widgets, no history.**

---

## 1. Data sources used and what blocked a full study

| Source | Result |
|---|---|
| Public `t.me/s/cosmasanalyst` and message URLs | Group exists. **History is not published.** Preview is the Bybit-era welcome text only. |
| Web / GitHub search for mirrored Cosmas posts | No extra signal texts. LinkedIn post by Elochukwu Cosmas (2026-07) only links `t.me/cosmasanalyst`. Bitunix page `activity/exclusive/Cosmas0404` is an ended deposit bonus, not signals. |
| `https://fxglitch.xyz/live-signals.json` | Still the **2026-09-06** PIPPIN snapshot. |
| `https://fxglitch.xyz/api/signals` and `/api/inbox` | Demo BTCUSDT only. Not Cosmas. |
| `https://fxglitch.xyz/api/telegram` | `signed_out` / operator only. Not used. |
| Box + GitHub `Daddymuphasa/FX-GLITCH` | Same PIPPIN snapshot. Tests add KAITO / PONS / PIEVERSE **fixtures** (voice only, no post time). |
| Local `Documents\GitHub\FX-GLITCH\data\inbox.json` | **6,703 bytes, last write 2026-09-06 19:41 Lagos.** Same 11 rows as the box copy. Listener did not keep later messages. |
| Local `public\live-signals.json` | Same Sep 6 book (27 KB). No new Cosmas rows. |
| Local `data\protection\bitunix-*.json` | Equity snapshots only. No fills. |
| Chat export search under Documents / Downloads / Desktop | **No** Telegram `ChatExport` / `result.json` / `messages.html`. Downloads\Telegram Desktop is media, not an export. |
| Secrets | Not read. No `.env`, `telegram.session`, or `session.secret` copied into this file. |

**Blocker:** Cosmas posts in a **private-history group**. Telegram will not serve that history without a logged-in member, and the only session-free copies we have stop on **2026-09-06**.

**Single best next path:** a Telegram Desktop **Export chat history** (JSON or HTML) of `Cosmas trading/forex signals.`, saved as a file (for example `Documents\GitHub\FX-GLITCH\data\ChatExport`). Public `t.me` will not work for this group. Do not paste API keys or the session file into chat.

---

## 2. Real messages vs fixtures

### Real Telegram rows (chat `-1002004401199`, 2026-09-06 only)

| id | Lagos time | Kind | Text (trimmed) |
|---|---|---|---|
| `:21566` | 2026-09-06 01:23 | **Entry** | Pippin/usdt, Long, Entry CMP, Tp 0.8, Sl 0.01300, Leverage 7x, "Night research let's go" |
| `:21568` | 2026-09-06 18:19 | Hold update | "Retracement is part of the market" / Pons, Kaito, Marscoin, Pippin / Holding / "Check out your profit on kaito" |
| `:21569` | 2026-09-06 18:40 | Noise | "Educational contents" |
| `:21570` | 2026-09-06 18:44 | Noise | Naira exchange promo (not a trade) |

Paste duplicates of PIPPIN and a demo BTCUSDT long are **not** Cosmas messages.

**Real Cosmas messages found: 4. Real entry signals: 1. Successful (TP before SL): 0.**

### Fixtures (parser tests — not dated group posts)

| Symbol | Text shape | Why it is not scored as a winner |
|---|---|---|
| KAITOUSDT | Long, CMP, Tp 1.5, Sl 0.2500, 10x | No post time. Bitunix daily **never printed 1.5 in 2026** (July high **1.3706**). Oct 2025 high was 1.5036, before any Sep-6 "profit" claim. |
| PONSUSDT | "has a whale's", "Am targeting long", Tp 1.5, Sl 0.4400, 10x. No CMP. | Listed 2026-09-02. High **0.9788**, low **0.3566**. TP 1.5 **never traded**. SL 0.44 was traded through. |
| PIEVERSEUSDT | "Adjust your sl to $1" plus Tp 0.3 / Sl 0.93 | Stop management, not a new entry. Sep 2026 range **1.0075–2.0321**, so "$1" sat on that month's low. Original entry unknown. |
| MARSCOINUSDT | Named in the hold post only | Listed 2026-09-02. Sep range **0.052–0.268**. No SL/TP in any text. |

---

## 3. The one real trade, scored on Bitunix public klines

**PIPPINUSDT long** — message `:21566`  
Posted **2026-09-06 00:23 UTC** (01:23 Lagos).  
Desk mark at ingest: **0.01977**. Entry type: **CMP (market)**.  
SL **0.013**. TP **0.8**. Leverage **7x**.

Hourly Bitunix futures from the post through the **2026-10-01 00:00 UTC** bar (01:00 Lagos):

| Check | Number |
|---|---|
| Bars | 600 hourly |
| High | **0.02028** |
| Low | **0.01626** |
| Last close | 0.01886 |
| TP 0.8 touched? | **No** |
| SL 0.013 touched? | **No** |
| Best move vs 0.01977 | **+2.6%** |
| Worst move vs 0.01977 | **−17.7%** |
| Verdict | **Open / failed to pay.** Not a successful trade. |

Where the levels sit on older price (this is placement style, not a win):

| Level | vs the signal | vs history |
|---|---|---|
| SL 0.013 | **34.2% under** the 0.01977 mark | **5.9% above** the June 2026 low **0.01227**. Not a tight ATR stop. |
| TP 0.8 | **40.5×** the mark (~39R if risk is the 34% stop) | **88% of** the Feb 2026 high **0.9076**, rounded down to one digit. Dec 2025 high was 0.75. |

So the only real signal is a **market long on a washed-out alt**, stop under the base near the cycle low, target a **round number just under the prior cycle high**. It has not worked as of 2026-10-01.

The Sep 6 line "profit on kaito" is an admin claim, not a fill. KAITO that month traded **0.279–0.376**. A 1.5 target was not in range. Treat the claim as **unverified mark-to-market talk**.

---

## 4. TP / SL rules in plain language (from the texts we actually have)

Confidence is on the **style**, not on profitability.

1. **Entry is market.** The only dated signal says `Entry CMP`. No limit ladder. (High confidence, n=1 live + KAITO fixture.)
2. **One TP, one SL, written as bare numbers.** `Tp 0.8` / `Sl 0.01300`. No partials in the live text. (High for the live post. Multi-TP is only in the parser, not in Cosmas text.)
3. **Stop is a wide base, not a percent scalp.** PIPPIN stop is **~34% under** the mark and **~6% above** the prior multi-month low (0.01227). Hypothesis: he parks SL at/just beyond the structure low that would kill the "this base holds" idea.
4. **TP is a round prior-cycle high, not 2R.** 0.8 is one significant figure, near 0.75–0.91 historical highs, about **40×** entry. KAITO fixture TP **1.5** matches the Oct 2025 high **1.50** more than any R multiple. PONS fixture TP **1.5** had **no** prior high (new coin, high since listing 0.98) — so on listings the target can just be a **round number above the spike**, not a measured move.
5. **Leverage in the group text is 7x–10x.** Live PIPPIN **7x**. Fixtures **10x**. That is the posted number, not a proven sizing rule.
6. **Direction in the sample is long only.** No Cosmas short text exists in what we have. Sample is too small to call that a rule.
7. **After entry he talks hold, not exit.** "Retracement is part of the market" + a named bag + "Holding". No time stop.
8. **On a runner he may tell people to lift the stop.** Pieverse "adjust your sl to $1" lines up with September's low **1.0075**. That is a **lock near the new floor**, not a new trade. The same message's Tp 0.3 / Sl 0.93 should not be opened as a fresh long.
9. **Ignore education, Naira/cash promos, and the Bitunix footer.** Three of four real messages that day were not trades.

---

## 5. Strategy labels (hypothesis only)

| Label | Evidence | Confidence |
|---|---|---|
| Washed-out alt, hold the base | PIPPIN: ~98% off the Feb 2026 high, SL near the June low, "night research" | Medium for this one trade. **Outcome not a win.** |
| New-listing bag | PONS + MARSCOIN both first print **2026-09-02**, then named in the Sep 6 hold list | Medium that he **bags fresh Bitunix listings**. No SL/TP outcome to copy. |
| Whale / narrative | PONS fixture only: "has a whale's" | Low. One undated line. |
| Hold-bag management | Sep 6 holding post; Pieverse SL lift | High that he **manages a basket** this way. Not a scan entry. |
| Breakout scalp | **Not seen.** PIPPIN was not a breakout (price went +2.6% / −18% and stalled). | Do not scan for tight breakouts in his name. |

---

## 6. How to scan and suggest

**Self-scan of a proven Cosmas edge is not feasible.** There is one dated entry and it has not hit TP or SL. A scanner can only mimic the **shape** and must label every idea **low confidence / not a Cosmas copy**.

Do **not** auto-trade these. If FX GLITCH suggests any, the card should say `hypothesis, not a Cosmas signal`.

### Screen A — washed-out Bitunix alt (PIPPIN shape)

Run on Bitunix USDT-M futures, public tickers + daily klines. Skip BTCUSDT and ETHUSDT.

All of these must be true:

- 180-day high exists and mark is **at least 80% below** that high (PIPPIN was ~98% below 0.91).
- Mark is within **25%** of the 90-day low (he buys the base, not the bounce that already ran).
- 24h quote volume is enough to fill a small futures order (drop dead books).
- Proposed **SL** = the lowest daily low of the last ~120 days, then **about 5–8% above that low** if you want to match PIPPIN (0.013 was 5.9% above 0.01227). Reject the idea if that SL is not **20–45%** under the mark (PIPPIN was 34%). A tighter stop is a different strategy.
- Proposed **TP** = the 180–365 day high, **rounded down to one significant figure** (0.907 → 0.8). Reject if that TP is not at least **5×** the mark. Do not sell the round number as a likely target. It is a runner.
- Suggested leverage text **7x**, and the desk should still size with its own risk tiers, not 7x as a must.
- Hold language if a suggestion is written: retracement is expected; no time stop in his template.

### Screen B — new listing bag (PONS / MARSCOIN shape)

- Bitunix perpetual whose first daily bar is **≤ 14 days** old.
- Still **above** the listing-day low, and not already **> 8×** that low (MARSCOIN's September high was ~5× the 0.052 low; chasing past that is outside the sample).
- **Do not invent his TP/SL.** The only listing text (PONS fixture) used TP **1.5** (never traded; ~1.5× the 0.98 high) and SL **0.44** (already traded through). Until a dated listing signal exists, Screen B may **name** the coin as "Cosmas-like new listing" and **must not** attach a TP/SL or a buy.

### What not to suggest

- Anything whose only source is "Educational contents", a cash/Naira promo, or a Bitunix affiliate footer.
- A stop-adjustment line (`Adjust your sl to …`) as a new entry.
- A "targeting long" line with no CMP and no live mark between SL and TP.
- Shorts. None are in the sample.
- A moonshot TP as a high-probability full exit. PIPPIN's 0.8 is still untouched after 25 days, with price ~0.019.

### Confidence

| Use | Confidence |
|---|---|
| Parse his template and drop non-trades | **High** |
| Describe TP as round prior high and SL as ~base low / ~30% under CMP | **Medium** (one live trade + two fixtures) |
| Expect longs, 7–10x, hold through dips | **Medium**, tiny sample |
| Run Screen A/B and call the output a Cosmas trade | **Not justified.** Label **low / hypothesis**. |
| Claim a win rate | **Blocked.** 0 of 1 dated trades hit TP before SL. |

---

## 7. What we still cannot know

- Win rate, average winner, how often the far TP is actually paid.
- Any Cosmas message after 2026-09-06 18:44 Lagos, or before `:21566`.
- When KAITO / PONS / PIEVERSE fixtures were posted, or if they were real posts.
- Whether "profit on kaito" was a closed TP, a partial, or a screenshot of unrealized PnL.
- His personal size. Group leverage is not position size.

---

## 8. Template (style only)

```
{Symbol}/usdt
Long position
Entry CMP
Tp {prior cycle high, one significant figure}
Sl {just beyond the base low}
Leverage 7x

Night research let's go
```

Management lines he has actually used:

```
Retracement is part of the market
{Name}
Holding
Check out your profit on {name}
```

```
Adjust your sl to ${new floor} on {symbol}
```
