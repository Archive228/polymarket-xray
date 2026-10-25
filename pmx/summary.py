"""Summary of all analyzed wallets: python3 -m pmx.summary → WALLET_SUMMARY.md"""
from __future__ import annotations

import json
from pathlib import Path

from .api import STORE
from .export import ROOT

OUT = STORE / "WALLET_SUMMARY.md"


def _money(x) -> str:
    if x is None:
        return "–"
    s = "−" if x < 0 else ""
    x = abs(x)
    return f"{s}${x / 1e6:.2f}M" if x >= 1e6 else (f"{s}${x / 1e3:.0f}k" if x >= 1e3 else f"{s}${x:,.0f}")


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.1f}%".replace("-", "−")


def row(d: Path) -> dict | None:
    pa, pc = d / "analysis.json", d / "copysim.json"
    if not pa.exists():
        return None
    a = json.loads(pa.read_text())
    c = json.loads(pc.read_text()) if pc.exists() else {}
    p, st = a["passport"], (a["bot"].get("style") or {})
    if not p.get("name") or str(p["name"]).startswith("0x"):          # exported by address – take the profile name
        from . import api
        p["name"] = api.profile_name(p["wallet"]) or p["wallet"][:10]
        mp = d / "meta.json"
        if mp.exists():
            meta = json.loads(mp.read_text())
            meta["name"] = p["name"]
            mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    o = c.get("overall") or {}
    cp = lambda m: (o.get(m) or {}).get("roi") if (o.get(m) or {}).get("copied_share", 0) and o[m]["copied_share"] >= .1 else None
    steps = (a.get("recipe") or {}).get("steps") or []
    return {"name": p.get("name") or p["wallet"][:10], "wallet": p["wallet"], "cat": a["categories"][0]["category"] if a["categories"] else "–",
            "pnl": p.get("pnl_economic"), "trades": p.get("trades_exported"), "type": st.get("label", "–"), "verdict": a["bot"]["verdict"],
            "axes": len(st.get("axes") or []), "rel": a["reliability"]["level"], "steps": [s["rule"] for s in steps],
            "his": cp("his"), "m0": cp("m0"), "m1": cp("m1"), "m15": cp("m15"), "m60": cp("m60"), "positions": (o.get("his") or {}).get("positions"),
            "signal": (a.get("signal") or {}).get("on_lead_usd")}


def main() -> None:
    rows = [r for r in (row(d) for d in sorted(ROOT.iterdir()) if d.is_dir()) if r]
    rows.sort(key=lambda r: -(r["pnl"] or 0))
    L = ["# Summary of analyzed wallets", "", f"Wallets: {len(rows)}. Total – the official Polymarket PnL. Copying – return on the latest "
         "positions: himself / right after him / after a minute / after 15 minutes / after an hour ('–' means the copy is already too late).", "",
         "| wallet | market | total | trades | who | reliability | copy: himself / instant / +1 / +15 / +60 min | positions |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['name']} | {r['cat']} | {_money(r['pnl'])} | {r['trades']:,} | {r['verdict']}: {r['type']} ({r['axes']}/8) | {r['rel']} | "
                 + " / ".join(_pct(r[k]) for k in ("his", "m0", "m1", "m15", "m60")) + f" | {r['positions'] or '–'} |")
    L += ["", "## Recipes in brief", ""]
    for r in rows:
        L += [f"### {r['name']} – {r['type']}", f"`{r['wallet']}` · reliability: {r['rel']}", ""] + [f"{i}. {s}" for i, s in enumerate(r["steps"], 1)] + [""]
    OUT.write_text("\n".join(L))
    print(f"summary: {OUT}")


if __name__ == "__main__":
    main()
