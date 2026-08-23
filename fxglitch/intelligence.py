"""AI-assisted analysis helpers for trading signals.

This module stays on the analysis side of the system. It does not place
orders. Instead it:

1. parses Telegram-style signal messages into structured observations
2. builds a compact context payload for an LLM
3. asks the model for a readable trading note and strategy ranking

The intent is to support the trader, not replace the rest of the execution
stack. Decisions still flow through the existing strategy, guard, portfolio,
and venue layers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from .signals import SignalFeed

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(slots=True)
class TelegramSignal:
    """A parsed message from a read-only Telegram source."""

    raw: str
    source: str = "telegram"
    parsed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    direction: str = "unknown"
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float | None = None
    note: str = ""


def parse_telegram_signal(message: str, source: str = "telegram") -> TelegramSignal:
    """Best-effort parser for noisy signal messages.

    The parser is intentionally conservative: if a field is missing or unclear,
    it stays blank rather than inventing a guess.
    """
    text = " ".join(message.strip().split())
    upper = text.upper()

    symbol = ""
    for candidate in re.findall(r"\b[A-Z]{3,12}(?:USDT|USD|XAUUSD|XAU|V75|V75M|SYN\d+)?\b", upper):
        symbol = candidate
        break

    direction = "unknown"
    if re.search(r"\b(BUY|LONG|CALL)\b", upper):
        direction = "long"
    elif re.search(r"\b(SELL|SHORT|PUT)\b", upper):
        direction = "short"

    def _first_number(patterns: list[str]) -> float | None:
        for pattern in patterns:
            match = re.search(pattern, upper)
            if match:
                try:
                    return float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
        return None

    entry = _first_number([
        r"\bENTRY[:\s]+([0-9]+(?:\.[0-9]+)?)",
        r"\bBUY\s+@?\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bSELL\s+@?\s*([0-9]+(?:\.[0-9]+)?)",
    ])
    stop_loss = _first_number([
        r"\bSL[:\s]+([0-9]+(?:\.[0-9]+)?)",
        r"\bSTOP(?:-?LOSS)?[:\s]+([0-9]+(?:\.[0-9]+)?)",
    ])
    take_profit = _first_number([
        r"\bTP[:\s]+([0-9]+(?:\.[0-9]+)?)",
        r"\bTAKE(?:-?PROFIT)?[:\s]+([0-9]+(?:\.[0-9]+)?)",
    ])
    confidence = _first_number([r"\bCONFIDENCE[:\s]+([0-9]+(?:\.[0-9]+)?)"])

    return TelegramSignal(
        raw=text,
        source=source,
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=confidence,
        note=text[:240],
    )


def build_context(
    *,
    symbols: list[str],
    strategies: list[str],
    feed: SignalFeed | None = None,
    telegram: list[TelegramSignal] | None = None,
    market_note: str = "",
) -> dict[str, Any]:
    """Build a compact payload for the model."""
    return {
        "symbols": symbols,
        "strategies": strategies,
        "market_note": market_note,
        "telegram": [asdict(sig) | {"parsed_at": sig.parsed_at.isoformat()} for sig in telegram or []],
        "signal_bias": None if feed is None else feed.bias_at(datetime.now(timezone.utc)),
        "signal_count": 0 if feed is None else len(feed),
    }


def _openai_chat(prompt: str, api_key: str, model: str = "gpt-4.1-mini") -> str:
    """Minimal OpenAI Responses API call using the standard library only."""
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "You are a careful trading analyst. Focus on explanation, filtering, and strategy selection. Do not claim certainty."}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        OPENAI_RESPONSES_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.code} {exc.reason}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc

    texts: list[str] = []
    for item in raw.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    return "\n".join(texts).strip()


def analyze_with_openai(context: dict[str, Any], *, model: str = "gpt-4.1-mini") -> str:
    """Return a readable analysis note from OpenAI.

    Requires OPENAI_API_KEY in the environment. If the key is absent, callers
    should fall back to the raw context or a local rules-based summary.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    prompt = (
        "Analyze this trading context for synthetic 75 and gold.\n"
        "Your job is analysis only: trade explanation, signal filtering, strategy "
        "selection, and decision support.\n"
        "Return a concise note with:\n"
        "- signal quality\n"
        "- whether the message should be trusted\n"
        "- which strategy family seems most suitable\n"
        "- any red flags\n"
        "- a final recommendation phrased as analysis, not an order\n\n"
        f"CONTEXT:\n{json.dumps(context, indent=2, default=str)}"
    )
    return _openai_chat(prompt, api_key=api_key, model=model)


def local_summary(context: dict[str, Any]) -> str:
    """Fallback if OpenAI is unavailable."""
    strategies = ", ".join(context.get("strategies", [])) or "none"
    symbols = ", ".join(context.get("symbols", [])) or "none"
    telegram = context.get("telegram", [])
    return (
        f"symbols: {symbols}\n"
        f"strategies: {strategies}\n"
        f"telegram messages: {len(telegram)}\n"
        f"signal bias: {context.get('signal_bias')}\n"
        "analysis mode active; no model response available yet"
    )
