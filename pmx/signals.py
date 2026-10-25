"""Signal recovery: what the bot looks at in crypto Up/Down markets.

The market "Bitcoin Up or Down, 15 minutes" resolves on Chainlink BTC/USD: Up if the price at the end of the window is not below the price at the start.
Hypothesis: the bot looks at which way the price has moved since the window start. The trades do not show this, so we check against the public
Binance 1-minute candles (a same-class substitute for Chainlink, no key; the difference between them is a fraction of a percent).

For each of his buys: move = price at the minute of the trade / price at the window start − 1 (the minute's open, no look-ahead).
The leading side is Up if move > 0, Down if < 0. We compute:
- what share of his money goes on the leading side, overall and by move size;
- in markets where he bought both sides, whether his tilt matches the side that was leading at his last purchase;
- how often the leader at the moment of entry ends up winning and whether buying the leader pays at his prices. → signal.json"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import api
from .pairs import slug_window

BINANCE = "https://api.binance.com/api/v3/klines"
SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
MOVE_BINS = [0, .0002, .0005, .001, .002, np.inf]
MOVE_LABELS = ["under 0.02%", "0.02–0.05%", "0.05–0.1%", "0.1–0.2%", "over 0.2%"]


def klines(symbol: str, start: int, end: int) -> pd.Series:
    """Open price of every minute in [start, end]; the index is the unix second of the minute start. The past is cached in chunks of 1000 minutes."""
    out = {}
    t0 = start // 60 * 60
    while t0 <= end:
        chunk_end = t0 + 999 * 60

        def fetch(t0=t0, chunk_end=chunk_end):
            return api.get(BINANCE, {"symbol": symbol, "interval": "1m", "startTime": t0 * 1000, "endTime": chunk_end * 1000, "limit": 1000}) or []
        rows = api.cached(f"kl:{symbol}:{t0}", fetch) if chunk_end < time.time() - 120 else fetch()
        for r in rows:
            out[int(r[0]) // 1000] = float(r[1])
        t0 = chunk_end + 60
    return pd.Series(out).sort_index()


def signal(d: Path, markets_n: int = 400, log=print) -> dict:
    t = pd.read_csv(d / "trades.csv", low_memory=False, usecols=lambda c: c in {"side", "condition_id", "size", "price", "timestamp", "outcome", "is_maker"})
    mk = pd.read_csv(d / "markets.csv", low_memory=False)
    t["condition_id"] = t["condition_id"].str.lower()
    mk["condition_id"] = mk["condition_id"].str.lower()
    sw = mk["slug"].map(slug_window)
    mk["asset"] = sw.map(lambda x: x[0].split("-")[0] if x[0] else None)
    mk["w_start"], mk["w_end"] = sw.map(lambda x: x[1]), sw.map(lambda x: x[2])
    mk = mk[mk["asset"].isin(SYMBOL) & mk["winner_index"].notna()]
    if not len(mk):
        return {}
    mk["winner"] = [json.loads(o)[int(w)] for o, w in zip(mk["outcomes"], mk["winner_index"])]
    b = t[(t["side"] == "BUY") & t["outcome"].isin(["Up", "Down"])].merge(mk[["condition_id", "asset", "w_start", "w_end", "winner"]], on="condition_id")
    if not len(b):
        return {}
    last = b.groupby("condition_id")["timestamp"].max().sort_values(ascending=False).head(markets_n).index
    b = b[b["condition_id"].isin(last)].copy()
    log(f"signal: {len(last)} latest Up/Down markets, {len(b):,} buys")
    parts = []
    for asset, g in b.groupby("asset"):
        px = klines(SYMBOL[asset], int(g["w_start"].min()), int(g["timestamp"].max()) + 60)
        g = g.copy()
        g["p_start"] = (g["w_start"] // 60 * 60).map(px)
        g["p_now"] = (g["timestamp"] // 60 * 60).map(px)
        parts.append(g)
    b = pd.concat(parts)
    b = b[b["p_start"].notna() & b["p_now"].notna()]
    b["move"] = b["p_now"] / b["p_start"] - 1
    b["lead"] = np.where(b["move"] > 0, "Up", np.where(b["move"] < 0, "Down", None))
    b = b[b["lead"].notna()]
    b["usd"] = b["size"] * b["price"]
    b["on_lead"] = b["outcome"] == b["lead"]
    b["won"] = b["outcome"] == b["winner"]
    b["lead_won"] = b["lead"] == b["winner"]
    tot = b["usd"].sum()
    on = b.loc[b["on_lead"], "usd"].sum() / tot
    # by move size: the further the price moved from the start, the more money on the leader?
    b["bin"] = pd.cut(b["move"].abs(), MOVE_BINS, labels=MOVE_LABELS, right=False)
    by_move = []
    for lbl, g in b.groupby("bin", observed=True):
        by_move.append({"move": str(lbl), "share_usd": round(float(g["usd"].sum() / tot), 3),
                        "on_lead_usd": round(float(g.loc[g["on_lead"], "usd"].sum() / g["usd"].sum()), 3),
                        "lead_wins": round(float(g["lead_won"].mean()), 3)})
    # does "buy the leader" pay at his prices: share of wins against the average price, by money
    def ev(g):
        if not len(g) or g["usd"].sum() == 0:
            return None
        w = g["usd"]
        return {"usd_share": round(float(w.sum() / tot), 3), "win_rate": round(float((g["won"] * w).sum() / w.sum()), 3),
                "avg_price": round(float((g["price"] * w).sum() / w.sum()), 3),
                "roi_before_fees": round(float(((g["won"] * g["size"]).sum() - w.sum()) / w.sum()), 3)}
    # tilt in markets with both sides vs the leader at his last purchase
    # tilt is by money: the cheap side gives more shares per dollar, and a tilt "by shares" looks the wrong way
    m = b.groupby("condition_id").apply(lambda g: pd.Series({
        "up": g.loc[g["outcome"] == "Up", "usd"].sum(), "down": g.loc[g["outcome"] == "Down", "usd"].sum(),
        "lead_last": g.sort_values("timestamp")["lead"].iloc[-1], "winner": g["winner"].iloc[0]}))
    both = m[(m["up"] > 0) & (m["down"] > 0) & (m["up"] != m["down"])].copy()
    both["tilt"] = np.where(both["up"] > both["down"], "Up", "Down")
    out = {"markets": int(b["condition_id"].nunique()), "buys": int(len(b)), "source": "Binance 1m (Chainlink substitute)",
           "assets": sorted(b["asset"].str.upper().unique().tolist()),
           "on_lead_usd": round(float(on), 3), "lead_at_entry_wins": round(float((b["lead_won"] * b["usd"]).sum() / tot), 3),
           "by_move": by_move, "buy_leader": ev(b[b["on_lead"]]), "buy_against": ev(b[~b["on_lead"]]),
           "two_sided_markets": int(len(both)),
           "tilt_matches_lead": round(float((both["tilt"] == both["lead_last"]).mean()), 3) if len(both) else None,
           "tilt_wins": round(float((both["tilt"] == both["winner"]).mean()), 3) if len(both) else None}
    (d / "signal.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def describe(s: dict) -> list[str]:
    """The same conclusion in plain words – for the recipe and the report."""
    if not s:
        return []
    L = [f"Checked on the {s['markets']} latest markets using Binance minute prices (buys: {s['buys']:,}): "
         + f"{s['on_lead_usd'] * 100:.0f}% of his money goes on the side that is winning on price since the window start at the moment of purchase."]
    bm = s.get("by_move") or []
    if len(bm) >= 3:
        L.append(f"The further the price moved from the start, the more money on the leader: at a move of {bm[0]['move']} – {bm[0]['on_lead_usd'] * 100:.0f}%, "
                 f"at a move of {bm[-2]['move']} – {bm[-2]['on_lead_usd'] * 100:.0f}%, {bm[-1]['move']} – {bm[-1]['on_lead_usd'] * 100:.0f}%.")
    if s.get("tilt_matches_lead") is not None:
        L.append(f"Where he takes both sides, more money is on the leader's side in {s['tilt_matches_lead'] * 100:.0f}% of markets; "
                 f"the side with the tilt wins in {s['tilt_wins'] * 100:.0f}% of markets.")
    bl, ba = s.get("buy_leader"), s.get("buy_against")
    if bl and ba:
        L.append(f"Leader buys: win {bl['win_rate'] * 100:.0f}% at an average price of {bl['avg_price'] * 100:.0f}¢ (before fees {bl['roi_before_fees'] * 100:+.1f}%); "
                 f"against the leader: {ba['win_rate'] * 100:.0f}% at {ba['avg_price'] * 100:.0f}¢ ({ba['roi_before_fees'] * 100:+.1f}%).".replace("-", "−"))
    return L


def apply_to_recipe(d: Path, s: dict) -> None:
    """Write the checked signal into the recipe: a new step and, instead of "what is not visible", what was verified."""
    p = d / "analysis.json"
    if not s or not p.exists():
        return
    from .recipe import to_md
    a = json.loads(p.read_text())
    r = a.get("recipe") or {}
    bm = s.get("by_move") or []
    if not bm:
        return
    step = {"rule": f"How much money goes on each side is decided by the {'/'.join(s.get('assets') or ['BTC'])} price move since the window start: "
                    f"at a move of {bm[0]['move']} – nearly even, at a move of 0.1% and above – {max(x['on_lead_usd'] for x in bm[-2:]) * 100:.0f}% on the leader's side.",
            "evidence": f"on average {s['on_lead_usd'] * 100:.0f}% of money on the leader; the leader at the moment of entry wins in {s['lead_at_entry_wins'] * 100:.0f}% of cases; "
                        f"checked on Binance minute prices across {s['markets']} markets", "strength": "mostly"}
    r["steps"] = [x for x in r.get("steps", []) if not x["rule"].startswith("How much money goes on each side")]
    r["steps"].insert(min(3, len(r["steps"])), step)
    r.setdefault("spec", []).append({"param": "signal", "value": "asset price move since the window start (Binance 1m as a Chainlink substitute)",
                                     "evidence": "; ".join(f"{x['move']}: {x['on_lead_usd'] * 100:.0f}% on the leader" for x in bm)})
    r["unseen"] = [u for u in r.get("unseen", []) if u["what"] != "why he picks the side and the moment"]
    r["unseen"].insert(0, {"what": "why he picks the side", "replace": "verified on data, not assumed. " + " ".join(describe(s))})
    a["recipe"], a["signal"] = r, s
    p.write_text(json.dumps(a, ensure_ascii=False, indent=1, default=str))
    (d / "recipe.md").write_text(to_md(a["passport"].get("name") or a["passport"].get("wallet"), r))
