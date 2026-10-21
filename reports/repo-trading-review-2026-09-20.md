# Trading Reliability and Weekly Target Review

The repository does not demonstrate an ability to earn 40% every week. Reliability fixes can remove execution errors; they cannot create a profitable trading signal or ensure a return. Changes in this review are local and have not been deployed or tested with real orders.

## Findings and Changes

| Finding | Change |
| --- | --- |
| Signal text, stored row values and trade confirmation could produce different sizes. | Preserve normalized prices when publishing signals; use current exchange prices and equity for chat quotes; recheck at submission and cap size at the approved quantity. |
| Missing market prices could be replaced with an invented midpoint entry. | Require an actual entry or a live price. Live sends require a fresh venue price. |
| Trade sends could continue after a balance lookup failed and ignored existing holdings. | Re-read balance and holdings; apply capacity, exposure, margin and daily-loss checks before a Bitunix plan is submitted. |
| Duplicate confirmations could create new timestamp-based order IDs. | Persist pending/accepted signal submissions, use stable IDs, and serialize plan submissions in the desk process. Explicit exchange refusals can be retried; unknown outcomes remain blocked for reconciliation. |
| Risk plans omitted fees/slippage and could promote blocked tiers as meeting the target. | Size against stop distance plus a stated cost allowance. Compare estimated net TP returns and exclude policy-blocked tiers. |
| Daily-target protection only affected winners below +1R and forgot original stops after restart. | Choose the tighter of the R ladder and a stop at half the open profit. Persist original stops and daily activation per account. Poll configured accounts even without a logged-in chat. |
| Signal parsing missed `sl of ...`, misclassified stop updates, and rejected short asset names. | Correct those parsing cases and support symbols such as OPUSDT and WUSDT. |
| Invalid/non-finite prices, wrong-side targets and explicit oversized quantities were not fully checked. | Add checks in the shared policy and guards. |
| Agent cycles could fetch BTC context for another signal symbol, ignore holdings with an injected balance, and omit leverage setup. | Use the proposal symbol, refresh live holdings and price, persist the daily-loss anchor and set leverage before sending. |
| A scan could ignore positions accepted earlier in that scan; daily-loss halts could never roll to a new day. | Reserve pending entries and margin within the scan, refresh holdings, persist accepted actions promptly, and allow daily rollover. |
| Binance conditional orders used old endpoints, replacement cancelled protection first, and hedge closes addressed the wrong side. | Use the current algo-order API, submit replacement protection before cancelling old stops, correct hedge-side routing, and attempt a reducing emergency close when market-entry protection fails. |
| Backtests omitted the final exit's costs from final equity and filled adverse stop gaps at the stop price. | Include final exit costs in the equity curve and use the worse opening price when a bar gaps through a stop. |

## Target Arithmetic

For seven trading days, the daily compounded pace is `100 * (1.40 ** (1/7) - 1) = 4.924144%`.

Estimated risk needed for one winning trade to produce that daily gain is `daily_target_pct / net_reward_risk`. This describes the winning outcome only. Losses, trade frequency, funding, slippage and the probability of reaching TP determine actual results. A tier meeting this arithmetic is not a prediction or an instruction to increase risk automatically.

Plans currently allow 0.08% of notional per side for costs: assumed 0.06% fee plus 0.02% slippage. These are explicit planning assumptions, not verified fees for the user's account. Funding is excluded. A gap or worse execution can exceed the estimated loss.

## Historical Evidence

Offline replay used the local BTC daily, ETH daily and BTC hourly CSVs, with four research strategies, fixed parameters and no optimisation. The local series end in August 2026. The live Telegram/WhatsApp signal feed is a different strategy, so these results do not validate it.

| Risk Per Trade | BTC Daily Donchian | BTC Hourly Donchian | Weeks Reaching 40% |
| --- | --- | --- | --- |
| 1% | +96.68% total; 5.33% max drawdown | -4.52% total; 27.29% max drawdown | 0/572 daily; 0/99 hourly |
| 3% | +568.62% total; 15.19% max drawdown | -27.67% total; 64.34% max drawdown | 0/572 daily; 0/99 hourly |
| 8% | +7898.57% total; 35.85% max drawdown | -85.24% total; 96.71% max drawdown | 0/572 daily; 3/99 hourly |

Total returns span each entire dataset, not one week or one year. BTC daily covers roughly eleven years; BTC hourly roughly two. None of the twelve strategy/data combinations reached 40% in a complete week at 1% or 3% risk. Confluence was run without an intelligence feed and with fixed risk, making it equivalent to the plain breakout here; those rows are not independent evidence.

Raw results:

- [1% risk](performance-audit-2026-09-20.json)
- [3% risk](performance-audit-risk3-2026-09-20.json)
- [8% risk](performance-audit-risk8-2026-09-20.json)

Reproduce with:

```powershell
python tools/performance_audit.py --csv data/raw/btcusdt_1d.csv data/raw/ethusdt_1d.csv data/raw/btcusdt_1h.csv --risk 1 --output reports/performance-audit-2026-09-20.json
python -m unittest discover -s tests
```

## Remaining Limits

Verification: all 344 unit tests pass, including the new execution, parsing, protection, weekly-report and backtest regressions. `git diff --check` passes. The declared WebAuthn dependency was installed into an ignored local test directory; Windows sandbox permissions required running the full offline suite with access to that directory.

- There is no validated, reconciled history of realised Telegram-signal fills, costs and weekly net performance in this review. Positive expectancy for that live workflow is unproven.
- The target tracker measures equity changes, including open P/L and deposits/withdrawals. It is not a cashflow-adjusted realised-profit ledger. Deposits can activate protection. Daily/weekly anchors start at the first observation in the UTC day/ISO week, not a reconstructed midnight balance.
- Moving a stop to half the open profit does not secure the entire daily target. Other positions, fees, gaps and later losses can reduce account profit. The target latch resets with the UTC day; existing stops are never intentionally loosened.
- The 10-second protection loop needs the long-running desk process and working exchange access. Serverless handlers cannot provide this continuous loop. Native exchange stops remain important during an outage.
- Pending-order records with unknown outcomes require exchange reconciliation before another submission. Process-local serialization is not a distributed lock: multiple desk/serverless instances need shared transactional storage before they can safely execute concurrently for one account.
- Research replays exclude funding, liquidation rules, liquidity limits and exchange minimum sizes. They use bar data and fixed cost assumptions, not tick-by-tick fills, and are not unseen-data validation. The research runner and signal-copy workflow also have different execution and sizing rules.
- Offline tests and current documentation checks do not prove that a deployed exchange integration accepts every request. No authenticated live account or real-money trade was exercised.

## ZETA Follow-Up: 2026-09-21

Read-only SSH inspection of the running WhatsApp backend confirmed that the earlier local fixes were not deployed. Its risk preview calls `recommend` without the account balance, using the default 1,000 USDT. Confirmation fetches the account balance but still uses the signal's cached mark. That deployed planner can also invent a midpoint between stop and target when no mark is available. These are verified differences between the running code and this working tree, not an inference from the error text alone.

The saved ZETA conversation's balance, cached entry and stop reproduce the reported unrounded quantity `3.43351`. The 10-unit exchange minimum is real; at those saved inputs it requires approximately 1.456% of equity before costs, exceeding the selected 0.5%. Thus the minimum-size refusal is justified for those inputs, while the preceding preview and cached-price workflow are misleading. No current exchange balance was requested and no trade was submitted during this diagnosis.

Additional local corrections:

- Explicit CMP entries require a supplied market price and cannot use a cached entry override when that price is missing.
- Plans report rounded size, executable minimum size, and estimated minimum-order stop loss as cash and a percentage of equity, including the stated cost allowance.
- Preview policy evaluates the rounded quantity, avoiding refusals caused solely by a larger unrounded quantity.
- WhatsApp rejects undersized plans before confirmation and identifies the lowest-risk tier passing preview checks, when one exists. Selecting it still requires a new preview and explicit confirmation; no automatic risk increase is performed.
- Selecting another tier clears the prior approval even if repricing fails.
- `/api/health` exposes `sizing_version` for deployment verification.

Verification: all 353 unit tests pass, and `git diff --check` passes.

These changes remain local pending deployment approval. A deployment must preserve server data and credentials, verify the reported sizing version, and exercise previews without sending real orders. The broader account protection limitations above still apply; passing a preview does not bypass final position, daily-loss, or execution checks.

## Sources

Binance's current [USD-M trading API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) specifies the conditional-order endpoints and parameters used in the adapter fix. Bitunix documents attached SL/TP on its [place-order endpoint](https://www.bitunix.com/api-docs/futures/trade/place_order.html). The CFTC explains that [futures leverage amplifies risk](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/understand_risks_of_virtual_currency.html); it cannot turn a target into a guaranteed outcome.
