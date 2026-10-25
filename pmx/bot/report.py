"""Paper bot report: the copy's lag, what was copied and why things were skipped, the copy price against the leader's price, the account result."""
from __future__ import annotations

import numpy as np

from .ledger import Ledger


def summary(l: Ledger, get_book=None) -> dict:
    q = lambda sql, *a: l.db.execute(sql, a).fetchall()
    lags = np.array([r["lag"] for r in q("SELECT lag FROM leader_trades WHERE status = 'signal'")], dtype=float)
    dec = {f"{r['action']}: {r['reason'][:70]}": r["n"] for r in q("SELECT action, reason, COUNT(*) n FROM decisions GROUP BY action, reason ORDER BY n DESC")}
    fills = q("SELECT side, shares, avg_price, cost, fee, leader_price FROM fills")
    # how much worse than the leader: a buy – above his price, a sell – below it; in cents per share
    worse = [((f["avg_price"] - f["leader_price"]) if f["side"] == "BUY" else (f["leader_price"] - f["avg_price"])) * 100 for f in fills]
    pos = l.open_positions()
    mark = 0.0
    for p in pos:
        mid = None
        if get_book:
            book, _ = get_book(p["token"])
            bb, ba = max((x.price for x in book.bids), default=None), min((x.price for x in book.asks), default=None)
            mid = (bb + ba) / 2 if bb is not None and ba is not None else bb
        mark += p["shares"] * (mid if mid is not None else p["cost"] / max(p["shares"], 1e-9))
    closed = q("SELECT cost, payout FROM positions WHERE closed IS NOT NULL AND payout IS NOT NULL")
    equity = l.cash() + mark
    return {"leader_trades_seen": int(len(lags)),
            "lag_sec": {"p50": round(float(np.median(lags)), 1), "p90": round(float(np.quantile(lags, .9)), 1), "max": round(float(lags.max()), 1)} if len(lags) else None,
            "decisions": dec, "fills": len(fills), "fees": round(sum(f["fee"] for f in fills), 2),
            "cents_worse_than_leader": {"mean": round(float(np.mean(worse)), 2), "p90": round(float(np.quantile(worse, .9)), 2)} if worse else None,
            "open_positions": len(pos), "resolved_positions": len(closed),
            "resolved_pnl": round(sum(c["payout"] - c["cost"] for c in closed), 2),
            "start_cash": round(l.start_cash(), 2), "cash": round(l.cash(), 2), "open_value_mid": round(mark, 2), "equity": round(equity, 2),
            "pnl": round(equity - l.start_cash(), 2)}


def text(s: dict) -> str:
    L = [f"leader trades seen: {s['leader_trades_seen']}"]
    if s["lag_sec"]:
        L.append(f"lag from his trade to our reaction: median {s['lag_sec']['p50']} s, 90% – up to {s['lag_sec']['p90']} s, max {s['lag_sec']['max']} s")
    L.append(f"fills: {s['fills']}, fees ${s['fees']}")
    if s["cents_worse_than_leader"]:
        m = s["cents_worse_than_leader"]["mean"]
        L.append(f"our price is {'worse' if m > 0 else 'better'} than his by {abs(m)}¢ per share on average (for 90% of fills – no worse than {s['cents_worse_than_leader']['p90']}¢)")
    L.append("decisions:")
    L += [f"   {n:>5}  {k}" for k, n in list(s["decisions"].items())[:12]]
    L.append(f"positions: {s['open_positions']} open, {s['resolved_positions']} resolved (result on resolved ${s['resolved_pnl']})")
    L.append(f"account: start ${s['start_cash']}, cash ${s['cash']}, open positions at the order-book midpoint ${s['open_value_mid']}, "
             f"total ${s['equity']} ({'+' if s['pnl'] >= 0 else '−'}${abs(s['pnl'])})")
    return "\n".join(L)
