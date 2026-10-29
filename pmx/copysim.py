"""Copyability: what would happen if we repeated the wallet's trades with a delay.

For a sample of resolved positions we take each of his trades (buy and sell) and repeat it at the market price
(CLOB prices-history, a 1-minute step): in the same minute and 1, 5, 15, 60 minutes later. The copier is always a taker and pays the
fee. We hold as he held and receive the payout at resolution. → copysim.json

Rows:
- "himself" – his prices and his fees (a maker pays nothing);
- "right after him" – a copy in the same minute. The difference from "himself" is the entry price (his maker orders, the spread)
  and the fee, not the delay;
- "+N min" – a copy made late. If the market is no longer trading by then, the purchase did not happen – "too late":
  that money is not part of the return, but next to it we show what share of his money we managed to repeat.

The copy price is conservative. Minute history lags behind a jump: he buys at 99¢ at the moment of the news, while the history
still shows 66¢ for a few seconds. So the market price is the median of the points in the 2 minutes after the copy moment, and the copier buys
no cheaper than he did and sells no higher than he did: he has already taken the best orders. The estimate can only be worse than reality.

The sample – the latest positions, equally from each price bucket (so as not to lose the rare cheap entries), and the overall
result is weighted by how much money he actually puts into each bucket.

Limitations: history is the market price, not the order book, so the slippage of large orders is not accounted for; the
return eaten by each 1¢ of slippage is computed separately."""
from __future__ import annotations

import bisect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from . import api
from .analyze import TRADE_COLS, _bucket
from .markets import fee

DELAYS = [1, 5, 15, 60]
MODES = ["his", "m0"] + [f"m{d}" for d in DELAYS]
LABELS = {"his": "himself", "m0": "right after him", **{f"m{d}": f"+{d} min" for d in DELAYS}}
TOL = 600          # a history price older than 10 minutes is no longer a price


def _price_at(times: list, prices: list, ts: float, stop: float):
    """Market price at moment ts: the median of the history points in the 2 minutes after ts (single outliers of a thin market are smoothed out).
    A price from before ts will not do: he often buys at the moment of a move, and the price a minute earlier is the pre-news price.
    None – the market is not trading at that moment or the history has a gap."""
    if ts > stop:
        return None
    i = bisect.bisect_left(times, ts)
    if i >= len(times) or times[i] - ts > TOL:
        return None
    j = bisect.bisect_right(times, max(ts + 120, times[i]))
    w = sorted(prices[i:j])
    return w[len(w) // 2]


def copysim(d: Path, sample: int = 300, log=print) -> dict:
    t = pd.read_csv(d / "trades.csv", low_memory=False, usecols=lambda c: c in TRADE_COLS)
    mk = pd.read_csv(d / "markets.csv", low_memory=False)
    t["condition_id"] = t["condition_id"].str.lower()
    mk["condition_id"] = mk["condition_id"].str.lower()
    t = t.merge(mk[["condition_id", "winner_index", "category", "fee_rate", "closed_time"]], on="condition_id", how="left")
    t = t[t["winner_index"].notna()].copy()
    t["fee_rate"] = t["fee_rate"].fillna(0)
    t["usd"] = t["size"] * t["price"]
    buy = t["side"] == "BUY"
    tok = t.assign(b_usd=t["usd"].where(buy, 0), b_sh=t["size"].where(buy, 0), s_sh=t["size"].where(~buy, 0)).groupby("token_id").agg(
        buy_usd=("b_usd", "sum"), buy_sh=("b_sh", "sum"), sell_sh=("s_sh", "sum"), first_ts=("timestamp", "min"), last_ts=("timestamp", "max"),
        cat=("category", "first"), won=("outcome_index", "first"), winner=("winner_index", "first"), rate=("fee_rate", "first"),
        closed=("closed_time", "first")).reset_index()
    # only positions built entirely from his trades (no shares from splits and conversions)
    tok = tok[(tok["buy_usd"] >= 5) & (tok["sell_sh"] <= tok["buy_sh"] * 1.05)].copy()
    if not len(tok):
        return {}
    tok["won"] = tok["won"] == tok["winner"]
    tok["bucket"] = (tok["buy_usd"] / tok["buy_sh"]).apply(_bucket)
    weights = (tok.groupby("bucket")["buy_usd"].sum() / tok["buy_usd"].sum()).to_dict()
    tok = tok.sort_values("last_ts", ascending=False)
    per = max(sample // max(tok["bucket"].nunique(), 1), 10)
    pick = pd.concat([x.head(per) for _, x in tok.groupby("bucket")])
    log(f"copyability: {len(pick)} positions in the sample out of {len(tok)}")
    fills_by = {k: v.sort_values("timestamp") for k, v in t[t["token_id"].isin(set(pick["token_id"]))].groupby("token_id")}

    def one(row):
        fills = fills_by[row.token_id]
        hist = api.prices_history(str(row.token_id), int(row.first_ts) - 900, int(row.last_ts) + max(DELAYS) * 60 + 900, fidelity=1)
        out = {"token_id": str(row.token_id), "bucket": row.bucket, "cat": row.cat}
        if not hist:
            out["no_history"] = True
            return out
        times, prices = [h["t"] for h in hist], [float(h["p"]) for h in hist]
        stop = times[-1] + 120
        closed = pd.to_datetime(row.closed, utc=True, errors="coerce")
        if closed is not None and not pd.isna(closed):
            stop = min(stop, closed.timestamp())
        for mode in MODES:
            dly = None if mode == "his" else int(mode[1:]) * 60
            cost = proceeds = sh = his_buy = copied_buy = bought = 0.0
            for f in fills.itertuples():
                if dly is None:
                    p, taker = f.price, not f.is_maker
                else:
                    p, taker = _price_at(times, prices, f.timestamp + dly, stop), True
                    if p is not None:              # no better than him: a buy no cheaper, a sell no higher
                        p = max(p, f.price) if f.side == "BUY" else min(p, f.price)
                if f.side == "BUY":
                    his_buy += f.size * f.price
                    if p is None:
                        continue                    # too late: the market is no longer trading
                    copied_buy += f.size * f.price
                    cost += f.size * p + (fee(f.size, p, row.rate) if taker else 0.0)
                    sh += f.size
                    bought += f.size
                elif p is not None and sh > 0:
                    s = min(f.size, sh)              # we sell no more than we managed to buy
                    proceeds += s * p - (fee(s, p, row.rate) if taker else 0.0)
                    sh -= s
            out[mode] = {"cost": cost, "pnl": proceeds + (sh if row.won else 0.0) - cost, "his_buy": his_buy,
                         "copied_buy": copied_buy, "bought_sh": bought}
        return out

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, pick.itertuples()))
    ok = [r for r in rows if not r.get("no_history")]

    def stat(rs: list, mode: str) -> dict:
        v = [r[mode] for r in rs]
        cost = sum(x["cost"] for x in v)
        hb = sum(x["his_buy"] for x in v)
        return {"positions": sum(1 for x in v if x["cost"] > 0), "cost": cost, "pnl": sum(x["pnl"] for x in v),
                "roi": sum(x["pnl"] for x in v) / cost if cost else None,
                "copied_share": sum(x["copied_buy"] for x in v) / hb if hb else None,
                "slip_pp_per_cent": sum(x["bought_sh"] for x in v) * 0.01 / cost * 100 if cost else None}

    by_bucket = {b: {m: stat([r for r in ok if r["bucket"] == b], m) for m in MODES} for b in sorted({r["bucket"] for r in ok})}

    def weighted(mode: str) -> dict:
        num = {"roi": 0.0, "copied_share": 0.0, "slip_pp_per_cent": 0.0}
        den = {k: 0.0 for k in num}
        for b, s in by_bucket.items():
            w = weights.get(b, 0)
            for k in num:
                if s[mode][k] is not None:
                    num[k] += w * s[mode][k]
                    den[k] += w
        res = {k: round(num[k] / den[k], 4) if den[k] else None for k in num}
        res["positions"] = sum(s[mode]["positions"] for s in by_bucket.values())
        res["weight_covered"] = round(sum(weights.get(b, 0) for b in by_bucket), 3)
        return res

    rnd = lambda s: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}
    out = {"modes": MODES, "labels": LABELS, "delays_min": DELAYS, "population_positions": int(len(tok)), "sample": int(len(pick)),
           "no_history": len(rows) - len(ok), "bucket_weights": {k: round(v, 3) for k, v in weights.items()},
           "overall": {m: weighted(m) for m in MODES},
           "by_bucket": {b: {m: rnd(s) for m, s in v.items()} for b, v in by_bucket.items()},
           "by_category": {c: {m: rnd(stat([r for r in ok if str(r["cat"]) == c], m)) for m in MODES} for c in sorted({str(r["cat"]) for r in ok})},
           "limits": ["history price is the market price (per minute), not the order book: slippage of large orders is not accounted for – see 'per 1¢ of slippage'",
                      "the copier is always a taker: it pays the fee and does not repeat his maker orders at his price",
                      "the sample is the latest positions from each bucket; the overall result is weighted by his money in the buckets",
                      "positions without minute history (old markets) are not included in the calculation",
                      "positions with shares from splits and conversions are not modeled"]}
    (d / "copysim.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out
