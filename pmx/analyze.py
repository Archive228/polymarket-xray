"""Wallet strategy analysis from the export → analysis.json.

Sections: passport, where the money comes from, where he trades, at what price he enters and where the edge is, when he enters and how long he holds,
bet size, bot signals, the PnL curve, best and worst markets, the strategy type with evidence, limitations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import stats as S
from .export import dedup_activity, rebuild_markets
from .markets import category, fee
from .pairs import complete_sets, pairing, timing_to_end
from .recipe import build as recipe_build
from .recipe import to_md as recipe_md
from .style import apply_ops_override, detect_carry, detect_style, flatten

BUCKETS =[(0, .05, "≤5¢"), (.05, .20, "6–20¢"), (.20, .50, "21–50¢"), (.50, .80, "51–80¢"), (.80, .95, "81–95¢"), (.95, 1.01, "96–99¢")]


def _bucket(p: float) -> str:
    for lo, hi, n in BUCKETS:
        if lo < p <= hi or (lo == 0 and p <= hi):
            return n
    return "?"


def _ts(s) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def _r(x, n=2):
    try:
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)
    except Exception:
        return None


TRADE_COLS = {"side", "token_id", "condition_id", "size", "price", "timestamp", "outcome", "outcome_index", "is_maker", "transaction_hash"}
ACT_COLS = {"type", "side", "usdc_size", "size", "price", "condition_id", "token_id", "timestamp", "transaction_hash", "outcome_index"}


def load(d: Path) -> dict:
    # for big wallets the trades weigh a gigabyte because of text columns (titles, icons) – read only what is needed
    cols = {"trades": TRADE_COLS, "activity": ACT_COLS}
    rd = lambda n: (pd.read_csv(d / f"{n}.csv", low_memory=False, usecols=(lambda c: c in cols[n]) if n in cols else None)
                    if (d / f"{n}.csv").exists() else pd.DataFrame())
    mk = rd("markets")
    if len(mk) and "fee_rate" not in mk.columns:          # an export from an old version – rebuild the markets from the cache
        mk = rebuild_markets(d)
    if len(mk):                                            # category by the current rules, not the rules at the time of the export
        mk["category"] = [category({"question": q, "slug": sl, "sportsMarketType": st if isinstance(st, str) else None,
                                    "gameStartTime": gs if isinstance(gs, str) else None, "series_slug": se if isinstance(se, str) else None})
                          for q, sl, st, gs, se in zip(mk["question"], mk["slug"], mk.get("sports_type", [None] * len(mk)), mk["game_start"],
                                                       mk.get("series_slug", [None] * len(mk)))]
    pos = rd("positions")
    if len(pos) and "token_id" in pos.columns:             # one position arrives under several statuses
        pos = pos.drop_duplicates("token_id", keep="last")
    return {"trades": rd("trades"), "activity": dedup_activity(rd("activity")), "positions": pos, "markets": mk, "pnl": rd("pnl"),
            "stats": json.loads((d / "stats.json").read_text()) if (d / "stats.json").exists() else {},
            "lb": json.loads((d / "leaderboard.json").read_text()) if (d / "leaderboard.json").exists() else {},
            "meta": json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}}


class NoTrades(SystemExit):
    """A wallet whose export has no trades at all (profit from rewards or redemptions only)."""


def analyze(d: Path) -> dict:
    D = load(d)
    t, pos, mk, pnl, st = D["trades"], D["positions"], D["markets"], D["pnl"], D["stats"]
    if not len(t) or "size" not in t.columns:
        raise NoTrades("the wallet has no trades in the export: its profit does not come from trading, so there is nothing to read or copy")
    t = t.copy()
    t["usd"] = t["size"] * t["price"]
    t["dt"] = pd.to_datetime(t["timestamp"], unit="s", utc=True)
    t["condition_id"] = t["condition_id"].str.lower()
    mk = mk.copy()
    mk["condition_id"] = mk["condition_id"].str.lower()
    t = t.merge(mk[["condition_id", "category", "game_start", "closed_time", "end_date", "winner_index", "closed", "fee_rate", "neg_risk_market_id"]],
                on="condition_id", how="left")
    t["category"] = t["category"].fillna("other")
    # taker fee: C × rate × p × (1 − p); a maker pays nothing. On a buy it is added to the price, on a sell it is subtracted
    t["fee"] = np.where(t["is_maker"].astype(bool), 0.0, fee(t["size"], t["price"], t["fee_rate"].fillna(0)))

    # ---------- positions + outcome ----------
    pos = pos.copy()
    pos["condition_id"] = pos["condition_id"].str.lower()
    pos = pos.merge(mk[["condition_id", "category", "winner_index", "closed", "closed_time", "game_start"]], on="condition_id", how="left")
    pos["category"] = pos["category"].fillna("other")
    # Our own accounting from trades, token by token: bought – sold – received at resolution, with fees. This shows where exactly
    # the profit is, and the numbers can be checked against the trades. Splits, merges and conversions are not included – their share is computed below
    # in "reliability", and if it is noticeable, the buckets are marked unreliable.
    buy, sell = t["side"] == "BUY", t["side"] == "SELL"
    t["buy_px"] = np.where(buy, t["usd"], 0.0)                       # what the shares cost at the price – for the average entry price
    t["buy_usd"] = np.where(buy, t["usd"] + t["fee"], 0.0)           # how much he actually paid
    t["sell_usd"] = np.where(sell, t["usd"] - t["fee"], 0.0)         # how much he actually received
    t["buy_sh"] = np.where(buy, t["size"], 0.0)
    t["sell_sh"] = np.where(sell, t["size"], 0.0)
    led = t.groupby(["condition_id", "token_id", "outcome_index", "outcome"], dropna=False).agg(
        buy_px=("buy_px", "sum"), buy_usd=("buy_usd", "sum"), sell_usd=("sell_usd", "sum"), buy_sh=("buy_sh", "sum"), sell_sh=("sell_sh", "sum"),
        fee=("fee", "sum"), first_ts=("timestamp", "min"), last_ts=("timestamp", "max"), fills=("timestamp", "size"), maker=("is_maker", "mean")).reset_index()
    led = led.merge(mk[["condition_id", "category", "winner_index", "closed_time", "end_date", "title" if "title" in mk else "question"]], on="condition_id", how="left")
    led["category"] = led["category"].fillna("other")
    led["resolved"] = led["winner_index"].notna()
    led["won"] = led["outcome_index"] == led["winner_index"]
    led["left_sh"] = (led["buy_sh"] - led["sell_sh"]).clip(lower=0)
    # sold more than bought, or only sales: the shares did not come from trades (split, conversion) – the cost basis is unknown
    led["unrecoverable"] = (led["sell_sh"] > led["buy_sh"] * 1.05 + 1e-9)
    # realized per leg: at resolution – the payout; in an unresolved market – sales minus the cost of what was sold
    avg_cost = led["buy_usd"] / led["buy_sh"].replace(0, np.nan)
    led["realized"] = np.where(led["resolved"], led["sell_usd"] + np.where(led["won"], led["left_sh"], 0.0) - led["buy_usd"],
                               led["sell_usd"] - avg_cost.fillna(0) * np.minimum(led["sell_sh"], led["buy_sh"]))
    res = led[led["resolved"] & (led["buy_usd"] >= 1)].copy()        # resolved markets, without dust < $1
    res["payout"] = np.where(res["won"], res["left_sh"], 0.0)
    res["pnl"] = res["realized"]
    res["cost"] = res["buy_usd"]
    res["avg_price"] = res["buy_px"] / res["buy_sh"].replace(0, np.nan)    # entry price without the fee – the one the market "promises"
    res["held_share"] = res["left_sh"] / res["buy_sh"].replace(0, np.nan)
    res["entry_bucket"] = res["avg_price"].apply(lambda p: _bucket(p) if p == p else "?")
    res["first_entry_at"] = res["first_ts"]
    res["total_pnl"] = res["pnl"]

    # ---------- 1. passport and where the money comes from ----------
    ap = st.get("all_time_pnl") or {}
    join = datetime.fromtimestamp(st["join_date"], timezone.utc) if st.get("join_date") else None
    first, last = t["dt"].min(), t["dt"].max()
    active_days = int(t["dt"].dt.date.nunique()) if len(t) else 0
    income = {k: _r(ap.get(k)) for k in ("realized_market_pnl", "unrealized_pnl", "reward_income", "maker_rebate", "taker_rebate", "fees_paid", "yield_income", "referral_income")}
    side_income = (ap.get("reward_income") or 0) + (ap.get("maker_rebate") or 0) + (ap.get("taker_rebate") or 0)
    passport = {"wallet": D["meta"].get("wallet"), "name": D["meta"].get("name"), "join_date": join.date().isoformat() if join else None,
                # user-stats.trades is the number of markets, not trades; trades are in all_time_pnl.trade_count
                "trades_all_time": ap.get("trade_count"), "markets_all_time": st.get("trades"), "trades_exported": int(len(t)),
                "first_trade": str(first)[:16], "last_trade": str(last)[:16],
                "active_days": active_days, "volume_usdc": _r(ap.get("volume_usdc"), 0), "biggest_win": _r(st.get("biggest_win"), 0),
                "pnl_realized": _r(ap.get("realized_pnl"), 0), "pnl_economic": _r(ap.get("economic_pnl"), 0),
                "income_breakdown": income,
                "fees_paid_official": _r(ap.get("fees_paid"), 0), "fees_by_formula": _r(-t["fee"].sum(), 0),
                "share_from_rewards_and_rebates": _r(side_income / max(abs(ap.get("economic_pnl") or 0), 1e-9), 3) if ap.get("economic_pnl") else None,
                "leaderboard": D["lb"]}

    # ---------- money flow by activity: where the money goes, no models ----------
    act = D["activity"]
    flow = {}
    if len(act):
        a_usd = act["usdc_size"].astype(float)
        for ty in ("TRADE", "REDEEM", "MERGE", "SPLIT", "CONVERSION", "REWARD", "MAKER_REBATE", "TAKER_REBATE", "YIELD", "REFERRAL_REWARD"):
            m = act["type"] == ty
            if ty == "TRADE":
                for sd in ("BUY", "SELL"):
                    ms = m & (act["side"] == sd)
                    flow[f"TRADE_{sd}"] = {"rows": int(ms.sum()), "usd": _r(a_usd[ms].sum(), 0)}
            elif m.any():
                flow[ty] = {"rows": int(m.sum()), "usd": _r(a_usd[m].sum(), 0)}
    fl = lambda k: (flow.get(k) or {}).get("usd") or 0
    buys_usd = fl("TRADE_BUY")

    # ---------- analysis reliability: how well our position-based accounting describes this wallet ----------
    touched = set()
    for ty in ("MERGE", "SPLIT"):
        if len(act):
            touched |= set(act.loc[act["type"] == ty, "condition_id"].dropna().str.lower())
    if len(act) and "neg_risk_market_id" in mk.columns:
        conv_events = set(act.loc[act["type"] == "CONVERSION", "condition_id"].dropna().str.lower())
        touched |= set(mk.loc[mk["neg_risk_market_id"].isin(conv_events), "condition_id"])
    buy_total = led["buy_usd"].sum()
    coverage = res["buy_usd"].sum() / max(buy_total, 1e-9)
    touched_share = led.loc[led["condition_id"].isin(touched), "buy_usd"].sum() / max(buy_total, 1e-9)
    unrec_fills = led.loc[led["unrecoverable"], "fills"].sum() / max(led["fills"].sum(), 1)
    ledger_realized = led["realized"].sum()
    off_realized = ap.get("realized_market_pnl")
    gap = (ledger_realized - off_realized) / max(abs(off_realized), 1.0) if off_realized is not None else None
    off_n = D["meta"].get("official_trade_count") or ap.get("trade_count")
    completeness = len(t) / off_n if off_n else None
    days = D["meta"].get("days")
    if days:                                           # only a recent period was exported – we do not compare with the all-time total
        completeness, gap = None, None
    fail, warn = [], []
    if coverage < .5:
        fail.append(f"{coverage:.0%} of buy money made it into the bucket analysis: the rest is in markets that are not resolved yet")
    elif coverage < .8:
        warn.append(f"{coverage:.0%} of buy money made it into the bucket analysis, the rest is in markets that are not resolved yet")
    if touched_share > .1:
        if gap is not None and abs(gap) <= .05:          # our own accounting matches the official one – merges did not spoil it
            warn.append(f"{touched_share:.0%} of buy money is in markets with merges, splits or conversions, but our own accounting matches the official one")
        else:
            fail.append(f"{touched_share:.0%} of buy money is in markets where he did merges, splits or conversions: trade-based accounting cannot see them")
    if unrec_fills > .15:
        fail.append(f"{unrec_fills:.0%} of fills are in positions where more was sold than bought: the shares did not come from trades")
    if gap is not None:
        if abs(gap) > .2:
            fail.append(f"our own accounting differs from the official realized PnL by {gap:+.0%}")
        elif abs(gap) > .05:
            warn.append(f"our own accounting differs from the official realized PnL by {gap:+.0%}")
    if days:
        warn.append(f"only the last {days} days were exported: no comparison with the all-time official total")
    fee_off, fee_ours = abs(ap.get("fees_paid") or 0), t["fee"].sum()
    if not days and fee_off > 100 and abs(fee_ours - fee_off) / fee_off > .15:
        warn.append(f"fees by formula (${fee_ours:,.0f}) differ from the official ones (${fee_off:,.0f}) by {(fee_ours - fee_off) / fee_off:+.0%}")
    if completeness is not None and completeness < .95:
        fail.append(f"exported {len(t):,} trades out of {off_n:,} according to Polymarket")
    reliability = {"level": "unreliable" if fail else ("approximate" if warn else "reliable"), "reasons": fail + warn,
                   "coverage_buy_usd": _r(coverage, 3), "conversion_merge_split_share": _r(touched_share, 3),
                   "unrecoverable_fills_share": _r(unrec_fills, 3), "ledger_realized": _r(ledger_realized, 0),
                   "official_realized": _r(off_realized, 0), "ledger_vs_official": _r(gap, 3),
                   "trades_exported": int(len(t)), "trades_official": off_n, "completeness": _r(completeness, 3)}

    # ---------- 2. where he trades ----------
    cats = []
    for c, g in t.groupby("category"):
        rc = res[res["category"] == c]
        cats.append({"category": c, "trades": int(len(g)), "volume_usd": _r(g["usd"].sum(), 0), "share_volume": _r(g["usd"].sum() / max(t["usd"].sum(), 1e-9), 3),
                     "markets": int(g["condition_id"].nunique()), "resolved_positions": int(len(rc)),
                     "pnl_resolved": _r(rc["pnl"].sum(), 0) if len(rc) else None,
                     "profitable_positions": _r((rc["pnl"] > 0).mean(), 3) if len(rc) else None,
                     "roi": _r(rc["pnl"].sum() / max(rc["cost"].sum(), 1e-9), 3) if len(rc) else None})
    cats.sort(key=lambda x: -x["volume_usd"])

    # ---------- 3. how he enters and where the edge is ----------
    buys = t[t["side"] == "BUY"]
    yn = buys[buys["outcome"].isin(["Yes", "No"])]
    by_bucket, tests = [], []
    for lo, hi, n in BUCKETS:
        rb = res[res["entry_bucket"] == n]
        bb = buys[buys["price"].apply(_bucket) == n]
        if not len(bb) and not len(rb):
            continue
        w = rb["cost"]
        hit_w = (rb["won"] * w).sum() / max(w.sum(), 1e-9)                  # share of money put on the outcome that won
        # at a fair price the expected hit_w is the money-weighted average price (not dollars ÷ shares: that comes out lower)
        price_w = (w * rb["avg_price"]).sum() / max(w.sum(), 1e-9)
        tst = S.excess_test(rb["avg_price"].values, rb["won"].values) if len(rb) else {"n": 0}
        tests.append(tst.get("p_value") if tst.get("n", 0) >= 30 else None)
        by_bucket.append({"bucket": n, "buy_trades_share": _r(len(bb) / max(len(buys), 1), 3), "buy_usd_share": _r(bb["usd"].sum() / max(buys["usd"].sum(), 1e-9), 3),
                          "resolved_positions": int(len(rb)), "cost": _r(w.sum(), 0), "pnl": _r(rb["pnl"].sum(), 0),
                          "avg_entry": _r(price_w, 3) if len(rb) else None,
                          "hit_rate": _r(hit_w, 3) if len(rb) else None,
                          "edge_pp": _r((hit_w - price_w) * 100, 1) if len(rb) else None,   # the outcome wins more often than the entry price "promises" (by money)
                          "test": tst,                                                       # the same by number of positions – with a chance test
                          "held_to_resolution": _r(rb["held_share"].mean(), 2) if len(rb) else None,
                          "roi": _r(rb["pnl"].sum() / max(w.sum(), 1e-9), 3) if len(rb) else None})
    for b, p_adj in zip(by_bucket, S.holm(tests)):
        b["p_adjusted"] = p_adj
        b["verdict"] = S.verdict(b["test"], p_adj)
    fills = t.groupby("token_id").size()
    entry = {"buy_share": _r(len(buys) / max(len(t), 1), 3), "maker_share_trades": _r(t["is_maker"].mean(), 3), "maker_share_usd": _r(t.loc[t["is_maker"], "usd"].sum() / max(t["usd"].sum(), 1e-9), 3),
             "no_share_of_yes_no_buys": _r((yn["outcome"] == "No").mean(), 3) if len(yn) else None, "buy_price_median": _r(buys["price"].median(), 3),
             "fills_per_position_median": _r(fills.median(), 1), "fills_per_position_p90": _r(fills.quantile(.9), 1), "by_price_bucket": by_bucket}

    # ---------- 4. when he enters and how long he holds ----------
    hours = t["dt"].dt.hour.value_counts(normalize=True).sort_index()
    wd = t["dt"].dt.dayofweek.value_counts(normalize=True).sort_index()
    gs = _ts(t["game_start"])
    rel = ((t["dt"] - gs).dt.total_seconds() / 60).dropna()
    # end of trading is end_date; closed_time is set at resolution, on average 4–10 hours later
    ct = _ts(t["end_date"]).fillna(_ts(t["closed_time"]))
    before_close_h = ((ct - t["dt"]).dt.total_seconds() / 3600).dropna()
    before_close_h = before_close_h[before_close_h >= 0]
    hold = ((_ts(res["end_date"]).fillna(_ts(res["closed_time"])) - pd.to_datetime(res["first_entry_at"], unit="s", utc=True)).dt.total_seconds() / 3600).dropna()
    hold = hold[hold >= 0]
    timing = {"hours_utc": {int(k): _r(v, 3) for k, v in hours.items()}, "weekday": {int(k): _r(v, 3) for k, v in wd.items()},
              "active_hours_of_24": int((hours > 0.005).sum()),
              "minutes_from_event_start": {"before_share": _r((rel < 0).mean(), 3), "median": _r(rel.median(), 0), "p10": _r(rel.quantile(.1), 0), "p90": _r(rel.quantile(.9), 0)} if len(rel) else None,
              "hours_before_market_close": {"median": _r(before_close_h.median(), 1), "share_last_hour": _r((before_close_h <= 1).mean(), 3), "share_last_10min": _r((before_close_h <= 1 / 6).mean(), 3)} if len(before_close_h) else None,
              "hold_hours": {"median": _r(hold.median(), 1), "p90": _r(hold.quantile(.9), 1)} if len(hold) else None}

    # ---------- 5. bet size ----------
    usd = t["usd"]
    sz = t["size"].round(2)
    mode_share = sz.value_counts(normalize=True).iloc[0] if len(sz) else 0
    from scipy.stats import spearmanr
    rho = spearmanr(buys["price"], buys["usd"]).correlation if len(buys) > 20 else None
    sizing = {"trade_usd": {q: _r(usd.quantile(v), 2) for q, v in (("p10", .1), ("median", .5), ("p90", .9), ("max", 1.0))},
              "position_cost": {q: _r(res["cost"].quantile(v), 2) for q, v in (("median", .5), ("p90", .9), ("max", 1.0))} if len(res) else None,
              "fixed_lot_share": _r(mode_share, 3), "fixed_lot_size": _r(sz.value_counts().index[0], 2) if len(sz) else None,
              "price_vs_size_spearman": _r(rho, 2)}

    # ---------- 6. bot signals ----------
    ts = np.sort(t["timestamp"].values)
    gaps = np.diff(ts) if len(ts) > 1 else np.array([])
    span_days = max((ts[-1] - ts[0]) / 86400, 1 / 24) if len(ts) > 1 else 1
    both = pos.groupby("condition_id")["outcome_index"].nunique()
    bot = {"trades_per_active_day": _r(len(t) / max(active_days, 1), 1), "trades_per_day_calendar": _r(len(t) / span_days, 1),
           "active_hours_of_24": timing["active_hours_of_24"], "gaps_under_2s_share": _r((gaps <= 2).mean(), 3) if len(gaps) else None,
           "gaps_cv": _r(gaps.std() / gaps.mean(), 2) if len(gaps) and gaps.mean() > 0 else None,
           "both_sides_markets_share": _r((both > 1).mean(), 3) if len(both) else None}
    # verdict after Dennim89 (8 independent signals, a bot at ≥4, see style.py); 2–3 signals – "unclear"
    settled, _open, fstats = flatten(t, mk, act)
    style = detect_style(t, settled)
    ops = sum((flow.get(k) or {}).get("rows", 0) for k in ("CONVERSION", "MERGE", "SPLIT"))
    style = apply_ops_override(style, ops, style["signals"].get("active_days") or active_days)
    bot["style"] = style
    bot["both_sides_markets_share"] = style["signals"].get("both_sides_ratio")
    bot["verdict"] = "bot" if style["is_bot"] else ("unclear" if len(style["axes"]) >= 2 else "likely human")
    carry = detect_carry(settled, t, style["signals"].get("both_sides_ratio") or 0)
    pair = {"pairing": pairing(t), "complete_sets": complete_sets(t, led.loc[led["resolved"], ["condition_id", "realized"]])}
    tte = timing_to_end(t, mk)

    # ---------- 6a. profit by outcome and by week: do not pass off a market move as skill ----------
    # (polybot took a "bias to DOWN" for a strategy, but BTC had dropped 5% over the same days)
    by_outcome = []
    if len(res):
        for o, g in res.groupby(res["outcome"].fillna("?")):
            by_outcome.append({"outcome": str(o), "positions": int(len(g)), "cost": _r(g["cost"].sum(), 0), "pnl": _r(g["pnl"].sum(), 0)})
        by_outcome.sort(key=lambda x: -(x["cost"] or 0))
    wk = pd.to_datetime(res["first_ts"], unit="s", utc=True).dt.strftime("%G-W%V") if len(res) else pd.Series(dtype=str)
    by_week = [{"week": k, "pnl": _r(v, 0)} for k, v in res.groupby(wk)["pnl"].sum().items()] if len(res) else []
    direction_warning = None
    top2 = by_outcome[:2]
    span_d = (res["first_ts"].max() - res["first_ts"].min()) / 86400 if len(res) else 0
    if len(top2) == 2 and top2[0]["pnl"] is not None and top2[1]["pnl"] is not None and (top2[0]["pnl"] > 0) != (top2[1]["pnl"] > 0) \
            and span_d < 30 and min(abs(top2[0]["pnl"]), abs(top2[1]["pnl"])) > 0.2 * max(abs(top2[0]["pnl"]), abs(top2[1]["pnl"])):
        win = top2[0] if top2[0]["pnl"] > 0 else top2[1]
        direction_warning = (f"all the profit is on the outcome '{win['outcome']}' over {span_d:.0f} days: this may be the market's move over the period, "
                             f"not skill – a period where the market went both ways is needed")

    # ---------- 7. PnL curve ----------
    curve = None
    if len(pnl):
        pe = pnl.sort_values("timestamp")
        col = "economic_pnl" if "economic_pnl" in pe else "realized_pnl"
        s = pe[col].astype(float)
        dd = (s - s.cummax())
        curve = {"points": [[int(a), _r(b, 0)] for a, b in zip(pe["timestamp"], s)], "max_drawdown": _r(dd.min(), 0),
                 "last": _r(s.iloc[-1], 0), "rewards_rebates_last": _r((pe[["reward_income", "maker_rebate", "taker_rebate"]].fillna(0).sum(axis=1)).iloc[-1], 0)}

    # ---------- 8. best and worst ----------
    p2 = res.sort_values("pnl")
    top = lambda df: [{"title": r.question, "outcome": r.outcome, "avg_price": _r(r.avg_price, 3), "cost": _r(r.cost, 0), "pnl": _r(r.pnl, 0), "category": r.category} for r in df.itertuples()]
    markets_best_worst = {"best": top(p2.tail(5)[::-1]), "worst": top(p2.head(5))}

    # ---------- 9. strategy type ----------
    arche = []
    ax = style["axes"]
    arche.append((style["label"], f"{len(ax)} of 8 bot signals fired" + (": " + "; ".join(a["text"] for a in ax) if ax else "")
                  + (f"; hard marker: {style['override']}" if style.get("override") else "")))
    if carry["is_carry"]:
        arche.append(("carry: collects the nearly decided",
                      f"{carry['carry_frac']:.0%} of money at 85¢ and above, such positions win {carry['carry_win_rate']:.0%} of the time, "
                      f"score {carry['score']:.0f}/100; " + ("holds to resolution" if carry["type"] == "carry-hold" else "resells on the approach to $1")))
    elif carry["carry_frac"] >= .5:
        why = ("buys both sides – this is market making, not a bet on the favorite" if (style["signals"].get("both_sides_ratio") or 0) >= .4
               else f"such positions win only {carry['carry_win_rate']:.0%} of the time – collecting the nearly decided needs 82%+")
        arche.append(("looks like collecting the nearly decided, but is not", f"{carry['carry_frac']:.0%} of money at 85¢ and above, but {why}"))
    lo_share = sum(b["buy_usd_share"] or 0 for b in by_bucket if b["bucket"] in ("≤5¢", "6–20¢"))
    if lo_share > .4:
        arche.append(("cheap-outcome hunter", f"{int(lo_share * 100)}% of money on buys below 20¢"))
    pg, cs_ = pair["pairing"], pair["complete_sets"]
    if pg and pg["both_legs_share"] >= .3:
        d_ = pg["opposite_leg_delay_sec"]
        ev = f"in {pg['both_legs_share']:.0%} of markets; the second leg comes ~{d_['p50']:.0f} s later (median), within a minute – {d_['share_within_60s']:.0%}"
        if cs_.get("markets"):
            ev += (f"; the sum of the two sides' prices averages {1 - (cs_['avg_edge_per_share'] or 0):.3f} – "
                   f"{'cheaper' if (cs_['avg_edge_per_share'] or 0) > 0 else 'more expensive'} than the dollar the pair is guaranteed to receive")
        arche.append(("buys both sides of one market", ev))
    if tte.get("sniper"):
        arche.append(("last-minutes sniper", f"median – {tte['median_seconds_to_end']:.0f} s before the market end, average buy price {tte['avg_buy_price']:.2f}"))
    if (passport["share_from_rewards_and_rebates"] or 0) > .3:
        arche.append(("lives on rewards and rebates", f"{int(passport['share_from_rewards_and_rebates'] * 100)}% of profit is rewards and rebates"))
    top_cat = cats[0] if cats else None
    if top_cat and top_cat["share_volume"] and top_cat["share_volume"] > .6:
        arche.append((f"specialist: {top_cat['category']}", f"{int(top_cat['share_volume'] * 100)}% of volume in one category"))
    short_markets = sum((tte.get("short_series") or {}).values()) > .5     # 5–15-minute markets: "the last hour" is any trade
    if not short_markets and timing["hours_before_market_close"] and (timing["hours_before_market_close"]["share_last_hour"] or 0) > .4:
        arche.append(("enters right before the close", f"{int(timing['hours_before_market_close']['share_last_hour'] * 100)}% of trades in the last hour before the close"))
    if timing["minutes_from_event_start"] and (timing["minutes_from_event_start"]["before_share"] or 1) < .3:
        arche.append(("trades as the event unfolds", f"{int((1 - timing['minutes_from_event_start']['before_share']) * 100)}% of trades after the event start"))
    if buys_usd and fl("CONVERSION") / buys_usd > .2:
        arche.append(("converts NO in multi-outcome events",
                      f"{flow['CONVERSION']['rows']:,} conversions: NO on several outcomes of an event is exchanged for dollars and YES on the others; "
                      f"${fl('CONVERSION'):,.0f} passed through them – {fl('CONVERSION') / buys_usd:.0%} of total buys"))
    if buys_usd and fl("MERGE") / buys_usd > .05:
        arche.append(("merges YES+NO pairs into a dollar",
                      f"{flow['MERGE']['rows']:,} merges for ${fl('MERGE'):,.0f} – {fl('MERGE') / buys_usd:.0%} of total buys"))

    edge_rows = [b for b in by_bucket if b.get("verdict") == "beats the price"]
    best_edge = max(edge_rows, key=lambda b: b["roi"] if b["roi"] is not None else -9, default=None)
    limits = ["cancelled and unfilled orders are not visible", "the external signals that decisions are based on are not visible",
              "the link to other wallets of the same owner is not visible",
              "the total in the passport is official (Data API, with rewards and rebates); buckets and categories are our own accounting from trades with fees, "
              "without splits, merges and conversions",
              "the chance test treats positions as independent; if he bets on several outcomes of one event they are linked, "
              "and the real uncertainty is larger"]

    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passport": passport, "reliability": reliability,
           "money_flow": flow, "categories": cats, "entry": entry,
           "timing": timing, "timing_to_end": tte, "sizing": sizing, "bot": bot, "carry": carry, "pairs": pair, "flatten_stats": fstats,
           "by_outcome": by_outcome, "by_week": by_week, "direction_warning": direction_warning, "curve": curve, "markets": markets_best_worst,
           "archetypes": [{"type": a, "evidence": e} for a, e in arche], "best_edge_bucket": best_edge,
           "resolved_positions": int(len(res)), "profitable_positions": _r((res["pnl"] > 0).mean(), 3) if len(res) else None,
           "pnl_resolved_by_trades": _r(res["pnl"].sum(), 0) if len(res) else None,
           "roi_resolved": _r(res["pnl"].sum() / max(res["cost"].sum(), 1e-9), 3) if len(res) else None,
           "limits": limits}
    # recipe: the same numbers assembled into rules by which the strategy can be repeated
    out["recipe"] = recipe_build(json.loads(json.dumps(out, default=str)), t, mk)
    (d / "recipe.md").write_text(recipe_md(passport.get("name") or passport.get("wallet"), out["recipe"]))
    (d / "analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    if (d / "signal.json").exists():                 # a signal already checked – back into the recipe
        from .signals import apply_to_recipe
        apply_to_recipe(d, json.loads((d / "signal.json").read_text()))
        out = json.loads((d / "analysis.json").read_text())
    return out
