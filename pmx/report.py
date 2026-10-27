"""Wallet report: charts (matplotlib, dark terminal style) + report.html.
The plain-words explanation is assembled from the numbers in analysis.json and copysim.json by rules – no inventions."""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .signals import describe as describe_signal  # noqa: E402
from .style import AXES_TEXT  # noqa: E402

BG, FG, DIM, ACC, RED, GRID = "#0b0e11", "#e6edf3", "#7d8590", "#3fb950", "#f85149", "#21262d"
plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "axes.edgecolor": GRID, "axes.labelcolor": DIM, "xtick.color": DIM,
                     "ytick.color": DIM, "text.color": FG, "font.family": "monospace", "font.size": 10, "axes.grid": True, "grid.color": GRID})

FLOW = [("TRADE_BUY", "buys", -1), ("TRADE_SELL", "sells", 1), ("REDEEM", "payouts at resolution", 1), ("MERGE", "merges YES + NO → $", 1),
        ("SPLIT", "splits $ → YES + NO", -1), ("CONVERSION", "conversions NO → $ + YES", 1), ("REWARD", "liquidity rewards", 1),
        ("MAKER_REBATE", "maker rebates", 1), ("TAKER_REBATE", "taker rebates", 1), ("YIELD", "yield on balance", 1),
        ("REFERRAL_REWARD", "referrals", 1)]


def _img(fig) -> str:
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _money(x) -> str:
    if x is None:
        return "–"
    s = "−" if x < 0 else ""
    x = abs(x)
    return f"{s}${x / 1e6:.2f}M" if x >= 1e6 else (f"{s}${x / 1e3:.1f}k" if x >= 1e3 else f"{s}${x:,.0f}")


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.1f}%".replace("-", "−")


def _pval(p) -> str:
    return "–" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}")


def _n(x) -> str:
    return f"{x:,.0f}"


def _pv(p) -> str:
    return "< 0.001" if p < 0.001 else "= " + f"{p:.3f}"


def _dur(sec) -> str:
    sec = float(sec)
    if sec < 120:
        return f"{sec:.0f} s"
    if sec < 2 * 3600:
        return f"{sec / 60:.0f} min"
    if sec < 2 * 86400:
        return f"{sec / 3600:.0f} h"
    return f"{sec / 86400:.0f} d"


def _of(n: int, one: str, many: str) -> str:
    """'of 1 position', 'of 2 positions'."""
    return one if n == 1 else many


def _share(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def charts(a: dict, c: dict) -> dict:
    out = {}
    if a.get("curve"):
        pts = a["curve"]["points"]
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot([datetime.fromtimestamp(p[0], timezone.utc) for p in pts], [p[1] for p in pts], color=ACC, lw=2)
        ax.set_title("PnL, $", loc="left", color=FG)
        out["pnl"] = _img(fig)
    bb = [b for b in a["entry"]["by_price_bucket"] if b.get("cost")]
    if bb:
        fig, ax = plt.subplots(1, 2, figsize=(9, 3))
        names = [b["bucket"] for b in bb]
        ax[0].barh(names, [b["buy_usd_share"] * 100 for b in bb], color=DIM)
        ax[0].set_title("where the money goes, % of buys", loc="left", color=FG)
        ax[1].barh(names, [b["pnl"] for b in bb], color=[ACC if b["pnl"] >= 0 else RED for b in bb])
        ax[1].set_title("where the profit comes from, $", loc="left", color=FG)
        ax[1].set_yticklabels([])
        out["buckets"] = _img(fig)
    cats = a["categories"][:6]
    if cats:
        fig, ax = plt.subplots(figsize=(8, 2.6))
        ax.barh([x["category"] for x in cats][::-1], [x["share_volume"] * 100 for x in cats][::-1], color=ACC)
        ax.set_title("where he trades, % of volume", loc="left", color=FG)
        out["cats"] = _img(fig)
    hrs = a["timing"]["hours_utc"]
    fig, ax = plt.subplots(figsize=(8, 2.4))
    ax.bar([int(h) for h in hrs], [v * 100 for v in hrs.values()], color=ACC)
    ax.set_title("when he trades, % of trades by UTC hour", loc="left", color=FG)
    out["hours"] = _img(fig)
    # points where we manage to repeat less than 10% of his money are not drawn – there is nothing left to copy;
    # with no point at all (every position closed before a copy could enter) there is no chart and no copy section
    ms = [m for m in c["modes"] if c["overall"][m]["roi"] is not None and (c["overall"][m]["copied_share"] or 0) >= .1] \
        if c and c.get("overall") else []
    if ms:
        rois = [c["overall"][m]["roi"] * 100 for m in ms]
        fig, ax = plt.subplots(figsize=(8, 2.8))
        ax.axhline(0, color=DIM, lw=1)
        short = {"his": "himself", "m0": "instant"} | {f"m{d}": f"+{d} min" for d in c["delays_min"]}
        ax.plot([short.get(m, m) for m in ms], rois, color=ACC, marker="o", lw=2)
        for x, y in zip(range(len(ms)), rois):
            ax.annotate(f"{y:+.1f}%".replace("-", "−"), (x, y), textcoords="offset points", xytext=(0, 8), ha="center", color=FG, fontsize=9)
        ax.margins(y=0.25)
        ax.set_title("if you repeat his trades: return, %", loc="left", color=FG)
        out["copyline"] = _img(fig)
    return out


def plain(a: dict, c: dict) -> list[str]:
    """An explanation in plain words – from the numbers only."""
    p, e, b, rl = a["passport"], a["entry"], a["bot"], a["reliability"]
    L = []
    st = b.get("style") or {}
    ax, sg = st.get("axes") or [], st.get("signals") or {}
    head = {"bot": f"This is a bot – {st.get('label')}", "unclear": f"Unclear whether this is a bot or a human (by the rules – {st.get('label')})",
            "likely human": f"Likely a human trader – {st.get('label')}"}[b["verdict"]]
    h = int(round(sg.get("hours_active_per_day") or 0))
    hw = "hour" if h == 1 else "hours"
    L.append(f"{head}: {len(ax)} of 8 bot signals fired" + (f" – {'; '.join(x['text'] for x in ax)}" if ax else "")
             + (f"; plus a hard marker: {st['override']}" if st.get("override") else "")
             + f". {_n(b['trades_per_active_day'])} trades per active day, usually active {h} {hw} a day, "
             f"in {int((e['maker_share_trades'] or 0) * 100)}% of trades he places his own order in the book and waits"
             + (f" (by money – {int((e['maker_share_usd'] or 0) * 100)}%)." if abs((e['maker_share_usd'] or 0) - (e['maker_share_trades'] or 0)) > .3 else "."))
    if a["categories"]:
        top = a["categories"][0]
        L.append(f"Main market – {top['category']}: {int(top['share_volume'] * 100)}% of all volume.")
    cr = a.get("carry") or {}
    if cr.get("is_carry"):
        L.append(f"Strategy – collecting the nearly decided (carry): he puts {_share(cr['carry_frac'])} of money on outcomes the market already considers "
                 f"nearly decided (85¢ and above), and they win "
                 f"in {_share(cr['carry_win_rate'])} of cases; " + ("holds to resolution." if cr["type"] == "carry-hold" else "resells without waiting for resolution."))
    pg = (a.get("pairs") or {}).get("pairing") or {}
    cs_ = (a.get("pairs") or {}).get("complete_sets") or {}
    if pg.get("both_legs_share", 0) >= .3:
        line = (f"In {_share(pg['both_legs_share'])} of markets he buys both sides; the second leg comes {pg['opposite_leg_delay_sec']['p50']:.0f} s later "
                f"(median).")
        if cs_.get("locked_pnl") is not None and cs_.get("avg_edge_per_share") is not None:
            lk = cs_["locked_pnl"]
            line += (f" A pair costs him ${1 - cs_['avg_edge_per_share']:.3f} on average against a guaranteed $1, so on the pairs themselves he "
                     + (f"earns {_money(lk)}" if lk > 0 else f"loses {_money(-lk)}")
                     + f", and the rest is a tilt toward one outcome: {_money(cs_['directional_pnl'])}.")
        L.append(line)
    tt = a.get("timing_to_end") or {}
    if tt.get("median_seconds_to_end") is not None:
        ser = tt.get("short_series") or {}
        when = _dur(tt["median_seconds_to_end"])
        L.append(f"Trades short markets ({', '.join(f'{k} – {_share(v)}' for k, v in list(ser.items())[:3])}) and buys on average "
                 f"{when} before trading ends." if sum(ser.values()) > .5 else f"Buys on average {when} before trading ends.")
    for x in describe_signal(a.get("signal") or {}):
        L.append(x)
    if a.get("direction_warning"):
        L.append(f"Caution: {a['direction_warning']}.")
    for x in a["archetypes"]:
        if x["type"].startswith(("converts NO", "merges YES+NO pairs")):
            L.append(f"Mechanics: {x['type']} – {x['evidence']}.")
    bb = [x for x in e["by_price_bucket"] if x.get("cost")]
    if bb:
        money = max(bb, key=lambda x: x["buy_usd_share"] or 0)
        L.append(f"He puts the most money ({int(money['buy_usd_share'] * 100)}%) at price {money['bucket']}.")
        if rl["level"] == "unreliable":
            L.append(f"Where exactly he earns by entry price cannot be honestly computed from his trades: {rl['reasons'][0]}.")
        else:
            two_sided = (b.get("both_sides_markets_share") or 0) > .3
            if two_sided:
                L.append(f"He buys both sides in {int(b['both_sides_markets_share'] * 100)}% of markets, so no conclusions are drawn here from the price buckets: "
                         "the expensive and cheap legs are parts of one pair, a win on one and a loss on the other are not two forecasts.")
            good = [] if two_sided else [x for x in bb if x.get("verdict") == "beats the price"]
            bad = [x for x in bb if x.get("verdict") == "loses to the price"]
            if two_sided:
                pass
            elif good:
                g = max(good, key=lambda x: x["test"]["wins"] - x["test"]["expected_wins"])
                n = g["test"]["n"]
                L.append(f"He guesses better than the market at price {g['bucket']}: {_n(g['test']['wins'])} wins out of {_n(n)} {_of(n, 'position', 'positions')} "
                         f"against {_n(g['test']['expected_wins'])} expected from the price. This is not chance (p {_pv(g['p_adjusted'])}), "
                         f"the return there is {_pct(g['roi'])}.")
            else:
                L.append("In no price bucket can we say with confidence that he guesses better than the market: the differences fit within chance.")
            for x in ([] if two_sided else bad):
                L.append(f"At price {x['bucket']} he wins less often than the price promises: {_n(x['test']['wins'])} out of {_n(x['test']['n'])} "
                         f"against {_n(x['test']['expected_wins'])} expected.")
    sh = p.get("share_from_rewards_and_rebates")
    if sh is not None and sh > 0.2:
        L.append(f"{int(sh * 100)}% of his profit is not bets but Polymarket rewards for liquidity and fee refunds.")
    L.append(f"Official Polymarket total: {_money(p['pnl_economic'])} on a volume of {_money(p['volume_usdc'])}.")
    if c and c.get("overall"):
        o = c["overall"]
        r = lambda m: _pct(o[m]["roi"])
        def cp(m, word):
            cs = o[m]["copied_share"] or 0
            if o[m]["roi"] is None or cs < .1:
                return f"{word} – too late: the markets are already closed by then"
            return f"{word} – {r(m)}" + (f" (manages to repeat {_share(cs)} of his money)" if cs < .95 else "")
        steps = [("m0", "a copy right after him on the market"), ("m1", "after a minute"), ("m5", "after 5 minutes"), ("m15", "after 15 minutes"), ("m60", "after an hour")]
        parts = [cp(m, w) for m, w in steps]
        late = [i for i, x in enumerate(parts) if x.endswith("the markets are already closed by then")]
        if len(late) > 1 and late == list(range(late[0], len(parts))):     # "after 15 minutes and later – too late"
            parts = parts[:late[0]] + [f"{steps[late[0]][1]} and later – too late: the markets are already closed by then"]
        slip = next((o[m]["slip_pp_per_cent"] for m in ("m1", "m0") if o[m]["slip_pp_per_cent"] is not None), None)
        L.append(f"If you repeat his trades: on his latest positions he himself got {r('his')}; " + ", ".join(parts) + "."
                 + (f" Each cent of slippage costs another {slip:.1f} pp." if slip is not None else ""))
        if any(x["type"].startswith(("converts NO", "merges YES+NO pairs")) for x in a["archetypes"]):
            L.append("Important: the model repeats only his trades. Conversions and merges are separate operations, and his profit comes through them, "
                     "so a copy of the trades alone is not his strategy, and its return says nothing about his result.")
    return L


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ name }} – wallet analysis</title><style>
:root{--bg:#0b0e11;--card:#11161c;--fg:#e6edf3;--dim:#7d8590;--acc:#3fb950;--red:#f85149;--yel:#d29922;--line:#21262d}
body{margin:0;background:#05070a;color:var(--fg);font:15px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.win{max-width:980px;margin:32px auto;background:var(--bg);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:0 30px 80px #000a}
.bar{display:flex;gap:8px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);color:var(--dim)}
.dot{width:12px;height:12px;border-radius:50%}.r{background:#ff5f57}.y{background:#febc2e}.g{background:#28c840}
main{padding:24px 28px}h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;color:var(--acc);margin:28px 0 10px;text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--dim)}.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.k b{display:block;font-size:20px}.k span{color:var(--dim);font-size:12px}
ul.plain li{margin:6px 0}img{width:100%;border-radius:8px;border:1px solid var(--line);margin:6px 0}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;white-space:nowrap}td:first-child{white-space:normal}th{color:var(--dim);font-weight:400}td:first-child,th:first-child{text-align:left}
.tag{display:inline-block;border:1px solid var(--acc);color:var(--acc);border-radius:999px;padding:2px 10px;margin:2px 4px 2px 0;font-size:12px}
.rel{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:16px}.rel b{font-size:14px}
.rel.ok{border-color:var(--acc)}.rel.ok b{color:var(--acc)}.rel.warn{border-color:var(--yel)}.rel.warn b{color:var(--yel)}.rel.bad{border-color:var(--red)}.rel.bad b{color:var(--red)}
.v-good{color:var(--acc)}.v-bad{color:var(--red)}.v-none{color:var(--dim)}
.wrap{overflow-x:auto}ol.steps li{margin:10px 0}ol.steps .tag{font-size:11px;padding:0 8px}.vv{white-space:normal;min-width:120px;text-align:left}.muted{opacity:.45}
@media(max-width:700px){.kpi{grid-template-columns:repeat(2,1fr)}main{padding:16px}}
</style></head><body><div class="win"><div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>&nbsp; pmx · wallet x-ray</div><main>
<h1>{{ name }}</h1><div class="sub">{{ wallet }} · since {{ join }} · exported {{ exported }}</div>
<div class="kpi"><div class="k"><b>{{ pnl }}</b><span>total PnL (official)</span></div><div class="k"><b>{{ vol }}</b><span>volume</span></div>
<div class="k"><b>{{ trades }}</b><span>trades exported out of {{ trades_off }}</span></div><div class="k"><b>{{ verdict }}</b><span>{{ bstyle.label or 'who trades' }}</span></div></div>
<div class="rel {{ rel_cls }}"><b>Analysis reliability: {{ rel.level }}</b>
<div class="sub">own trade-based accounting {{ money(rel.ledger_realized) }} vs official {{ money(rel.official_realized) }} ({{ pct(rel.ledger_vs_official) }}) ·
{{ share(rel.coverage_buy_usd) }} of buy money made it into the bucket analysis · fees by formula {{ money(fees_formula) }}, official {{ money(fees_off) }}</div>
{% if rel.reasons %}<ul>{% for x in rel.reasons %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}</div>
{% if rec.steps %}<h2>Strategy recipe</h2><div class="sub">what he does, step by step; each step has evidence from his trades and the strength of the rule</div>
<ol class="steps">{% for x in rec.steps %}<li><b>{{ x.rule }}</b><br><span class="sub">{{ x.evidence }}</span> <span class="tag">{{ x.strength }}</span></li>{% endfor %}</ol>{% endif %}
<h2>In plain words</h2><ul class="plain">{% for l in plain %}<li>{{ l }}</li>{% endfor %}</ul>
<h2>Strategy type</h2>{% for x in arche %}<div><span class="tag">{{ x.type }}</span> <span class="sub">{{ x.evidence }}</span></div>{% endfor %}
{% if rec.spec %}<h2>Technical spec</h2><div class="wrap"><table><tr><th>parameter</th><th>value</th><th>evidence</th></tr>
{% for x in rec.spec %}<tr><td>{{ x.param }}</td><td class="vv">{{ x.value }}</td><td class="vv sub">{{ x.evidence }}</td></tr>{% endfor %}</table></div>
<h2>Where the money comes from</h2><ul>{% for x in rec.economics %}<li>{{ x }}</li>{% endfor %}</ul>
<h2>What the trades do not show and what could replace it</h2><ul>{% for u in rec.unseen %}<li><b>{{ u.what }}</b> – <span class="sub">{{ u.replace }}</span></li>{% endfor %}</ul>{% endif %}
<h2>Bot or human</h2><div class="sub">8 signals from Dennim89/polymarket-wallet-analyzer: a bot at 4 or more{% if bstyle.override %} · pmx hard marker: {{ bstyle.override }}{% endif %}</div>
<table><tr><th>signal</th><th>fired</th></tr>{% for k, txt in axes_all %}<tr><td>{{ txt }}</td><td class="{{ 'v-good' if k in axes_on else 'v-none' }}">{{ 'yes' if k in axes_on else '–' }}</td></tr>{% endfor %}</table>
{% if pg and pg.both_legs_share >= 0.1 %}<h2>Leg pairs</h2><div class="sub">whether he buys both sides of one market – after polybot (ent0n29/polybot)</div>
<table><tr><td>markets where both sides were bought</td><td>{{ share(pg.both_legs_share) }} ({{ "{:,}".format(pg.markets_both_legs) }} of {{ "{:,}".format(pg.markets_with_buys) }})</td></tr>
<tr><td>second leg after the first: median / 90% / within a minute</td><td>{{ pg.opposite_leg_delay_sec.p50 }} s / {{ pg.opposite_leg_delay_sec.p90 }} s / {{ share(pg.opposite_leg_delay_sec.share_within_60s) }}</td></tr>
<tr><td>share imbalance between the sides (median)</td><td>{{ share(pg.imbalance_median) }}</td></tr>
{% if csx.avg_edge_per_share is not none %}<tr><td>a pair costs on average (guaranteed to receive $1)</td><td>${{ '%.3f'|format(1 - csx.avg_edge_per_share) }}</td></tr>
<tr><td>locked in pairs / directional part</td><td>{{ money(csx.locked_pnl) }} / {{ money(csx.directional_pnl) }}</td></tr>{% endif %}</table>{% endif %}
{% if sig.by_move %}<h2>Signal: what he looks at</h2><div class="sub">{{ sig.assets|join('/') }} price move since the window start at the minute of purchase – {{ sig.source }}; {{ sig.markets }} latest markets</div>
<table><tr><th>price move since the window start</th><th>share of his money</th><th>on the leading side</th><th>the leader ended up winning</th></tr>
{% for x in sig.by_move %}<tr><td>{{ x.move }}</td><td>{{ share(x.share_usd) }}</td><td>{{ share(x.on_lead_usd) }}</td><td>{{ share(x.lead_wins) }}</td></tr>{% endfor %}</table>
{% if sig.buy_leader %}<div class="sub">leader buys: win {{ share(sig.buy_leader.win_rate) }} at an average price of {{ '%.2f'|format(sig.buy_leader.avg_price) }} ({{ pct(sig.buy_leader.roi_before_fees) }} before fees) ·
against the leader: {{ share(sig.buy_against.win_rate) }} at {{ '%.2f'|format(sig.buy_against.avg_price) }} ({{ pct(sig.buy_against.roi_before_fees) }})</div>{% endif %}{% endif %}
{% if tt.buckets %}<h2>When he buys relative to the market end</h2><table><tr><th>until trading ends</th><th>share of trades</th><th>share of money</th><th>median shares</th><th>average price</th></tr>
{% for x in tt.buckets %}<tr><td>{{ x.bucket }}</td><td>{{ share(x.share_trades) }}</td><td>{{ share(x.share_usd) }}</td><td>{{ x.median_shares }}</td><td>{{ x.avg_price }}</td></tr>{% endfor %}</table>{% endif %}
{% if outcomes %}<h2>Profit by outcome</h2>{% if dwarn %}<div class="rel warn"><b>Caution</b><div class="sub">{{ dwarn }}</div></div>{% endif %}
<table><tr><th>outcome</th><th>positions</th><th>invested</th><th>PnL</th></tr>{% for o in outcomes %}<tr><td>{{ o.outcome }}</td><td>{{ "{:,}".format(o.positions) }}</td><td>{{ money(o.cost) }}</td><td>{{ money(o.pnl) }}</td></tr>{% endfor %}</table>{% endif %}
<h2>Where the money flows</h2><div class="sub">from the Polymarket activity feed, no models</div>
<table><tr><th>flow</th><th>rows</th><th>amount</th></tr>
{% for f in flow %}<tr><td>{{ f.label }}</td><td>{{ "{:,}".format(f.rows) }}</td><td>{{ f.usd }}</td></tr>{% endfor %}</table>
{% if ch.buckets %}<h2>Where the money goes and where the profit comes from</h2>
{% if rel.level == 'unreliable' %}<div class="rel bad"><b>The buckets are unreliable for this wallet</b><div class="sub">{{ rel.reasons[0] }}</div></div>{% endif %}
<img src="{{ ch.buckets }}"><div class="wrap{{ ' muted' if rel.level == 'unreliable' else '' }}">
<table><tr><th>entry price</th><th>share of money</th><th>positions</th><th>won / expected</th><th>edge, pp</th><th>p</th><th>verdict</th><th>invested</th><th>PnL</th><th>return</th></tr>
{% for b in buckets %}<tr><td>{{ b.bucket }}</td><td>{{ (b.buy_usd_share*100)|round(1) }}%</td><td>{{ b.resolved_positions }}</td>
<td>{{ b.test.wins if b.test.n else '–' }} / {{ b.test.expected_wins if b.test.n else '–' }}</td><td>{{ b.test.edge_pp if b.test.n else '–' }}</td>
<td>{{ pval(b.p_adjusted) }}</td>
<td class="vv {{ 'v-good' if b.verdict == 'beats the price' else ('v-bad' if b.verdict == 'loses to the price' else 'v-none') }}">{{ b.verdict }}</td>
<td>{{ money(b.cost) }}</td><td>{{ money(b.pnl) }}</td><td>{{ pct(b.roi) }}</td></tr>{% endfor %}</table></div>
<div class="sub">edge – by how many percentage points he wins more often than the entry price promised. p – the probability of getting such a difference by chance,
adjusted for testing several buckets at once; below 0.05 – not chance.</div>{% endif %}
{% if ch.pnl %}<h2>Profit curve</h2><img src="{{ ch.pnl }}">{% endif %}
<h2>Where and when he trades</h2>{% if ch.cats %}<img src="{{ ch.cats }}">{% endif %}<img src="{{ ch.hours }}">
{% if ch.copyline %}<h2>Can this be copied</h2><img src="{{ ch.copyline }}">
<table><tr><th></th><th>return</th><th>of his money repeated</th><th>positions</th></tr>
{% for m in cs.modes %}<tr><td>{{ cs.labels[m] }}</td><td>{{ pct(cs.overall[m].roi) }}</td><td>{{ share(cs.overall[m].copied_share) }}</td><td>{{ cs.overall[m].positions }}</td></tr>{% endfor %}</table>
<div class="sub">the latest {{ cs.sample }} of {{ cs.population_positions }} resolved positions, the total weighted by his money in the buckets ·
the copier is a taker paying the fee, buying no cheaper than he did{% if cs.overall['m1'].slip_pp_per_cent is not none %} · each 1¢ of slippage costs another {{ '%.1f'|format(cs.overall['m1'].slip_pp_per_cent) }} pp{% endif %}</div>{% endif %}
<h2>Best and worst positions</h2><table><tr><th>market</th><th>outcome</th><th>entry</th><th>invested</th><th>PnL</th></tr>
{% for r in best + worst %}<tr><td>{{ r.title }}</td><td>{{ r.outcome }}</td><td>{{ r.avg_price }}</td><td>{{ money(r.cost) }}</td><td>{{ money(r.pnl) }}</td></tr>{% endfor %}</table>
<h2>Calculation limits</h2><ul>{% for l in limits %}<li class="sub">{{ l }}</li>{% endfor %}</ul>
</main></div></body></html>"""


def report(d: Path) -> Path:
    from jinja2 import Template
    a = json.loads((d / "analysis.json").read_text())
    c = json.loads((d / "copysim.json").read_text()) if (d / "copysim.json").exists() else {}
    if c and "modes" not in c:              # copysim.json from an old version
        c = {}
    p, rl = a["passport"], a["reliability"]
    flow = [{"label": lbl, "rows": a["money_flow"][k]["rows"], "usd": _money(sign * a["money_flow"][k]["usd"])}
            for k, lbl, sign in FLOW if k in a.get("money_flow", {})]
    html = Template(HTML).render(
        name=p.get("name") or p.get("wallet"), wallet=p.get("wallet"), join=p.get("join_date"), exported=a["generated"][:10],
        pnl=_money(p.get("pnl_economic")), vol=_money(p.get("volume_usdc")), trades=f"{p.get('trades_exported') or 0:,}",
        trades_off=f"{p.get('trades_all_time') or 0:,}", verdict=a["bot"]["verdict"], rel=rl,
        bstyle=a["bot"].get("style") or {}, axes_all=list(AXES_TEXT.items()), axes_on={x["axis"] for x in (a["bot"].get("style") or {}).get("axes", [])},
        pg=(a.get("pairs") or {}).get("pairing") or {}, csx=(a.get("pairs") or {}).get("complete_sets") or {}, tt=a.get("timing_to_end") or {},
        outcomes=(a.get("by_outcome") or [])[:6], dwarn=a.get("direction_warning"), sig=a.get("signal") or {}, rec=a.get("recipe") or {},
        rel_cls={"reliable": "ok", "approximate": "warn"}.get(rl["level"], "bad"),
        fees_formula=p.get("fees_by_formula"), fees_off=p.get("fees_paid_official"), flow=flow,
        plain=plain(a, c), arche=a["archetypes"], ch=charts(a, c), cs=c,
        buckets=[b for b in a["entry"]["by_price_bucket"] if b.get("cost")], best=a["markets"]["best"], worst=a["markets"]["worst"],
        limits=a["limits"] + (c.get("limits") or []), money=_money, pct=_pct, share=_share, pval=_pval)
    (d / "report.html").write_text(html)
    (d / "summary.md").write_text("\n".join(f"- {x}" for x in plain(a, c)) + "\n")
    return d / "report.html"
