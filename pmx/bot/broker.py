"""Paper broker: a decision on the signal and a fill against the live order book, without money and without keys.

Buy: size – sizing.calculate_order_size (ported from shmlkv), price ceiling – a relative and an absolute tolerance
to the leader's price right away (shmlkv had only "ask − 5¢"), fill – fill.simulate_buy_fill (agent-next) with
the market fee. Sell: the share of our position that the leader sold of his own; the tier multiplier is not
applied a second time (a shmlkv bug). The order book and market sources are injected – so the broker is tested without a network."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .fill import simulate_buy_fill, simulate_sell_fill
from .ledger import Ledger
from .rules import Filters, Risk, check_risk, check_signal
from .sizing import SizingConfig, calculate_order_size


@dataclass
class Slippage:
    rel: float = 0.05     # no more expensive than the leader's price by 5%…
    abs: float = 0.02     # …and no more than 2¢ above it – whichever is stricter applies


class PaperBroker:
    def __init__(self, ledger: Ledger, sizing: SizingConfig, filters: Filters, risk: Risk, slip: Slippage, db_dir: Path,
                 get_book: Callable, market_info: Callable, log=print):
        self.l, self.sizing, self.f, self.risk, self.slip, self.dir = ledger, sizing, filters, risk, slip, db_dir
        self.get_book, self.market_info, self.log = get_book, market_info, log

    def handle(self, key: str, t: dict, now: float | None = None) -> str:
        """Handle one leader trade. Returns the action: bought / sold / skip."""
        now = now or time.time()
        side, token, cid = t["side"], str(t["token_id"]), (t.get("condition_id") or "").lower()
        price, size, wallet = float(t["price"]), float(t["size"]), t.get("proxy_wallet")
        lead_before = self.l.leader_pos(wallet, token)
        try:
            return self._handle(key, t, side, token, cid, price, size, wallet, lead_before, now)
        finally:                                                  # the leader's position is tracked from the stream of his trades
            self.l.move_leader_pos(wallet, token, size if side == "BUY" else -size)

    def _handle(self, key, t, side, token, cid, price, size, wallet, lead_before, now) -> str:
        info = self.market_info(cid)
        why = check_signal(self.f, side, now - t["timestamp"], price, info, now)
        if why:
            self.l.decide(key, "skip", why, leader_price=price)
            return "skip"
        if side == "BUY":
            return self._buy(key, t, token, cid, price, size, info)
        return self._sell(key, t, token, cid, price, size, lead_before, info)

    def _buy(self, key, t, token, cid, price, size, info) -> str:
        pos = self.l.position(token)
        cur = pos["cost"] if pos and pos["closed"] is None else 0.0
        want = calculate_order_size(self.sizing, size * price, self.l.cash(), cur)
        if want.final_amount <= 0:
            self.l.decide(key, "skip", want.reasoning, want_usd=0, leader_price=price)
            return "skip"
        why = check_risk(self.risk, want.final_amount, cid, self.l, self.dir)
        if why:
            self.l.decide(key, "skip", why, want_usd=want.final_amount, leader_price=price)
            return "skip"
        book, meta = self.get_book(token)
        best_bid = max((x.price for x in book.bids), default=None)
        best_ask = min((x.price for x in book.asks), default=None)
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
        limit = min(price * (1 + self.slip.rel), price + self.slip.abs, 0.99)
        ctx = dict(want_usd=want.final_amount, best_bid=best_bid, best_ask=best_ask, mid=mid, leader_price=price, limit_price=limit)
        if best_ask is None:
            self.l.decide(key, "skip", "no sellers in the order book", **ctx)
            return "skip"
        fill = simulate_buy_fill(book, want.final_amount, 0, order_type="fak", max_price=limit, fee_rate=info.get("fee_rate") or 0.0)
        if fill.total_shares <= 0:
            self.l.decide(key, "skip", f"best price {best_ask:.2f} is above the limit {limit:.2f}", **ctx)
            return "skip"
        if fill.total_shares < meta.get("min_shares", 0) or fill.total_cost < 1.0:
            self.l.decide(key, "skip", f"below the minimum order ({fill.total_shares:.1f} shares, ${fill.total_cost:.2f})", **ctx)
            return "skip"
        with self.l.atomic() as db:
            did = db.execute("INSERT INTO decisions (key, ts, action, reason, want_usd, best_bid, best_ask, mid, leader_price, limit_price) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (key, time.time(), "buy", want.reasoning, *ctx.values())).lastrowid
            db.execute("INSERT INTO fills (decision_id, ts, side, token, cid, outcome, shares, avg_price, cost, fee, slippage_bps, levels, partial, leader_price) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (did, time.time(), "BUY", token, cid, t.get("outcome"), fill.total_shares, fill.avg_price,
                                                                fill.total_cost, fill.fee, fill.slippage_bps, fill.levels_filled, int(fill.is_partial), price))
            db.execute("UPDATE account SET cash = cash - ?", (fill.total_cost + fill.fee,))
            db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(token) DO UPDATE SET "
                       "shares = shares + excluded.shares, cost = cost + excluded.cost, closed = NULL",
                       (token, cid, t.get("outcome"), t.get("outcome_index"), t.get("title"), fill.total_shares, fill.total_cost + fill.fee, time.time()))
        self.log(f"   + bought {fill.total_shares:.1f} {t.get('outcome')} at {fill.avg_price:.3f} (leader {price:.3f}) · {t.get('title', '')[:60]}")
        return "bought"

    def _sell(self, key, t, token, cid, price, size, lead_before, info) -> str:
        pos = self.l.position(token)
        if not pos or pos["shares"] <= 1e-9 or pos["closed"] is not None:
            self.l.decide(key, "skip", "we do not hold this position", leader_price=price)
            return "skip"
        frac = 1.0 if lead_before <= size * 1.0001 else size / lead_before      # the leader exited fully – we exit fully
        shares = pos["shares"] * frac
        book, meta = self.get_book(token)
        best_bid = max((x.price for x in book.bids), default=None)
        best_ask = min((x.price for x in book.asks), default=None)
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
        floor = max(price * (1 - self.slip.rel), price - self.slip.abs, 0.01)
        ctx = dict(want_shares=shares, best_bid=best_bid, best_ask=best_ask, mid=mid, leader_price=price, limit_price=floor)
        fill = simulate_sell_fill(book, shares, 0, order_type="fak", min_price=floor, fee_rate=info.get("fee_rate") or 0.0)
        if fill.total_shares <= 0:
            self.l.decide(key, "skip", f"best bid {best_bid if best_bid is not None else '–'} is below the floor {floor:.2f}", **ctx)
            return "skip"
        with self.l.atomic() as db:
            did = db.execute("INSERT INTO decisions (key, ts, action, reason, want_shares, best_bid, best_ask, mid, leader_price, limit_price) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (key, time.time(), "sell", f"leader sold {frac:.0%} of the position", *ctx.values())).lastrowid
            db.execute("INSERT INTO fills (decision_id, ts, side, token, cid, outcome, shares, avg_price, cost, fee, slippage_bps, levels, partial, leader_price) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (did, time.time(), "SELL", token, cid, t.get("outcome"), fill.total_shares, fill.avg_price,
                                                                fill.total_cost, fill.fee, fill.slippage_bps, fill.levels_filled, int(fill.is_partial), price))
            db.execute("UPDATE account SET cash = cash + ?", (fill.total_cost - fill.fee,))
            left = pos["shares"] - fill.total_shares
            db.execute("UPDATE positions SET shares = ?, cost = cost * ?, closed = ? WHERE token = ?",
                       (max(left, 0.0), max(left, 0.0) / pos["shares"], time.time() if left <= 1e-6 else None, token))
        self.log(f"   − sold {fill.total_shares:.1f} {t.get('outcome')} at {fill.avg_price:.3f} (leader {price:.3f})")
        return "sold"


def settle(ledger: Ledger, market_info: Callable, log=print) -> int:
    """Resolved markets: a $1 payout per share of the winning outcome, the position is closed."""
    n = 0
    for p in ledger.open_positions():
        info = market_info(p["cid"], max_age=60)
        w = info.get("winner_index") if info else None
        if w is None or w != w:
            continue
        payout = p["shares"] if p["outcome_index"] == int(w) else 0.0
        with ledger.atomic() as db:
            db.execute("UPDATE account SET cash = cash + ?", (payout,))
            db.execute("UPDATE positions SET payout = ?, closed = ? WHERE token = ?", (payout, time.time(), p["token"]))
        log(f"   ✓ resolved: {p['title'][:50] if p['title'] else p['token'][:10]} – {'won' if payout else 'lost'}, payout ${payout:.2f} on an entry of ${p['cost']:.2f}")
        n += 1
    return n
