"""Markets: a flat table from the Gamma response, the market category, the outcome."""
from __future__ import annotations

import json
import re

CATS = [
    # "will X say word Y" is a mentions market, even if the word is about inflation or crypto
    ("mentions", r"\bsays?\b|\bsaid\b|\bmention|\btweets?\b|\bposts?\b.*\btimes\b"),
    ("weather", r"temperature|°c|°f|\brain\b|snow|weather"),
    ("crypto up/down", r"up or down"),
    ("crypto price", r"bitcoin|\bbtc\b|ethereum|\beth\b|solana|\bxrp\b|crypto|price of"),
    ("economy", r"\bfed\b|interest rate|cpi|inflation|gdp|s&p|nasdaq|stock|earnings"),
    ("elections", r"elect|nominee|primary"),
    ("culture", r"box office|oscar|grammy|album|movie|award|billboard"),
]


SPORT_SERIES = r"(mlb|nba|nfl|nhl|ufc|epl|ucl|uel|mls|wnba|atp|wta|cs2|counter-strike|league-of-legends|dota|valorant|soccer|f1|formula|golf|pga|ncaa|cfb|cbb|cricket|ipl|tennis|boxing|mma|premier-league|la-liga|serie-a|bundesliga)"


def category(m: dict) -> str:
    # gameStartTime also exists on weather markets (start of the local day), so check the text first, then sports
    text = f"{m.get('question') or ''} {m.get('slug') or ''}".lower()
    for name, rx in CATS:
        if re.search(rx, text):
            return name
    series = m.get("series_slug") or ((m.get("events") or [{}])[0] or {}).get("seriesSlug") or ""
    if m.get("sportsMarketType") or (m.get("gameStartTime") and re.search(r"\bvs\.?\b|\bwin on\b|o/u|spread|map \d|set \d", text)) \
            or re.search(r"\bvs\.?\b", text) or re.match(SPORT_SERIES, series):
        return "sports"
    return "other"


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def fee(shares, price, rate):
    """Taker fee in dollars: C × rate × p × (1 − p), rounded to 5 decimals (docs.polymarket.com/trading/fees).
    The maker pays nothing."""
    return (shares * rate * price * (1 - price)).round(5) if hasattr(shares, "round") else round(shares * rate * price * (1 - price), 5)


def flat(m: dict) -> dict:
    """The market fields we need in one row; winner_index is the index of the winning outcome if the market is resolved."""
    prices = [float(x) for x in (_j(m.get("outcomePrices")) or [])] if m.get("outcomePrices") else []
    winner = None
    uma = m.get("umaResolutionStatus")
    # the winner only after final UMA resolution: a proposed but disputed outcome is not counted
    if m.get("closed") and prices and max(prices) >= 0.99 and uma in (None, "", "resolved"):
        winner = prices.index(max(prices))
    ev = (m.get("events") or [{}])[0]
    fs = _j(m.get("feeSchedule")) or {}
    return {
        "condition_id": (m.get("conditionId") or "").lower(),
        "question": m.get("question"),
        "slug": m.get("slug"),
        "event_title": ev.get("title"),
        "series_slug": ev.get("seriesSlug"),
        "category": category(m),
        "outcomes": json.dumps(_j(m.get("outcomes"))),
        "outcome_prices": json.dumps(prices),
        "token_ids": json.dumps(_j(m.get("clobTokenIds"))),
        "closed": bool(m.get("closed")),
        "winner_index": winner,
        "end_date": m.get("endDate"),
        "closed_time": m.get("closedTime"),
        "game_start": m.get("gameStartTime"),
        "sports_type": m.get("sportsMarketType"),
        "created_at": m.get("createdAt"),
        "uma_status": uma,
        "neg_risk": m.get("negRisk"),
        "neg_risk_market_id": (m.get("negRiskMarketID") or "").lower() or None,   # for CONVERSION in activity, condition_id = this id
        "fees_enabled": m.get("feesEnabled"),
        "fee_rate": float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0,
        "fee_exponent": fs.get("exponent"),
        "fee_type": m.get("feeType"),
        "volume": float(m.get("volumeNum") or m.get("volume") or 0),
        "resolution_source": m.get("resolutionSource"),
    }
