"""Minimal xAI Grok chat + tool-calling client (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

XAI_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-3-mini"


def _api_key() -> str:
    return (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Grok chat completions. Returns the raw assistant message dict."""
    key = _api_key()
    if not key:
        raise RuntimeError("XAI_API_KEY is not set")
    payload: dict[str, Any] = {
        "model": model or os.environ.get("FXGLITCH_GROK_MODEL") or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        XAI_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "FX-GLITCH/grok-bot",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"xAI HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"xAI request failed: {exc.reason}") from exc
    choices = raw.get("choices") or []
    if not choices:
        raise RuntimeError(f"xAI empty choices: {str(raw)[:300]}")
    return choices[0].get("message") or {}
