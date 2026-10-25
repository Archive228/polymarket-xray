"""Leg pairs and timing to the end of the market.

Ported from ent0n29/polybot @ 51e5362 (MIT, Copyright (c) 2024-2025 Polybot Contributors):
- research/replication_score_orders.py: _nearest_opposite_delta_seconds, _imbalance_frac – 1-to-1;
- analytics-service/clickhouse/init/005_position_ledger.sql: user_complete_sets_by_market and user_complete_sets_summary –
  SQL rewritten in pandas;
- research/snapshot_report.py: _series and the "seconds to the end" buckets.
Full license text – THIRD_PARTY_LICENSES/ent0n29_polybot.txt.

pmx: instead of the outcome names Up/Down/Yes/No – outcome_index 0/1, so it works on any binary market; the "lock"
gains the leftover directional bet and the share of profit that is locked in pairs; the series and lifetime are taken from the slug of
any xxx-updown-<window>-<start time>, the time buckets are shared by short and long markets."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd


def nearest_opposite_delta_seconds(times_a: np.ndarray, times_b: np.ndarray) -> np.ndarray:
    """For each trade in a – the signed distance to the nearest-in-time trade in b. b is sorted."""
    if times_a.size == 0 or times_b.size == 0:
        return np.array([], dtype="float64")
    times_a = np.asarray(times_a, dtype="float64")
    times_b = np.asarray(times_b, dtype="float64")
    j = np.searchsorted(times_b, times_a)
    prev_valid = j > 0
    next_valid = j < times_b.size
    prev_idx = np.where(prev_valid, j - 1, 0)
    next_idx = np.where(next_valid, j, times_b.size - 1)
    delta_prev = times_b[prev_idx] - times_a
    delta_next = times_b[next_idx] - times_a
    choose_prev = np.abs(delta_prev) <= np.abs(delta_next)
    return np.where(~prev_valid, delta_next, np.where(~next_valid, delta_prev, np.where(choose_prev, delta_prev, delta_next)))


def imbalance_frac(up: float, down: float) -> float:
    denom = up + down
    if denom <= 0:
        return float("nan")
    return abs(up - down) / denom


def pairing(t: pd.DataFrame) -> dict:
    """How often and how fast he buys the second side of the same market."""
    b = t[(t["side"] == "BUY") & t["outcome_index"].isin([0, 1])]
    if not len(b):
        return {}
    b = b.sort_values(["condition_id", "timestamp"], kind="stable")
    legs = b.groupby("condition_id")["outcome_index"].nunique()
    both = set(legs[legs == 2].index)
    deltas, imb = [], []
    for cid, g in b[b["condition_id"].isin(both)].groupby("condition_id", sort=False):
        ts, oi, sh = g["timestamp"].to_numpy(dtype=float), g["outcome_index"].to_numpy(), g["size"].to_numpy(dtype=float)
        a0, a1 = ts[oi == 0], ts[oi == 1]
        deltas.append(np.abs(nearest_opposite_delta_seconds(a0, a1)))
        deltas.append(np.abs(nearest_opposite_delta_seconds(a1, a0)))
        imb.append(imbalance_frac(sh[oi == 0].sum(), sh[oi == 1].sum()))
    d = np.concatenate(deltas) if deltas else np.array([])
    q = lambda x, p: round(float(np.quantile(x, p)), 1) if len(x) else None
    return {"markets_with_buys": int(len(legs)), "markets_both_legs": int(len(both)), "both_legs_share": round(len(both) / len(legs), 3),
            "opposite_leg_delay_sec": {"p50": q(d, .5), "p90": q(d, .9), "share_within_60s": round(float((d <= 60).mean()), 3) if len(d) else None},
            "imbalance_median": round(float(np.nanmedian(imb)), 3) if imb else None}


def complete_sets(t: pd.DataFrame, realized: pd.DataFrame | None = None) -> dict:
    """The "lock": for a market where both sides were bought, matched = min(shares 0, shares 1); edge per share = 1 − (avg price 0 + avg price 1).
    A positive edge is profit that arrives under any outcome (before fees). realized – realized PnL per leg
    (condition_id, realized) for resolved markets: pmx uses it to compute what share of profit is locked in pairs."""
    b = t[(t["side"] == "BUY") & t["outcome_index"].isin([0, 1])]
    if not len(b):
        return {}
    usd = b["size"] * b["price"]
    p = pd.DataFrame({"cid": b["condition_id"], "oi": b["outcome_index"].astype(int), "sh": b["size"], "usd": usd}) \
        .pivot_table(index="cid", columns="oi", values=["sh", "usd"], aggfunc="sum", fill_value=0.0)
    p.columns = [f"{a}{c}" for a, c in p.columns]
    for c in ("sh0", "sh1", "usd0", "usd1"):
        if c not in p:
            p[c] = 0.0
    p = p[(p["sh0"] > 0) & (p["sh1"] > 0)].copy()
    if not len(p):
        return {"markets": 0}
    p["matched"] = np.minimum(p["sh0"], p["sh1"])
    p["edge_per_share"] = 1 - (p["usd0"] / p["sh0"] + p["usd1"] / p["sh1"])
    p["edge_pnl"] = p["matched"] * p["edge_per_share"]
    p["leftover_sh"] = (p["sh0"] - p["sh1"]).abs()                    # pmx: leftover directional bet
    tot_sh = p["sh0"] + p["sh1"]
    out = {"markets": int(len(p)), "positive_edge_markets": int((p["edge_per_share"] > 0).sum()),
           "total_matched_shares": round(float(p["matched"].sum()), 0), "total_edge_pnl": round(float(p["edge_pnl"].sum()), 0),
           "avg_edge_per_share": round(float(p["edge_pnl"].sum() / p["matched"].sum()), 4) if p["matched"].sum() else None,
           "leftover_share_of_shares": round(float(p["leftover_sh"].sum() / tot_sh.sum()), 3)}
    if realized is not None and len(realized):
        r = realized.groupby("condition_id")["realized"].sum()
        done = p.index.intersection(r.index)
        if len(done):
            locked, total = float(p.loc[done, "edge_pnl"].sum()), float(r.loc[done].sum())
            out.update({"resolved_markets": int(len(done)), "pnl_in_paired_markets": round(total, 0), "locked_pnl": round(locked, 0),
                        "directional_pnl": round(total - locked, 0)})
    return out


# ---------- time to the end: series and window from the slug, otherwise end_date ----------
UPDOWN = re.compile(r"^(?P<asset>[a-z0-9]+)-updown-(?P<n>\d+)(?P<u>[mh])-(?P<ts>\d{9,11})$")
TTE_BUCKETS = [(60, "<1 min"), (180, "1–3 min"), (300, "3–5 min"), (600, "5–10 min"), (900, "10–15 min"), (1800, "15–30 min"),
               (3600, "30–60 min"), (6 * 3600, "1–6 h"), (24 * 3600, "6–24 h"), (float("inf"), "> 1 day")]


def slug_window(slug: str):
    """btc-updown-5m-1792800000 → (series 'btc-updown-5m', start, end). The time in the slug is the window start."""
    m = UPDOWN.match(str(slug or ""))
    if not m:
        return None, None, None
    sec = int(m["n"]) * (60 if m["u"] == "m" else 3600)
    start = int(m["ts"])
    return f"{m['asset']}-updown-{m['n']}{m['u']}", start, start + sec


def timing_to_end(t: pd.DataFrame, mk: pd.DataFrame) -> dict:
    """How many seconds were left until trading ended at the moment of purchase: share of trades, median size, average price per bucket."""
    b = t[t["side"] == "BUY"]
    if not len(b) or "slug" not in mk:
        return {}
    w = mk[["condition_id", "slug", "end_date"]].copy()
    sw = w["slug"].map(slug_window)
    w["series"] = sw.map(lambda x: x[0])
    w["end_ts"] = sw.map(lambda x: x[2]).astype("float64")
    ed = pd.to_datetime(w["end_date"], utc=True, errors="coerce")
    w["end_ts"] = w["end_ts"].fillna(ed.map(lambda x: x.timestamp() if pd.notna(x) else np.nan))
    b = b.merge(w[["condition_id", "series", "end_ts"]], on="condition_id", how="left")
    b = b[b["end_ts"].notna()]
    if not len(b):
        return {}
    tte = b["end_ts"] - b["timestamp"]
    after = float((tte < 0).mean())
    b = b[tte >= 0].assign(tte=tte[tte >= 0])
    edges = [0] + [e for e, _ in TTE_BUCKETS]
    b["bucket"] = pd.cut(b["tte"], edges, right=False, labels=[n for _, n in TTE_BUCKETS])
    usd = b["size"] * b["price"]
    rows = []
    for name, g in b.groupby("bucket", observed=True):
        rows.append({"bucket": str(name), "share_trades": round(len(g) / len(b), 3), "share_usd": round(float(usd[g.index].sum() / usd.sum()), 3),
                     "median_shares": round(float(g["size"].median()), 1), "avg_price": round(float(usd[g.index].sum() / g["size"].sum()), 3)})
    short = b[b["series"].notna()]
    med = float(b["tte"].median())
    avg_px = float(usd.sum() / b["size"].sum())
    qs = {f"p{int(q * 100)}": round(float(b["tte"].quantile(q)), 0) for q in (.1, .25, .5, .75, .9)}
    return {"buckets": rows, "median_seconds_to_end": round(med, 0), "quantiles_sec": qs, "after_end_share": round(after, 3),
            "short_series": short["series"].value_counts(normalize=True).round(3).head(6).to_dict() if len(short) else {},
            # "sniper": buys expensive things in the last minutes (an idea from PipiClaw, without code: average price ≥ 0.75 and median ≤ 2 min)
            "sniper": bool(avg_px >= 0.75 and med <= 120), "avg_buy_price": round(avg_px, 3)}
