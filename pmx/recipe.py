"""Strategy recipe: a wallet analysis turned into rules by which the strategy can be repeated.

Every rule comes from the export's numbers, with evidence and a strength: "always" (≥90% of trades or money follow the rule),
"almost always" (≥75%), "mostly" (≥50%), "sometimes". Two forms: step by step in plain words, and a technical
spec (modeled on docs/EXAMPLE_STRATEGY_SPEC.md from ent0n29/polybot: markets, window, price, size, hedge, exit).
Separately – where the money comes from and what the trades do not show, with an open source that could replace it."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def strength(s: float) -> str:
    return "always" if s >= .9 else ("almost always" if s >= .75 else ("mostly" if s >= .5 else "sometimes"))


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _money(x) -> str:
    if x is None:
        return "–"
    s = "−" if x < 0 else ""
    x = abs(x)
    return f"{s}${x / 1e6:.2f}M" if x >= 1e6 else (f"{s}${x / 1e3:.1f}k" if x >= 1e3 else f"{s}${x:,.0f}")


def _dur(sec: float) -> str:
    sec = float(sec)
    if sec < 120:
        return f"{sec:.0f} s"
    if sec < 2 * 3600:
        return f"{sec / 60:.0f} min"
    if sec < 2 * 86400:
        return f"{sec / 3600:.0f} h"
    return f"{sec / 86400:.0f} d"


def _wq(x, w, qs) -> list[float]:
    x, w = np.asarray(x, float), np.asarray(w, float)
    o = np.argsort(x)
    c = np.cumsum(w[o]) / w.sum()
    return [float(x[o][min(np.searchsorted(c, q), len(x) - 1)]) for q in qs]


# Gamma series "seoul-daily-weather" → family "*-daily-weather": the same market type across different cities/assets
FAMILY_LABEL = [(r"up-or-down-(\d+)m$", lambda m: f"{m[1]}-minute crypto Up/Down"), (r"up-or-down-hourly$", lambda m: "hourly crypto Up/Down"),
                (r"lowest-temperature", lambda m: "daily low temperature in a city"), (r"(daily-weather|highest-temperature)", lambda m: "daily high temperature in a city")]


FAMILY_KEYS = [(r"up-or-down-(\d+m|hourly)$", "*-up-or-down-{0}"), (r"lowest-temperature", "*-daily-lowest-temperature"),
               (r"(daily-weather|highest-temperature)", "*-daily-weather")]


def _family(slug: str) -> str:
    """Family by series pattern (a city or asset name can be several words), otherwise – without the first word."""
    s = str(slug)
    for rx, key in FAMILY_KEYS:
        m = re.search(rx, s)
        if m:
            return key.format(*m.groups())
    return s                    # unknown pattern – the series as is (for sports leagues the first word is part of the name)


def _n(x: float) -> str:
    return f"{x:,.0f}"


def _deals(n: float) -> str:
    k = int(round(n))
    w = "trade" if k == 1 else "trades"
    return f"{_n(k)} {w}"


def _series_word(n: int) -> str:
    return "series"


SERIES_LABEL = {"soccer-fifwc": "FIFA World Cup", "fifwc": "FIFA World Cup", "nfl": "NFL", "cfb": "college football",
                "cbb": "college basketball", "nba": "NBA", "wnba": "WNBA", "mlb": "MLB", "nhl": "NHL", "ucl": "Champions League", "uel": "Europa League",
                "epl": "English Premier League", "premier-league": "English Premier League", "la-liga": "La Liga", "serie-a": "Serie A",
                "bundesliga": "Bundesliga", "mls": "MLS", "ufc": "UFC", "atp": "ATP tennis", "wta": "WTA tennis", "counter-strike": "Counter-Strike",
                "league-of-legends": "League of Legends", "dota": "Dota 2", "valorant": "Valorant", "f1": "Formula 1", "fomc": "Fed decisions",
                "elon-tweets": "Elon Musk tweet counts", "trump-weekly-mentions": "Trump weekly mentions"}


def _plural(n: float, one: str, few: str, many: str) -> str:
    k = int(round(n))
    w = one if k == 1 else many
    return f"{_n(k)} {w}"


def _family_label(fam: str) -> str:
    for rx, f in FAMILY_LABEL:
        m = re.search(rx, fam)
        if m:
            return f(m)
    base = re.sub(r"-\d{4}(-\d{2,4})?$", "", fam.replace("*-", ""))
    if base in SERIES_LABEL:
        return SERIES_LABEL[base]
    return re.sub(r"-\d{4}(-\d{2,4})?$", "", fam.replace("*-", "")).replace("-", " ")


UNSEEN = {
    "crypto up/down": ("the BTC/ETH price on the exchange at the moment of the trade", "Binance or Coinbase 1-minute candles (public, no key)"),
    "weather": ("the weather forecast and actual observations", "Open-Meteo (forecast), aviationweather.gov METAR (airport observations, which the markets resolve on)"),
    "sports": ("bookmaker odds and the course of the match", "open lines and live scores (The Odds API, ESPN)"),
    "elections": ("polls and news", "poll aggregators and news feeds"),
    "economy": ("rate and macro-data expectations", "FedWatch, the macro-data calendar"),
}


def build(a: dict, t: pd.DataFrame, mk: pd.DataFrame) -> dict:
    """a – the finished analysis (without recipe), t – trades with market columns, mk – markets."""
    steps, spec, econ = [], [], []
    rl = a["reliability"]
    buys = t[t["side"] == "BUY"].copy()
    if not len(buys):                       # only sells, merges or conversions: nothing to read a recipe from
        return {"reliability": rl["level"], "steps": [], "spec": [], "economics": [], "unseen": []}
    buys["usd"] = buys["size"] * buys["price"]
    tot = buys["usd"].sum()

    # 1. markets: series families
    ser = buys["condition_id"].map(mk.set_index("condition_id")["series_slug"]) if "series_slug" in mk else pd.Series(np.nan, index=buys.index)
    fam = ser.dropna().map(_family)
    if len(fam) and fam.index.size / len(buys) > .5:
        share = buys.loc[fam.index, "usd"].groupby(fam).sum().sort_values(ascending=False) / tot
        n_series = ser.groupby(fam).nunique()
        top = share[share >= .01].head(3)
        what = "; ".join(f"{_family_label(f)} – {_pct(s)} of money ({n_series[f]} {_series_word(n_series[f])})" for f, s in top.items())
        rule = f"Trades one market type: {_family_label(top.index[0])}." if top.iloc[0] >= .5 else f"Trades several market types: {what}."
        # the strategy may have changed: if his main market type over the last 30 days is different, say so
        recent = buys.loc[fam.index][buys.loc[fam.index, "timestamp"] >= buys["timestamp"].max() - 30 * 86400]
        if len(recent):
            rs = recent["usd"].groupby(fam[recent.index]).sum().sort_values(ascending=False) / recent["usd"].sum()
            if rs.index[0] != top.index[0]:
                now = "; ".join(f"{_family_label(f)} – {_pct(v)}" for f, v in rs[rs >= .05].head(3).items())
                rule = rule.rstrip(".") + f". Last 30 days it trades something else: {now} (share of money)."
                what += f"; over the last 30 days: {now}"
        steps.append({"rule": rule, "evidence": what, "strength": strength(top.iloc[0])})
        spec.append({"param": "markets", "value": what, "evidence": f"by Gamma market series, families: {len(n_series)}"})
    else:
        cats = a["categories"][:3]
        what = "; ".join(f"{c['category']} – {_pct(c['share_volume'])} of volume" for c in cats)
        steps.append({"rule": f"Main market – {cats[0]['category']}." if cats else "Mixed markets.", "evidence": what,
                      "strength": strength(cats[0]["share_volume"]) if cats else "sometimes"})
        spec.append({"param": "markets", "value": what, "evidence": "by question text"})

    # 2. when he enters
    tt = a.get("timing_to_end") or {}
    q = tt.get("quantiles_sec") or {}
    if q:
        steps.append({"rule": f"Enters {_dur(q['p25'])}–{_dur(q['p75'])} before trading ends.",
                      "evidence": f"half of the buys fall in this window; median – {_dur(q['p50'])} before, 80% – from {_dur(q['p10'])} to {_dur(q['p90'])}",
                      "strength": "mostly"})
        spec.append({"param": "entry window", "value": f"{_dur(q['p10'])} – {_dur(q['p90'])} before trading ends (80% of buys)",
                     "evidence": f"quartiles {_dur(q['p25'])} / {_dur(q['p50'])} / {_dur(q['p75'])}"})
    mes = (a.get("timing") or {}).get("minutes_from_event_start")
    if mes and mes.get("before_share") is not None and (mes["before_share"] > .7 or mes["before_share"] < .3) and tt.get("short_series") in (None, {}):
        live = 1 - mes["before_share"]
        steps.append({"rule": "Buys as the event unfolds, not in advance." if live > .5 else "Buys before the event starts.",
                      "evidence": f"{_pct(max(live, 1 - live))} of trades {'after' if live > .5 else 'before'} the start", "strength": strength(max(live, 1 - live))})

    # 2a. pairs
    pg = (a.get("pairs") or {}).get("pairing") or {}
    cs = (a.get("pairs") or {}).get("complete_sets") or {}
    if pg.get("both_legs_share", 0) >= .3:
        d = pg["opposite_leg_delay_sec"]
        steps.append({"rule": f"Buys both sides of a market: the second one ~{d['p50']:.0f} s after the first.",
                      "evidence": f"both sides in {_pct(pg['both_legs_share'])} of markets; within a minute – {_pct(d['share_within_60s'])}; "
                                  f"share imbalance between the sides – {_pct(pg['imbalance_median'])} (median)", "strength": strength(pg["both_legs_share"])})
        if cs.get("avg_edge_per_share") is not None:
            spec.append({"param": "pair", "value": f"both sides, second leg after {d['p50']:.0f} s (90% – within {d['p90']:.0f} s)",
                         "evidence": f"average pair cost ${1 - cs['avg_edge_per_share']:.3f}; share imbalance {_pct(pg['imbalance_median'])}"})

    # 3. what he buys
    p10, p50, p90 = _wq(buys["price"], buys["usd"], (.1, .5, .9))
    fav = buys.loc[buys["price"] > .5, "usd"].sum() / tot
    side = ("the favorite (above 50¢)" if fav >= .5 else "the underdog (below 50¢)")
    two_sided = pg.get("both_legs_share", 0) >= .3
    rule = (f"Pays {p10 * 100:.0f}–{p90 * 100:.0f}¢ per share; puts {_pct(fav)} of money on the more expensive side." if two_sided
            else f"Buys {side} at {p10 * 100:.0f}–{p90 * 100:.0f}¢.")
    steps.append({"rule": rule,
                  "evidence": f"80% of money at a price from {p10 * 100:.0f}¢ to {p90 * 100:.0f}¢, median {p50 * 100:.0f}¢; on the favorite – {_pct(fav)} of money",
                  "strength": strength(max(fav, 1 - fav))})
    spec.append({"param": "entry price", "value": f"{p10:.2f} – {p90:.2f} (80% of money), median {p50:.2f}", "evidence": f"favorite > 0.50: {_pct(fav)} of money"})
    cr = a.get("carry") or {}
    if cr.get("is_carry"):
        spec.append({"param": "price strategy", "value": "collecting the nearly decided (carry)",
                     "evidence": f"{_pct(cr['carry_frac'])} of money at 0.85 and above, {_pct(cr['carry_win_rate'])} of them win"})

    # 4. how he places orders
    mk_usd = (a["entry"].get("maker_share_usd") or 0)
    mk_n = (a["entry"].get("maker_share_trades") or 0)
    if abs(mk_usd - mk_n) > .3:          # small fills and the bulk of the money go through different routes
        rule = (f"Small fills come from his own resting orders ({_pct(mk_n)} of trades), while the bulk of the money "
                + (f"is taken from the market ({_pct(1 - mk_usd)} of money)." if mk_usd < .5 else f"also goes through resting orders ({_pct(mk_usd)} of money)."))
    else:
        rule = ("Places a limit order in the book and waits for it to be taken (maker)." if mk_usd >= .5
                else "Takes ready orders from the book (taker).")
    steps.append({"rule": rule,
                  "evidence": f"as maker – {_pct(mk_usd)} of money, {_pct(a['entry'].get('maker_share_trades') or 0)} of trades", "strength": strength(max(mk_usd, 1 - mk_usd))})
    spec.append({"param": "order type", "value": "limit, maker" if mk_usd >= .5 else "market, taker",
                 "evidence": f"maker {_pct(mk_usd)} of money; a maker pays no fee and earns a rebate"})

    # 5. size
    sh = buys["size"]
    s10, s50, s90 = (float(sh.quantile(x)) for x in (.1, .5, .9))
    sz = a.get("sizing") or {}
    lot = f"{_plural(s50, 'share', 'shares', 'shares')} (80% – from {s10:.0f} to {s90:.0f})"
    rho_t = None
    if q and len(buys) > 50 and "end_date" in buys:
        e = pd.to_datetime(buys["end_date"], utc=True, errors="coerce")
        tte = (e - pd.Timestamp(0, tz="UTC")).dt.total_seconds() - buys["timestamp"]
        ok = tte.notna() & (tte > 0)
        if ok.sum() > 50:
            rho_t = spearmanr(tte[ok], buys.loc[ok, "size"]).correlation
    extra = []
    if sz.get("fixed_lot_share") and sz["fixed_lot_share"] > .3:
        extra.append(f"{_pct(sz['fixed_lot_share'])} of trades – exactly {sz['fixed_lot_size']} shares")
    if sz.get("price_vs_size_spearman") is not None and abs(sz["price_vs_size_spearman"]) >= .2:
        extra.append(f"the {'higher' if sz['price_vs_size_spearman'] > 0 else 'lower'} the outcome price, the bigger the bet (ρ = {sz['price_vs_size_spearman']:+.2f})")
    if rho_t is not None and abs(rho_t) >= .2:
        extra.append(f"the closer the market end, the {'smaller' if rho_t > 0 else 'bigger'} the bet (ρ = {rho_t:+.2f})")
    fpp = a["entry"].get("fills_per_position_median")
    u50 = float((buys["usd"]).median())
    steps.append({"rule": f"Trade size – usually {_plural(s50, 'share', 'shares', 'shares')}, about ${_n(u50)}"
                          + (f"; builds a position over {_deals(fpp)}" if fpp and fpp > 2 else "") + ".",
                  "evidence": lot + ("; " + "; ".join(extra) if extra else ""), "strength": "mostly"})
    spec.append({"param": "size", "value": lot, "evidence": "; ".join(extra) or f"median ${sz.get('trade_usd', {}).get('median', 0):.0f} per trade"})

    # 7. exit
    sold = t.loc[t["side"] == "SELL", "size"].sum() / max(buys["size"].sum(), 1e-9)
    if sold < .1:
        steps.append({"rule": "Does not sell: holds the position to resolution and collects the payout.", "evidence": f"sold {_pct(sold)} of the shares bought",
                      "strength": strength(1 - sold)})
        spec.append({"param": "exit", "value": "holds to resolution", "evidence": f"sold {_pct(sold)} of shares"})
    else:
        sells = t[t["side"] == "SELL"]
        sp50 = _wq(sells["price"], sells["size"] * sells["price"], (.5,))[0] if len(sells) else None
        how = "Sells about half of the position before resolution" if sold <= .7 else "Sells the position before resolution"
        steps.append({"rule": how + (f", usually at {sp50 * 100:.0f}¢" if sp50 else "") + ".",
                      "evidence": f"sold {_pct(min(sold, 1))} of the shares bought, the rest waits for the payout", "strength": strength(max(min(sold, 1), 1 - sold))})
        spec.append({"param": "exit", "value": f"sells {_pct(min(sold, 1))} of shares before resolution" + (f", median sell price {sp50:.2f}" if sp50 else ""),
                     "evidence": "the rest – payout at resolution"})

    # 8. pace
    st = (a["bot"].get("style") or {}).get("signals") or {}
    b = a["bot"]
    hrs = (a.get("timing") or {}).get("hours_utc") or {}
    peak = sorted(hrs.items(), key=lambda kv: -kv[1])[:3]
    steps.append({"rule": f"On a typical day trades ~{st.get('hours_active_per_day', 0):.0f} h, {_deals(b['trades_per_active_day'])} per active day"
                          + (" – this is automation." if a["bot"]["verdict"] == "bot" else "."),
                  "evidence": f"trades on {_plural(st.get('active_days', 0), 'day', 'days', 'days')} out of {st.get('span_days', 0)}; most often at {', '.join(str(h) for h, _ in peak)} h UTC",
                  "strength": strength(st.get("active_days", 0) / max(st.get("span_days", 1), 1))})
    spec.append({"param": "pace", "value": f"{_deals(b['trades_per_active_day'])} per active day, {st.get('hours_active_per_day', 0):.0f} h a day",
                 "evidence": f"median pause between trades {st.get('median_inter_trade_secs', 0):.0f} s; {a['bot']['style']['label']}"})

    # 9. where the money comes from
    p = a["passport"]
    econ.append(f"Total per Polymarket data: {_money(p['pnl_economic'])} on a volume of {_money(p['volume_usdc'])}.")
    ib = p.get("income_breakdown") or {}
    side = sum(ib.get(k) or 0 for k in ("reward_income", "maker_rebate", "taker_rebate"))
    if side and (p.get("share_from_rewards_and_rebates") or 0) >= .01:
        econ.append(f"Liquidity rewards and rebates: {_money(side)} ({_pct(p.get('share_from_rewards_and_rebates') or 0)} of the total).")
    fl = a.get("money_flow") or {}
    if (fl.get("CONVERSION") or {}).get("usd"):
        econ.append(f"NO conversions in multi-outcome events: {_n(fl['CONVERSION']['rows'])} operations, {_money(fl['CONVERSION']['usd'])} passed through them.")
    if cs.get("locked_pnl") is not None and pg.get("both_legs_share", 0) >= .3:
        econ.append(f"Pairs: {_money(cs['locked_pnl'])} on the pairs themselves, a tilt toward one outcome – {_money(cs['directional_pnl'])}.")
    if cr.get("is_carry"):
        econ.append(f"Collecting the nearly decided: {_pct(cr['carry_frac'])} of money at 85¢ and above, such positions win {_pct(cr['carry_win_rate'])} of the time.")
    if pg.get("both_legs_share", 0) >= .3:
        econ.append("Profit cannot be split by entry price: the two legs of one pair fall into different price buckets – "
                    "the winning one gives a plus in one, the losing one a minus in the other, and the buckets mean nothing on their own.")
    elif rl["level"] != "unreliable":
        bb = [x for x in a["entry"]["by_price_bucket"] if x.get("pnl") is not None]
        if bb:
            top_b = max(bb, key=lambda x: x["pnl"])
            econ.append(f"Most earned at price {top_b['bucket']}: {_money(top_b['pnl'])} – "
                        + ("and this is not chance." if top_b.get("verdict") == "beats the price"
                           else f"but that is {top_b['test'].get('wins', 0)} wins against {top_b['test'].get('expected_wins', 0):.0f} expected: "
                                "indistinguishable from luck." if top_b.get("verdict") == "indistinguishable from chance" else top_b.get("verdict", "") + "."))
        good = [x for x in a["entry"]["by_price_bucket"] if x.get("verdict") == "beats the price"]
        econ.append("Guesses the outcome better than the market at price " + ", ".join(f"{x['bucket']} ({_money(x['pnl'])})" for x in good) + "."
                    if good else "In no price bucket does he guess better than the market beyond chance.")
    if rl["level"] == "unreliable":
        econ.append(f"Profit cannot be split by entry price: {rl['reasons'][0]}.")

    # 10. what the trades do not show and what could replace it
    cat = a["categories"][0]["category"] if a["categories"] else ""
    key = next((k for k in UNSEEN if cat.startswith(k)), None)
    unseen = [{"what": "why he picks the side and the moment", "replace": (f"he most likely looks at {UNSEEN[key][0]}; an open source of the same class – {UNSEEN[key][1]}. "
                                                                      "This is our candidate, not his source: it is checked by comparing his entries with this data")
                                                                     if key else "the signal is not visible in the trades; there is no candidate for this category"},
              {"what": "cancelled and unfilled orders", "replace": "only fills are visible; in the paper bot they are replaced by an order-book fill model"},
              {"what": "other wallets of the same owner", "replace": "cannot be linked using public data"}]
    return {"reliability": rl["level"], "steps": steps, "spec": spec, "economics": econ, "unseen": unseen}


def to_md(name: str, r: dict) -> str:
    L = [f"# Strategy recipe: {name}", "", f"Analysis reliability: {r['reliability']}.", "", "## Step by step", ""]
    L += [f"{i}. **{s['rule']}** {s['evidence']} ({s['strength']})." for i, s in enumerate(r["steps"], 1)]
    L += ["", "## Technical spec", "", "| parameter | value | evidence |", "|---|---|---|"]
    L += [f"| {s['param']} | {s['value']} | {s['evidence']} |" for s in r["spec"]]
    L += ["", "## Where the money comes from", ""] + [f"- {x}" for x in r["economics"]]
    L += ["", "## What the trades do not show and what could replace it", ""] + [f"- **{u['what']}** – {u['replace'].rstrip('.')}." for u in r["unseen"]]
    return "\n".join(L) + "\n"
