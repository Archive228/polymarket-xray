"""Live market data for the bot: the CLOB order book and the Gamma market card (fee, trading end, outcome)."""
from __future__ import annotations

import time
from datetime import datetime

from .. import api
from ..markets import category, flat
from ..pairs import slug_window
from .models import OrderBook, OrderBookLevel

_info: dict[str, tuple[float, dict]] = {}


def get_book(token: str) -> tuple[OrderBook, dict]:
    """The token's order book right now + service fields (tick size, minimum order)."""
    d = api.get(f"{api.CLOB}/book", {"token_id": token}, tries=3) or {}
    book = OrderBook(bids=[OrderBookLevel(float(x["price"]), float(x["size"])) for x in d.get("bids") or []],
                     asks=[OrderBookLevel(float(x["price"]), float(x["size"])) for x in d.get("asks") or []])
    return book, {"tick": float(d.get("tick_size") or 0.01), "min_shares": float(d.get("min_order_size") or 0)}


def market_info(cid: str, max_age: float = 300) -> dict:
    """Market card: whether it is open, the fee rate, the trading end, the winner. Cached in memory for 5 minutes."""
    cid = cid.lower()
    hit = _info.get(cid)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    m = api.markets_by_condition([cid]).get(cid)
    info = flat(m) if m else {}
    if m:
        info["category"] = category(m)
        info["accepting"] = bool(m.get("acceptingOrders", True)) and not m.get("closed")
        end = slug_window(info.get("slug"))[2]            # for short crypto markets the end is exactly in the slug
        if not end and info.get("end_date"):
            try:
                end = datetime.fromisoformat(str(info["end_date"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                end = None
        info["end_ts"] = end
    _info[cid] = (time.time(), info)
    return info
