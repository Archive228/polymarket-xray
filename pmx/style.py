"""Wallet style (bot or human, and which type) and the carry strategy.

Translated from Go 1-to-1 from Dennim89/polymarket-wallet-analyzer @ 4c169fb (MIT, Copyright (c) 2026 Denys):
analytics/flatten.go (FlattenWithPairs), analytics/style.go (computeStyleSignals, computeBotScore, whaleLikelihood,
botArchetype, classifyArchetype, DetectStyle), analytics/carry.go (DetectCarry, carryScore).
Full license text – THIRD_PARTY_LICENSES/Dennim89_polymarket-wallet-analyzer.txt.

Thresholds and rule order are the author's own; the author says they were set by hand, not fitted on labeled data.
The logic is ported line by line; Go loops are replaced by vectorized pandas operations with the same result.
Deviations from the original are marked "pmx:"."""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEK = 7 * 24 * 3600

ARCH_LABEL = {"algo_market_maker": "market-making bot", "algo_arbitrage": "arbitrage bot", "algo_hft": "HFT / scalper bot",
              "algo_basket_mm": "basket market-making bot", "whale_concentrated": "whale: big bets in few markets",
              "active_diversified": "active trader", "casual": "casual trader", "insufficient_data": "too few trades"}
BOT_ARCH = {"algo_market_maker", "algo_arbitrage", "algo_hft", "algo_basket_mm"}

# pmx: captions for the bot-score axes, so the report can explain the verdict in words
AXES_TEXT = {"A": "both sides in ≥40% of markets", "B": "median pause between trades < 60 s", "C": "active ≥18 hours a day",
             "D": "trade size almost constant (spread < 0.3)", "E": "≥100 markets and ≥10 trades per market", "F": "≥300 markets",
             "G": "≥500 markets and both sides in ≥40%", "H": "trades on ≥80% of days over a span of 14+ days"}


def _median(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(np.median(x)) if x.size else 0.0


def _coef_var(x) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 2:
        return 0.0
    m = x.mean()
    return 0.0 if m == 0 else float(x.std(ddof=1) / m)


# ---------- flatten.go: FlattenWithPairs ----------
def flatten(t: pd.DataFrame, mk: pd.DataFrame, act: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Positions (market, outcome) built from fills. MERGE is a synthetic sale of X shares at $0.5 on each outcome of the market,
    SPLIT is a synthetic purchase. Groups with no visible purchase, or with sales well above purchases, are dropped:
    their cost basis is unknown. Settled means the market is resolved or ≤1 share is left.
    pmx: the outcome comes from outcome_index (Dennim uses the normalized outcome name), the winner is winner_index from Gamma."""
    buy = t["side"] == "BUY"
    usd = t["size"] * t["price"]
    g = t.assign(bc=usd.where(buy, 0.0), bs=t["size"].where(buy, 0.0), sc=usd.where(~buy, 0.0), ss=t["size"].where(~buy, 0.0)) \
        .groupby(["condition_id", "outcome_index"]).agg(buy_cost=("bc", "sum"), buy_size=("bs", "sum"), sell_cost=("sc", "sum"),
                                                        sell_size=("ss", "sum"), opened=("timestamp", "min"), last=("timestamp", "max"),
                                                        fills=("timestamp", "size")).reset_index()
    if len(act):
        for ty, col in (("SPLIT", "split"), ("MERGE", "merge")):
            a = act[act["type"] == ty]
            g[col] = g["condition_id"].map(a.groupby(a["condition_id"].str.lower())["size"].sum()).fillna(0.0) if len(a) else 0.0
    else:
        g["split"] = g["merge"] = 0.0
    g["buy_size"] += g["split"]
    g["buy_cost"] += g["split"] * 0.5
    g["sell_size"] += g["merge"]
    g["sell_cost"] += g["merge"] * 0.5
    dropped = (g["buy_size"] <= 0) | ((g["sell_size"] > g["buy_size"] * 1.05) & (g["sell_size"] - g["buy_size"] > 1.0))
    stats = {"total_fills": int(len(t)), "dropped_groups": int(dropped.sum()), "dropped_fills": int(g.loc[dropped, "fills"].sum())}
    g = g[~dropped].copy()
    g["net"] = (g["buy_size"] - g["sell_size"]).clip(lower=0)
    g["entry"] = g["buy_cost"] / g["buy_size"]
    g = g.merge(mk[["condition_id", "winner_index", "closed_time"]], on="condition_id", how="left")
    res = g["winner_index"].notna()
    pay_ps = np.where(res & (g["outcome_index"] == g["winner_index"]), 1.0, 0.0)
    g["payout"] = np.where(res, g["sell_cost"] + g["net"] * pay_ps, g["sell_cost"])
    g["pnl"] = g["payout"] - g["buy_cost"]
    g["won"] = g["pnl"] > 0
    closed = pd.to_datetime(g["closed_time"], utc=True, errors="coerce").map(lambda x: x.timestamp() if pd.notna(x) else np.nan)
    g["closed"] = np.where(res, closed.fillna(g["last"]), g["last"])      # pmx: resolution time unknown – use the last trade
    settled_mask = res | (g["net"] <= 1.0)
    settled, open_ = g[settled_mask].copy(), g[~settled_mask].copy()
    stats.update({"settled_groups": int(len(settled)), "open_groups": int(len(open_))})
    return settled, open_, stats


# ---------- style.go ----------
def style_signals(t: pd.DataFrame, settled: pd.DataFrame) -> dict:
    s = t.sort_values("timestamp", kind="stable")
    out = {"total_trades": int(len(s))}
    if not len(s):
        return out
    ts = s["timestamp"].to_numpy(dtype=float)
    out["unique_markets"] = int(s["condition_id"].nunique())
    out["trades_per_market"] = len(s) / out["unique_markets"] if out["unique_markets"] else 0.0
    out["both_sides_ratio"] = float((s.groupby("condition_id")["outcome_index"].nunique() >= 2).mean())
    gaps = np.diff(ts)
    gaps = gaps[(gaps >= 0) & (gaps < WEEK)]                       # weeks of inactivity are not counted
    out["median_inter_trade_secs"] = _median(gaps)
    dt = pd.to_datetime(s["timestamp"], unit="s", utc=True)
    per_day = dt.dt.hour.groupby(dt.dt.strftime("%Y-%m-%d")).nunique()
    out["hours_active_per_day"] = _median(per_day.values)        # median over days, not unique hours over the whole history
    out["active_days"] = int(len(per_day))
    out["span_days"] = int((ts[-1] - ts[0]) / 3600 / 24) + 1
    pos = s["size"] > 0
    out["size_coef_var"] = _coef_var((s.loc[pos, "size"] * s.loc[pos, "price"]).values)
    # peak net position: walk the inventory per (market, outcome) through buys and sells
    sp = s[pos]
    signed = np.where(sp["side"] == "BUY", sp["size"], -sp["size"])
    net = pd.Series(signed, index=sp.index).groupby([sp["condition_id"], sp["outcome_index"]]).cumsum()
    v = np.where(net > 0, net * sp["price"], 0.0)
    out["max_position_size_usd"] = float(v.max()) if len(v) else 0.0
    b = sp[sp["side"] == "BUY"]
    cost_by_market = (b["size"] * b["price"]).groupby(b["condition_id"]).sum()
    out["total_cost_usd"] = float(cost_by_market.sum())
    out["avg_position_size_usd"] = out["total_cost_usd"] / len(cost_by_market) if len(cost_by_market) else 0.0
    h = (settled["closed"] - settled["opened"]).to_numpy(dtype=float) if len(settled) else np.array([])
    out["median_hold_secs"] = _median(h[h > 0])
    return out


def bot_score(s: dict) -> tuple[float, list[str]]:
    """8 independent signals; a wallet counts as a bot at ≥4 (score ≥ 0.65), 2–3 means unclear and stays "human".
    pmx: we also return the list of signals that fired."""
    if s["total_trades"] < 30:
        return 0.0, []
    ax = []
    if s["both_sides_ratio"] >= 0.40:
        ax.append("A")
    if s["median_inter_trade_secs"] < 60:
        ax.append("B")
    if s["hours_active_per_day"] >= 18:
        ax.append("C")
    if 0 < s["size_coef_var"] < 0.30:
        ax.append("D")
    if s["unique_markets"] >= 100 and s["trades_per_market"] >= 10:
        ax.append("E")
    if s["unique_markets"] >= 300:
        ax.append("F")
    if s["unique_markets"] >= 500 and s["both_sides_ratio"] >= 0.40:
        ax.append("G")
    if s["span_days"] >= 14 and s["active_days"] / s["span_days"] >= 0.80:
        ax.append("H")
    return {0: 0.05, 1: 0.20, 2: 0.40, 3: 0.55, 4: 0.80, 5: 0.90}.get(len(ax), 0.97), ax


def whale_likelihood(s: dict) -> float:
    if s["total_trades"] < 30:
        return 0.0
    score = 0.0
    um, avg = s["unique_markets"], s["avg_position_size_usd"]
    score += 1.0 if um <= 5 else (0.6 if um <= 20 else (0.3 if um <= 50 else 0.0))
    score += 0.8 if avg >= 100000 else (0.4 if avg >= 10000 else (0.15 if avg >= 1000 else 0.0))
    if s["both_sides_ratio"] < 0.25:
        score += 0.2                                   # a bet on one side
    if s["median_hold_secs"] > 24 * 3600:
        score += 0.3                                   # holds for longer than a day
    return min(max(score / 2.0, 0.0), 1.0)


def bot_archetype(s: dict) -> str:
    if s["unique_markets"] >= 500 and s["both_sides_ratio"] >= 0.40 and s["median_inter_trade_secs"] < 30:
        return "algo_basket_mm"
    if s["both_sides_ratio"] >= 0.5 and 0 < s["median_hold_secs"] < 600:
        return "algo_arbitrage"
    if s["both_sides_ratio"] >= 0.4:
        return "algo_market_maker"
    return "algo_hft"


def classify_archetype(s: dict, bot: float) -> str:
    if s["total_trades"] < 30:
        return "insufficient_data"
    if s["total_cost_usd"] >= 150_000_000:                                          # hard volume threshold
        return bot_archetype(s)
    if s["both_sides_ratio"] >= 0.40 and s["trades_per_market"] >= 200 and 0 < s["avg_position_size_usd"] < 100000:
        return bot_archetype(s)                                                     # quotes both sides with hundreds of trades
    if s["unique_markets"] >= 20 and s["both_sides_ratio"] >= 0.85:
        return bot_archetype(s)                                                     # both sides almost everywhere
    wl = whale_likelihood(s)
    if wl >= 0.60 and s["total_cost_usd"] >= 5000:
        return "whale_concentrated"
    if bot >= 0.65:
        arch = bot_archetype(s)
        # guard: a "scalper" who holds a position of $50k+ or for longer than an hour is not a scalper
        if not (arch == "algo_hft" and (s["max_position_size_usd"] >= 50000 or s["median_hold_secs"] > 3600)):
            return arch
    if wl >= 0.40 and s["unique_markets"] <= 30 and s["total_cost_usd"] >= 5000:
        return "whale_concentrated"
    return "casual" if s["total_trades"] < 100 else "active_diversified"


def detect_style(t: pd.DataFrame, settled: pd.DataFrame) -> dict:
    sig = style_signals(t, settled)
    if sig["total_trades"] == 0:
        return {"archetype": "insufficient_data", "label": ARCH_LABEL["insufficient_data"], "bot_score": 0.0, "is_bot": False,
                "confidence": "low", "axes": [], "signals": sig}
    bot, axes = bot_score(sig)
    arch = classify_archetype(sig, bot)
    is_bot = arch in BOT_ARCH
    shown = bot                          # the score must not contradict the verdict
    if arch == "whale_concentrated":
        shown = bot * 0.3
    elif not is_bot and bot >= 0.65:
        shown = 0.50
    elif is_bot and bot < 0.65:
        shown = 0.90
    conf = "high" if sig["total_trades"] >= 500 else ("medium" if sig["total_trades"] >= 100 else "low")
    return {"archetype": arch, "label": ARCH_LABEL[arch], "bot_score": round(shown, 2), "is_bot": is_bot, "confidence": conf,
            "axes": [{"axis": a, "text": AXES_TEXT[a]} for a in axes], "signals": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in sig.items()}}


def apply_ops_override(st: dict, ops: int, active_days: int) -> dict:
    """pmx: a hard automation marker modeled on ApplyHardBotOverride – thousands of conversions, merges and splits.
    Dennim's guard "holds longer than an hour – not a scalper" strips the "bot" verdict from a wallet that holds positions for days,
    and a converter's positions do live for days. Nobody does hundreds of such operations a day by hand."""
    per_day = ops / max(active_days, 1)
    if st["is_bot"] or ops < 1000 or per_day < 100:
        return st
    n = lambda x: f"{x:,.0f}"
    return {**st, "archetype": "algo_arbitrage", "label": "arbitrage bot on conversions and merges", "is_bot": True,
            "bot_score": max(st["bot_score"], 0.90), "override": f"{n(ops)} conversions, merges and splits – {n(per_day)} a day"}


# ---------- carry.go ----------
CARRY_FLOOR = 0.85


def detect_carry(settled: pd.DataFrame, t: pd.DataFrame, both_sides_ratio: float) -> dict:
    """Carry: systematically buys the nearly decided (entry ≥85¢) and collects the thin favorite premium.
    carry-hold – holds to resolution, carry-flip – resells on the approach to $1."""
    usd = t["size"] * t["price"]
    buy_n, sell_n = usd[t["side"] == "BUY"].sum(), usd[t["side"] == "SELL"].sum()
    sell_ratio = sell_n / buy_n if buy_n > 0 else 0.0
    total = settled["buy_cost"].sum() if len(settled) else 0.0
    zone = settled[settled["entry"] >= CARRY_FLOOR] if len(settled) else settled
    carry_n = int(len(zone))
    mean_entry = float((settled["entry"] * settled["buy_cost"]).sum() / total) if total > 0 else 0.0
    carry_frac = float(zone["buy_cost"].sum() / total) if total > 0 else 0.0
    carry_wr = float(zone["won"].mean()) if carry_n else 0.0
    clamp = lambda x: min(max(x, 0.0), 1.0)
    score = 50 * clamp(carry_frac)
    if carry_n >= 20:
        score += 35 * clamp((carry_wr - 0.60) / 0.35)
    score += 15 * clamp((mean_entry - 0.75) / 0.20)
    if both_sides_ratio >= 0.40:
        score *= 0.30                     # a market maker quotes both sides – this is not a directional carry
    is_carry = len(settled) >= 30 and carry_frac >= 0.50 and carry_n >= 20 and carry_wr >= 0.82 and both_sides_ratio < 0.40
    return {"is_carry": bool(is_carry), "type": ("carry-flip" if sell_ratio >= 0.35 else "carry-hold") if is_carry else "",
            "score": round(score, 1), "carry_frac": round(carry_frac, 3), "carry_win_rate": round(carry_wr, 3), "carry_n": carry_n,
            "mean_entry": round(mean_entry, 3), "sell_ratio": round(float(sell_ratio), 3)}
