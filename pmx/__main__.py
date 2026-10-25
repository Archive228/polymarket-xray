"""Command line: python3 -m pmx export <address | profile link | name>"""
from __future__ import annotations

import argparse
import json

from .export import export


def main() -> None:
    p = argparse.ArgumentParser(prog="pmx", description="Reverse-engineer Polymarket wallets")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, h in (("export", "download all the wallet's data"), ("analyze", "work out the strategy from the export"),
                    ("copysim", "compute what is left when copying with a delay"), ("report", "build the report with charts"),
                    ("signal", "crypto Up/Down: check whether he reacts to the price move since the window start (Binance)"),
                    ("all", "everything: export, analyze, check the signal, simulate copying, build the report")):
        sp = sub.add_parser(name, help=h)
        sp.add_argument("target")
        sp.add_argument("--days", type=int, default=None, help="export only the last N days (for huge wallets)")
    a = p.parse_args()
    if a.cmd in ("export", "all"):
        d = export(a.target, days=a.days)
    if a.cmd in ("analyze", "all"):
        from .analyze import analyze
        from . import api
        from .export import ROOT
        d = ROOT / api.resolve_wallet(a.target)[0]
        r = analyze(d)
        rl = r["reliability"]
        print(f"analysis reliability: {rl['level']}" + "".join(f"\n   – {x}" for x in rl["reasons"]))
        print("strategy recipe:")
        for i, s in enumerate(r["recipe"]["steps"], 1):
            print(f"   {i}. {s['rule']} ({s['strength']})")
        print(f"full recipe: {d / 'recipe.md'}")
        print(f"full analysis: {d / 'analysis.json'}")
    if a.cmd in ("signal", "all"):
        from .signals import apply_to_recipe, describe, signal
        from . import api
        from .export import ROOT
        d = ROOT / api.resolve_wallet(a.target)[0]
        try:
            s = signal(d)
        except Exception as e:                      # the signal is an add-on; without network the report is still built
            s = {}
            print(f"signal not checked: {e}")
        if s:
            apply_to_recipe(d, s)
            print("\n".join(describe(s)))
        elif a.cmd == "signal":
            print("the signal is only checked for crypto Up/Down markets")
    if a.cmd in ("copysim", "all"):
        from .copysim import copysim
        from . import api
        from .export import ROOT
        d = ROOT / api.resolve_wallet(a.target)[0]
        r = copysim(d)
        for m in r.get("modes") or []:
            o = r["overall"][m]
            roi = "–" if o["roi"] is None else f"{o['roi'] * 100:+.1f}%"
            cp = "–" if o["copied_share"] is None else f"{o['copied_share'] * 100:.0f}%"
            print(f"   {r['labels'][m]:<20} return {roi:>8}   money copied {cp:>5}   positions {o['positions']}")
    if a.cmd in ("report", "all"):
        from .report import report
        from . import api
        from .export import ROOT
        d = ROOT / api.resolve_wallet(a.target)[0]
        print(f"report: {report(d)}")
        print((d / "summary.md").read_text())


if __name__ == "__main__":
    main()
