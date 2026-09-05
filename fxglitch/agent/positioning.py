"""Live positioning snapshot from public Binance USDⓈ-M endpoints.

This is not technical analysis. It does not compute RSI, EMA, Donchian,
Bollinger, MACD, or any chart pattern. It reports what the *crowd* is
doing, using data Binance publishes for free:

- last funding rate (who is paying to stay in the trade)
- open interest vs 24h price change (the four quadrants)
- global long/short account ratio

Thresholds are the ones already documented in fxglitch/derivs.py and
docs/INTELLIGENCE.md. They are labels, not a forecast that price will
go a particular way.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..derivs import BASELINE_FUNDING, FUNDING_ELEVATED, FUNDING_EXTREME

FAPI = "https://fapi.binance.com"
USER_AGENT = "FX-GLITCH/agent-os"


@dataclass
class Briefing:
    """Point-in-time crowd snapshot. Safe to show a judge or an LLM."""

    symbol: str
    as_of: str
    mark_price: float
    last_funding: float
    funding_label: str
    open_interest: float
    oi_change_pct: float | None
    price_change_24h_pct: float
    oi_quadrant: str
    long_short_ratio: float | None
    crowd: str
    notes: list[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    veto_long: bool = False
    veto_short: bool = False
    source: str = "binance-fapi-public"

    def to_dict(self) -> dict:
        return asdict(self)


def funding_label(rate: float) -> str:
    if rate >= FUNDING_EXTREME:
        return "extreme_long"
    if rate >= FUNDING_ELEVATED:
        return "elevated_long"
    if rate <= -FUNDING_EXTREME:
        return "extreme_short"
    if rate <= -FUNDING_ELEVATED:
        return "elevated_short"
    return "baseline"


def oi_quadrant(price_change_pct: float, oi_change_pct: float | None) -> str:
    """Four quadrants from docs/INTELLIGENCE.md. Informational, not an entry."""
    if oi_change_pct is None:
        return "unknown"
    price_up = price_change_pct > 0
    oi_up = oi_change_pct > 0
    if price_up and oi_up:
        return "new_longs"
    if price_up and not oi_up:
        return "short_squeeze"
    if (not price_up) and oi_up:
        return "new_shorts"
    return "long_liquidation"


def _get(url: str, transport) -> dict | list:
    return transport("GET", url)


def default_transport(method: str, url: str):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"Binance public API {exc.code}: {raw}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance public API unreachable: {exc.reason}") from None


def fetch_briefing(symbol: str = "BTCUSDT", *, transport=default_transport) -> Briefing:
    """Pull a positioning briefing. Public endpoints, no API key."""
    symbol = symbol.upper().replace("/", "")
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"

    premium = _get(f"{FAPI}/fapi/v1/premiumIndex?symbol={symbol}", transport)
    ticker = _get(f"{FAPI}/fapi/v1/ticker/24hr?symbol={symbol}", transport)
    oi_now = _get(f"{FAPI}/fapi/v1/openInterest?symbol={symbol}", transport)
    oi_hist = _get(
        f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=24",
        transport,
    )
    lsr = _get(
        f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1",
        transport,
    )

    mark = float(premium.get("markPrice") or 0)
    funding = float(premium.get("lastFundingRate") or 0)
    change_24h = float(ticker.get("priceChangePercent") or 0)
    open_interest = float(oi_now.get("openInterest") or 0)

    oi_change = None
    if isinstance(oi_hist, list) and len(oi_hist) >= 2:
        first = float(oi_hist[0].get("sumOpenInterest") or 0)
        last = float(oi_hist[-1].get("sumOpenInterest") or 0)
        if first > 0:
            oi_change = (last - first) / first * 100.0

    ratio = None
    if isinstance(lsr, list) and lsr:
        raw = lsr[-1].get("longShortRatio")
        if raw not in (None, ""):
            ratio = float(raw)

    label = funding_label(funding)
    quadrant = oi_quadrant(change_24h, oi_change)
    notes: list[str] = []
    veto_long = label in ("elevated_long", "extreme_long")
    veto_short = label in ("elevated_short", "extreme_short")

    if label == "extreme_long":
        crowd = "euphoric longs — they are paying to stay; long liquidations are the fuel"
        notes.append("Refuse new longs. Crowded positioning is not a short prediction.")
        size = 0.4
    elif label == "elevated_long":
        crowd = "crowd leaning long and paying funding"
        notes.append("Do not add longs. Size any existing exposure down, do not invent a short.")
        size = 0.6
    elif label == "extreme_short":
        crowd = "heavily short — squeeze fuel if price rises anyway"
        notes.append("Refuse new shorts. This is not a signal to market-buy.")
        size = 0.4
    elif label == "elevated_short":
        crowd = "crowd leaning short and paying funding"
        notes.append("Do not add shorts. Squeeze fuel is not an entry.")
        size = 0.6
    else:
        crowd = f"funding near baseline ({BASELINE_FUNDING * 100:.2f}%/8h) — no crowd signal"
        size = 1.0

    if quadrant == "short_squeeze":
        notes.append("Price up on falling OI: shorts covering. Violent, then it exhausts.")
        size = min(size, 0.5)
    elif quadrant == "new_longs":
        notes.append("Price up on rising OI: new money, not a forced cover.")
    elif quadrant == "new_shorts":
        notes.append("Price down on rising OI: new shorts, genuine downtrend pressure.")
    elif quadrant == "long_liquidation":
        notes.append("Price down on falling OI: longs flushed. Often a low, not a forecast.")

    if ratio is not None:
        notes.append(f"Global long/short account ratio {ratio:.2f} ( >1 = more long accounts ).")

    notes.append("This briefing does not predict the next candle. It describes positioning.")

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Briefing(
        symbol=symbol,
        as_of=as_of,
        mark_price=mark,
        last_funding=funding,
        funding_label=label,
        open_interest=open_interest,
        oi_change_pct=None if oi_change is None else round(oi_change, 3),
        price_change_24h_pct=round(change_24h, 3),
        oi_quadrant=quadrant,
        long_short_ratio=None if ratio is None else round(ratio, 4),
        crowd=crowd,
        notes=notes,
        size_multiplier=size,
        veto_long=veto_long,
        veto_short=veto_short,
    )
