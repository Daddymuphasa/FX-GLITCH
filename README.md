# FX-GLITCH

**The agent may propose. This layer decides if the trade is allowed.**

Policy MCP for Binance USDⓈ-M futures. Built for the [Binance Agent OS Mini Hackathon](https://www.binance.com/en/blog/community/8802181509900814931) · **Track A**.

Binance’s MCP can place the order. FX-GLITCH answers a different question: **should it?**

---

## What it does

| Layer | Job |
|---|---|
| **Positioning** | Reads the crowd from public Binance data — funding, open interest, long/short. Not RSI. Not EMA. |
| **Policy** | Hard no on missing stops, excess leverage, crowded books, and alt baskets that are one BTC bet. |
| **Execution** | Dry-run by default. Sends only with `--live` **and** API keys. |

It does **not** invent chart entries. Default action is stand aside.

```
Agent  →  FX-GLITCH (positioning + policy)  →  Binance MCP / USD-M
                │
                ├── BLOCK  (no order)
                └── ALLOW  (dry-run, or live if you said so)
```

---

## 60-second demo (no keys, no network)

```bash
python agent.py --demo
```

```
BLOCK  no-stop 20x             no stop — unprotected position refused
BLOCK  correlated alts         3 alt longs = one BTC bet, entered 3 times
BLOCK  crowded long veto       euphoric longs — they are paying to stay
ALLOW  clean dry-run           guards passed — still not sent
```

**Correlated alts:** ETH, SOL, DOGE, WIF usually move with Bitcoin. Four “small” longs are often one BTC long. The cap is three same-direction tickets.

---

## Pair with Agent OS

```bash
claude mcp add fx-glitch -- python -m fxglitch.agent.mcp_server
claude mcp add binance-mcp-server --transport http https://agent.binance.com/mcp/agentic
```

Or use [`mcp.json`](mcp.json) in this repo.

| This MCP | Official Binance MCP |
|---|---|
| `python -m fxglitch.agent.mcp_server` | `https://agent.binance.com/mcp/agentic` |
| Is the trade allowed? | Place it, if you still want to |

Skill file for agents: [`skills/fx-glitch/SKILL.md`](skills/fx-glitch/SKILL.md)

---

## MCP tools

| Tool | What it does |
|---|---|
| `get_positioning` | Public USD-M briefing. No API key. |
| `propose_from_positioning` | Default: **stand aside**. Does not invent entries. |
| `parse_external_signal` | Signal text → candidate. Still not an order. |
| `check_policy` | Guards. Refuses. Never silently resizes. |
| `run_cycle` | Briefing → proposal → policy. Dry-run unless `live=true` and keys. |
| `judge_demo` | The four cases above. |

---

## Run

```bash
python -m pip install -r requirements.txt
python agent.py --demo
python agent.py --symbol BTCUSDT
python agent.py --message "BTCUSDT long CMP tp 120000 sl 105000 lev 5x"
python -m unittest discover tests
```

Local desk: `python serve.py --open` → http://127.0.0.1:8765  
Live desk: https://fxglitch.xyz

Sending to Binance is optional:

```
BINANCE_API_KEY
BINANCE_SECRET_KEY
```

`--live` actually sends. Without it, every decision is computed and **not** sent.

---

## Repo map

```
fxglitch/agent/     the product — positioning, policy, MCP, HTTP
fxglitch/live/      guards + BTC-equivalent exposure
fxglitch/venues/    Binance USDⓈ-M
skills/fx-glitch/   Agent OS skill
mcp.json            pair this MCP with Binance’s
public/             desk UI
docs/HACKATHON.md   published contest rules only
strategies/         research archive — not used by the agent
```

---

## Honest limits

- A policy pass is not a profit forecast.
- Funding extremes are labels on the crowd, not a promise that price reverses.
- Chart strategies in `strategies/` are archive. The live path does not call them.
- Binance did not publish a numeric judging rubric. This repo does not invent one.

Built by [Daddymuphasa](https://github.com/Daddymuphasa).
