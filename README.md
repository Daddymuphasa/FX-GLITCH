# FX-GLITCH

**Binance Agent OS Mini Hackathon · Track B (MCP + trade)**  
The MCP that sits between an AI agent and a Binance USDⓈ-M order.

An agent may *propose*. FX-GLITCH decides whether the trade is allowed.

- **Positioning** (funding, open interest, long/short) describes the crowd.
- **Policy** refuses missing stops, excess leverage, daily-loss breaches, and alt baskets that are one BTC bet.
- **Execution** is dry-run unless you pass `--live` and set API keys.
- **Chart TA is not in this path.** Donchian / EMA / squeeze files remain in `strategies/` as a research archive. The live product does not call them.

Live demo (Vercel): **https://fx-glitch.vercel.app**

The desk reads a Bitunix Telegram futures signal, maps the USDT-M pair on Binance, and offers four risk plans (Daredevil / High / Mid / Low). Stop stays the group’s invalidation. Size, leverage and TP change with the plan. Win/loss is that setup’s R-multiple, not a made-up win rate.

```
python tools/telegram_listen.py --discover
python tools/telegram_listen.py
```

```
python agent.py --demo
python serve.py          # local: http://127.0.0.1:8765
python -m fxglitch.agent.mcp_server
```

Pair with the official Binance MCP:

```
claude mcp add fx-glitch -- python -m fxglitch.agent.mcp_server
claude mcp add binance-mcp-server --transport http https://agent.binance.com/mcp/agentic
```

Submission notes, published rules, and the X reply draft: [docs/HACKATHON.md](docs/HACKATHON.md).

---

## Why this exists

Binance Agent OS already lets an agent trade through `https://agent.binance.com/mcp/agentic` (Spot / Margin / Convert / USDⓈ-M / COIN-M inside an Agentic sub-account). That MCP does not know when the book is crowded, when twenty alt longs are one BTC bet, or when a proposed order has no stop.

FX-GLITCH is that missing layer.

The 19 August 2026 squeeze is the example this repo already measured: ~$2.7bn of shorts were force-bought, and the setup was public in funding and open interest. Price-action systems can look like they “caught” that move after the fact. Positioning data is what actually described the crowd *during* it. See [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md).

---

## 60-second judge flow (offline, no keys)

```bash
python agent.py --demo
```

```
BLOCK  no-stop 20x            no stop price — refusing to open an unprotected position
BLOCK  correlated alts        already 3 long positions — that is one BTC bet 3 times over
BLOCK  crowded long veto      positioning veto: euphoric longs
ALLOW  clean dry-run          all guards passed — dry-run until --live
```

Dashboard: `python serve.py` then **Run 60-second judge demo**.

---

## MCP tools

| Tool | What it does |
|---|---|
| `get_positioning` | Public Binance USD-M briefing. No API key. Not RSI/EMA. |
| `propose_from_positioning` | Default: stand aside. This layer does not invent entries. |
| `parse_external_signal` | Telegram-style signal → candidate. Still not an order. |
| `check_policy` | Hard guards. Refuse, never silently resize. |
| `run_cycle` | Briefing → proposal → policy. Dry-run unless `live=true` **and** keys. |
| `judge_demo` | The four cases above. |

---

## Live Binance (optional)

Public briefing needs no keys. Sending does.

```
BINANCE_API_KEY
BINANCE_SECRET_KEY
# optional: BINANCE_FUTURES_BASE_URL=https://demo-fapi.binance.com
```

```bash
python agent.py --symbol BTCUSDT
python agent.py --message "BTCUSDT long CMP tp 120000 sl 105000 lev 5x"
python live.py donchian_breakout --venue binance --symbols BTCUSDT   # archive path
```

`--live` on either entry point actually sends. Without it, every decision is computed and not sent.

Venue: [fxglitch/venues/binance.py](fxglitch/venues/binance.py) — HMAC, exchange filters, MARK_PRICE protective stops.

---

## Tests

```bash
python -m unittest discover tests
```

Agent OS path: `tests/test_agent.py`. Binance venue (offline fakes): `tests/test_binance.py`.

---

## Layout

```
fxglitch/agent/         the product (positioning, policy, MCP, HTTP API)
fxglitch/venues/        Binance USDⓈ-M + Bitunix
fxglitch/live/          guards, portfolio (BTC-equivalent exposure), state
fxglitch/derivs.py      funding / OI thresholds used by the briefing
web/                    judge dashboard
skills/fx-glitch/       Agent OS skill file
docs/HACKATHON.md       published rules only
mcp.json                how to pair this MCP with Binance's
strategies/             research archive — not used by agent.py / MCP
```

---

## Honest limits

- Default is dry-run. A good policy pass is not a promise of profit.
- Funding extremes are **contrarian labels**, not a forecast that price will reverse.
- Binance free OI history is ~30 days. Do not pretend you backtested OI over years.
- Binance did not publish a numeric judging rubric. We did not invent one.
- You still have to follow @Binance, repost, reply, and complete the survey.

Deadline: **8 Sep 2026 23:59 UTC**.
