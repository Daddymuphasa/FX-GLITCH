# Binance Agent OS Mini Hackathon — what is published vs what we built

Sources used (no invented rubric):

- Official blog: https://www.binance.com/en/blog/community/8802181509900814931
- Official survey: https://www.binance.com/en/survey/2913aa200aac462c89a737779393f3d4
- Binance X reminder (5 Sep 2026): https://x.com/binance/status/2096025526339010723
- Agent Native MCP docs: https://developers.binance.com/en/docs/agent-native/mcp-server
- Hosted Binance MCP: https://agent.binance.com/mcp/agentic
- Skills Hub: https://github.com/binance/binance-skills-hub
- Agent OS intro (Square, 20 Aug 2026): https://www.binance.com/en/square/post/357708857068871

**Deadline:** 8 September 2026, 23:59 UTC.

**Prize pool (published):** $60,000 USDC. Track A $20,000 (build an AI agent with Agent OS). Track B $40,000 (connect your MCPs and trade).

**Eligibility (published):** not available in US, UK, EEA, Hong Kong, Singapore, and jurisdictions on Binance’s prohibited list.

**Judging rubric:** Binance did **not** publish a numeric scoring sheet. This document does not invent one.

---

## Published entry steps (you must do these yourself)

1. Follow [@Binance](https://x.com/binance) and **repost** the official announcement.
2. **Reply or quote-repost** with the submission. Track A asks for video/demo + GitHub.
3. Complete the [survey](https://www.binance.com/en/survey/2913aa200aac462c89a737779393f3d4).

Suggested reply text (edit with your X handle and demo URL):

```
Track B — FX-GLITCH
MCP policy layer for Binance USDⓈ-M futures.

The Binance MCP can trade. This MCP decides whether the trade is allowed.
Positioning (funding, OI, long/short) describes the crowd. Guards refuse
leverage, missing stops, daily-loss breaches, and correlated alt baskets
that are one BTC bet. Chart TA is not in the live path.

GitHub: https://github.com/Daddymuphasa/FX-GLITCH
Demo: https://fx-glitch.vercel.app
MCP:  python -m fxglitch.agent.mcp_server
Pair with: https://agent.binance.com/mcp/agentic

#Binance #AgentOS #Hackathon
```

---

## What this repo actually hits

| Published requirement | How this repo hits it |
|---|---|
| Track B: connect MCPs and trade | Stdio MCP in `fxglitch/agent/mcp_server.py` + documented pair with official Binance MCP |
| Trading on Binance | `fxglitch/venues/binance.py` USDⓈ-M perps, HMAC, filters, protective stops. Dry-run unless `--live` |
| Agent OS | MCP tools an agent can call: positioning, policy, cycle, signal parse, judge demo |
| Demo | https://fx-glitch.vercel.app + `python agent.py --demo` offline 60s flow |
| GitHub | this repository |

What we **cannot** do for you: follow, repost, survey, or confirm your jurisdiction.

---

## Product, in one paragraph

FX-GLITCH is not a chart bot. An agent (or a pasted signal) may propose a futures trade. FX-GLITCH fetches public Binance positioning, runs deterministic guards, and either dry-runs or — only if you pass `--live` and API keys — sends to USDⓈ-M. Failure mode is stand aside, not a guessed breakout.
