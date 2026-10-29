"""Copy verdicts for many wallets: the top of a Polymarket leaderboard, or a watchlist re-checked against its last check.

python3 -m pmx top --category SPORTS --period WEEK --n 10   → TOP_<category>_<period>_<date>.md in the data folder
python3 -m pmx watch <wallet> [<wallet> …]                   → what changed since each wallet's previous check

The verdict follows the skill's rule: "no" when a copy already loses right after him or one minute late; "yes, up to N min late" for the
longest delay that still makes money with at least half of his money copied; "unclear" under 30 resolved positions or when the breakdown
is unreliable. Every verdict is appended to verdicts.jsonl in the wallet's folder, so the next check can say what changed."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import api
from .api import STORE
from .export import ROOT, export

V1 = "https://data-api.polymarket.com/v1"
CATEGORIES = ("OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE", "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE", "ESPORTS")
PERIODS = ("DAY", "WEEK", "MONTH", "ALL")
DELAYS = (("m60", 60), ("m15", 15), ("m5", 5), ("m1", 1))     # longest first: the verdict names the longest delay that still pays
MIN_POSITIONS = 30
THIN = .02                                                   # a "yes" under +2% a minute late says the edge is thin
FULL_SCORE = .25                                             # a copy a minute late at +25% or more gets the full Copy Score
TRUST = {"reliable": 1, "approximate": .8, "unreliable": .4}
BIG = 300_000                                                # above this many trades only the last 30 days are exported, as in xray.sh


def leaderboard(category: str = "OVERALL", period: str = "WEEK", n: int = 10) -> list[dict]:
    """Top n wallets by profit in one category of the Polymarket leaderboard."""
    rows = api.get(f"{V1}/leaderboard", {"timePeriod": period, "orderBy": "PNL", "limit": n, "category": category}) or []
    return [{"wallet": r["proxyWallet"].lower(), "name": r.get("userName") or r["proxyWallet"][:10], "pnl": r.get("pnl"),
             "volume": r.get("vol")} for r in rows if r.get("proxyWallet")]


def xray(wallet: str, days: int | None = None, log=print) -> Path:
    """The whole pipeline for one wallet, as `pmx all`."""
    from .analyze import analyze
    from .copysim import copysim
    from .report import report
    from .signals import apply_to_recipe, signal
    if days is None and ((api.stats(wallet).get("all_time_pnl") or {}).get("trade_count") or 0) > BIG:
        days = 30
        log(f"   more than {BIG:,} trades – the last {days} days")
    d = export(wallet, days=days, log=log)
    analyze(d)
    try:
        s = signal(d)
    except Exception as e:                      # the signal is an add-on; the verdict does not need it
        s = {}
        log(f"   signal not checked: {e}")
    if s:
        apply_to_recipe(d, s)
    copysim(d)
    report(d)
    return d


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.1f}%".replace("-", "−")


def _score(x) -> str:
    return "–" if x is None else f"{x}/100"


def _span(sec) -> str:
    if sec is None:
        return "–"
    if sec < 3600:
        return f"{sec / 60:.0f} min"
    return f"{sec / 3600:.0f} h" if sec < 2 * 86400 else f"{sec / 86400:.0f} days"


def copy_score(v: str, m1, delays_ok: int, positions: int, level: str) -> int | None:
    """Copy Score, 0–100: the four checks in one number. How much a copy one minute late still makes (+25% or more is full
    marks), how many of the delays (1, 5, 15, 60 min) still pay, and how far to trust it (sample size, data check).
    0 for a "no"; none when nothing has resolved yet."""
    if positions == 0:
        return None
    if v == "no" or not m1 or m1 <= 0:
        return 0
    return round(100 * min(1, m1 / FULL_SCORE) * (.4 + .6 * delays_ok / len(DELAYS)) * min(1, positions / MIN_POSITIONS) * TRUST[level])


def verdict(d: Path) -> dict:
    """Copy? yes / no / unclear for an analysed wallet folder, with the four checks behind it."""
    a = json.loads((d / "analysis.json").read_text())
    pc = d / "copysim.json"
    o = (json.loads(pc.read_text()) if pc.exists() else {}).get("overall") or {}
    roi = lambda m: (o.get(m) or {}).get("roi")
    share = lambda m: (o.get(m) or {}).get("copied_share") or 0
    rel = a["reliability"]
    positions = (o.get("his") or {}).get("positions") or 0
    m1 = roi("m1") if share("m1") >= .1 else None
    to_end = (a.get("timing_to_end") or {}).get("median_seconds_to_end")
    if positions == 0:
        v, why = "unclear", "none of its recent positions has resolved yet, so there is nothing to replay"
    elif positions < MIN_POSITIONS:
        v, why = "unclear", f"only {positions} resolved position{'s' if positions > 1 else ''} to replay, it could be luck"
    elif rel["level"] == "unreliable":
        v, why = "unclear", (rel["reasons"] or ["its trades do not explain its profit"])[0]
    elif roi("m0") is not None and roi("m0") < 0:
        v, why = "no", f"a copy right after it already loses {_pct(roi('m0'))}"
    elif share("m1") < .1:
        v, why = "no", "a minute later most of its markets have closed"
    elif m1 is not None and m1 < 0:
        v, why = "no", f"one minute late the copy loses {_pct(m1)}"
    else:
        late = next((n for m, n in DELAYS if (roi(m) or 0) > 0 and share(m) >= .5), None)
        v, why = ("yes", f"up to {late} min late") if late else ("no", "a minute late less than half of its money can still be copied")
        if late and m1 is not None and m1 < THIN:
            why += f", but thin: {_pct(m1)} a minute late"
    st = a["bot"].get("style") or {}
    return {"verdict": v, "line": f"{v}, {why}" if v == "yes" else f"{v}: {why}",
            "score": copy_score(v, m1, sum((roi(m) or 0) > 0 and share(m) >= .5 for m, _ in DELAYS), positions, rel["level"]),
            "name": a["passport"].get("name") or a["passport"]["wallet"][:10], "wallet": a["passport"]["wallet"],
            "who": f"{a['bot']['verdict']}, {st.get('label', '–')}", "category": a["categories"][0]["category"] if a["categories"] else "–",
            "his": roi("his"), "m1": m1, "entry_before_end": _span(to_end), "reliability": rel["level"], "positions": positions,
            "steps": [s["rule"] for s in ((a.get("recipe") or {}).get("steps") or [])][:3], "report": str(d / "report.html")}


def record(d: Path, v: dict) -> dict | None:
    """Append the verdict to the wallet's history; return the previous one."""
    p = d / "verdicts.jsonl"
    lines = p.read_text().splitlines() if p.exists() else []
    prev = json.loads(lines[-1]) if lines else None
    keep = {k: v[k] for k in ("verdict", "line", "score", "his", "m1", "category", "who")}
    with p.open("a") as f:
        f.write(json.dumps({"checked": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), **keep}) + "\n")
    return prev


def changes(prev: dict | None, v: dict) -> list[str]:
    """What moved since the previous check."""
    if not prev:
        return ["first check"]
    out = []
    if prev["verdict"] != v["verdict"]:
        out.append(f"verdict: {prev['line']} → {v['line']}")
    if prev.get("m1") is not None and v["m1"] is not None and (prev["m1"] < 0) != (v["m1"] < 0):
        out.append(f"one minute late: {_pct(prev['m1'])} → {_pct(v['m1'])}")
    if prev.get("score") is not None and v["score"] is not None and abs(v["score"] - prev["score"]) >= 15:
        out.append(f"Copy Score: {prev['score']} → {v['score']}")
    if prev.get("category") != v["category"]:
        out.append(f"main market: {prev.get('category')} → {v['category']}")
    if prev.get("his") is not None and v["his"] is not None and abs(v["his"] - prev["his"]) >= .05:
        out.append(f"its own return on recent positions: {_pct(prev['his'])} → {_pct(v['his'])}")
    return out or [f"no change since {prev['checked']}"]


def _check(target: str, days, log) -> tuple[dict | None, str | None]:
    from .analyze import NoTrades
    try:
        w, name = api.resolve_wallet(target)
        d = xray(w, days, log)
        return verdict(d), None
    except NoTrades:                            # profit from rewards or redemptions only: a verdict, not a failure
        return {"verdict": "unclear", "line": "unclear: it has no trades, its profit does not come from trading", "score": None,
                "name": name, "wallet": w, "who": "–", "category": "–", "his": None, "m1": None, "entry_before_end": "–",
                "reliability": "unreliable", "positions": 0, "steps": [], "report": str(ROOT / w / "report.html")}, None
    except (Exception, SystemExit) as e:        # one broken wallet must not stop the rest of the list
        return None, str(e)


def top(category: str = "OVERALL", period: str = "WEEK", n: int = 10, days: int | None = None, log=print) -> Path:
    board = leaderboard(category, period, n)
    if not board:
        raise SystemExit(f"the leaderboard returned nothing for {category}, {period}")
    rows = []
    for i, b in enumerate(board, 1):
        log(f"\n[{i}/{len(board)}] {b['name']} ({b['wallet']})")
        v, err = _check(b["wallet"], days, log)
        if v:
            record(Path(v["report"]).parent, v)
        rows.append((b, v, err))
    day = dt.datetime.utcnow().strftime("%Y-%m-%d")
    L = [f"# Polymarket top {len(board)} – {category.lower()}, {period.lower()}, {day}", "",
         "Copy verdict for each wallet: yes (up to how many minutes late a copy still makes money), no, or unclear, and its "
         "Copy Score from 0 to 100. Return = the wallet's own recent resolved positions; 1 min late = a copy one minute behind it, "
         "paying the taker fee.", "",
         f"| # | wallet | profit, {period.lower()} | who | copy? | score | own return | 1 min late | enters before the end | positions |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (b, v, err) in enumerate(rows, 1):
        pnl = f"${b['pnl']:,.0f}" if b["pnl"] is not None else "–"
        if v:
            L.append(f"| {i} | {b['name']} | {pnl} | {v['who']} | **{v['line']}** | {_score(v['score'])} | {_pct(v['his'])} | {_pct(v['m1'])} | "
                     f"{v['entry_before_end']} | {v['positions']} |")
        else:
            L.append(f"| {i} | {b['name']} | {pnl} | – | not checked: {err} | | | | | |")
    yes = sorted((v for _, v, _ in rows if v and v["verdict"] == "yes"), key=lambda v: -(v["score"] or 0))
    L += ["", f"## Worth a closer look: {len(yes)}", ""]
    for v in yes:
        L += [f"### {v['name']} – Copy Score {_score(v['score'])}, {v['line']}", f"`{v['wallet']}` · {v['category']} · reliability: {v['reliability']}", ""]
        L += [f"{j}. {s}" for j, s in enumerate(v["steps"], 1)] + ["", f"Report: {v['report']}", ""]
    if not yes:
        L.append("None of them can be copied at a profit right now.")
    L += ["", "Analysis of public data, not financial advice."]
    out = STORE / f"TOP_{category}_{period}_{day}.md"
    out.write_text("\n".join(L))
    return out


def watch(targets: list[str], days: int | None = None, log=print) -> list[str]:
    out = []
    for t in targets:
        log(f"\n{t}")
        v, err = _check(t, days, log)
        if not v:
            out.append(f"{t}: not checked: {err}")
            continue
        prev = record(Path(v["report"]).parent, v)
        out.append(f"{v['name']}: {v['line']} · Copy Score {_score(v['score'])}\n" + "".join(f"   – {c}\n" for c in changes(prev, v)).rstrip())
    return out
