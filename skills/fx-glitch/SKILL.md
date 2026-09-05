---
title: FX-GLITCH
description: Policy and positioning layer for Binance USDⓈ-M futures. Use when an agent wants to trade on Binance: fetch crowd positioning (funding, open interest, long/short), parse an external signal, then run hard risk guards before any order. Does not invent RSI/EMA/Donchian entries.
metadata:
  version: 1.0.0
  author: Daddymuphasa
  license: MIT
---

# FX-GLITCH

Sit this skill **in front of** the official Binance MCP (`https://agent.binance.com/mcp/agentic`).

Binance MCP can read markets and place trades inside an Agentic sub-account. FX-GLITCH answers a different question: **is this trade allowed?**

## Rules the agent must follow

1. Call `get_positioning` before proposing a futures order.
2. Default action is `stand_aside`. Positioning may veto or shrink size. It does not invent a chart entry.
3. Call `check_policy` on every proposed `open`. If it refuses, do not send.
4. Never send without a stop. Never exceed 5x unless the user raised the cap on purpose.
5. Treat alt longs as BTC exposure. Three same-direction tickets is the default ceiling.
6. Dry-run unless the user explicitly asks to go live AND keys exist.

## Pairing

```
claude mcp add fx-glitch -- python -m fxglitch.agent.mcp_server
claude mcp add binance-mcp-server --transport http https://agent.binance.com/mcp/agentic
```

## Tools

- `get_positioning`
- `propose_from_positioning`
- `parse_external_signal`
- `check_policy`
- `run_cycle`
- `judge_demo`
