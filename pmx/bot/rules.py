"""Signal filters and risk limits. A rule from the review of other people's bots: every parameter either works and is covered by a test,
or is not in the config (duola had 5 of 6 limits not enforced, shmlkv – the daily volume and the age filter)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Filters:
    max_age_sec: float = 60          # a leader trade older than this is not copied (sells are always copied, otherwise the position hangs)
    min_price: float = 0.02
    max_price: float = 0.98
    min_seconds_to_end: float = 60   # less than this until trading ends – too late to make it
    categories: list[str] = field(default_factory=list)   # empty – all


@dataclass
class Risk:
    max_daily_buy_usd: float = 500.0
    max_open_positions: int = 50
    max_market_usd: float = 100.0     # our entry cost in one market
    max_total_exposure_usd: float = 800.0
    max_drawdown_usd: float = 300.0   # the account at cost fell this much from the start – stop buying
    kill_switch: str = "STOP"         # a file next to the log: if present – buying is stopped


def check_signal(f: Filters, side: str, age: float, price: float, info: dict, now: float | None = None) -> str | None:
    """The reason for skipping, or None."""
    if not info:
        return "market not found"
    if not info.get("accepting", True):
        return "market is not accepting orders"
    if side == "SELL":
        return None
    if age > f.max_age_sec:
        return f"leader trade older than {f.max_age_sec:.0f} s"
    if not f.min_price <= price <= f.max_price:
        return f"price {price:.2f} outside the corridor {f.min_price:.2f}–{f.max_price:.2f}"
    end = info.get("end_ts")
    if end and end - (now or time.time()) < f.min_seconds_to_end:
        return f"less than {f.min_seconds_to_end:.0f} s until trading ends"
    if f.categories and info.get("category") not in f.categories:
        return f"category '{info.get('category')}' is not in the list"
    return None


def check_risk(r: Risk, want_usd: float, cid: str, ledger, db_dir: Path) -> str | None:
    if (db_dir / r.kill_switch).exists():
        return f"stop file {r.kill_switch}"
    if ledger.bought_usd_today() + want_usd > r.max_daily_buy_usd:
        return f"daily buy limit ${r.max_daily_buy_usd:.0f}"
    pos = ledger.open_positions()
    if len(pos) >= r.max_open_positions and not any(p["cid"] == cid for p in pos):
        return f"open positions: {len(pos)} – limit reached"
    in_market = sum(p["cost"] for p in pos if p["cid"] == cid)
    if in_market + want_usd > r.max_market_usd:
        return f"market limit ${r.max_market_usd:.0f}"
    if sum(p["cost"] for p in pos) + want_usd > r.max_total_exposure_usd:
        return f"total exposure limit ${r.max_total_exposure_usd:.0f}"
    if ledger.start_cash() - (ledger.cash() + sum(p["cost"] for p in pos)) > r.max_drawdown_usd:
        return f"drawdown larger than ${r.max_drawdown_usd:.0f}"
    return None
