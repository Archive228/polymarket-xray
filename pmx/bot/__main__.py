"""pmx copy bot. Paper mode only: real trading is not implemented in this code and is enabled only by the owner's decision.

python3 -m pmx.bot paper --config bot.json [--minutes 30]   – copy on a paper account against live order books
python3 -m pmx.bot report --config bot.json                  – report from the log
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..api import STORE
from . import market
from .broker import PaperBroker, Slippage, settle
from .feed import Feed
from .ledger import Ledger
from .report import summary, text
from .rules import Filters, Risk
from .sizing import SizingConfig, parse_tiers, validate

ROOT = Path(__file__).resolve().parent.parent.parent


def load(path: Path) -> dict:
    c = json.loads(path.read_text())
    if c.get("mode", "paper") != "paper":
        sys.exit("config mode ≠ paper: real trading is not implemented in pmx and is enabled only by the owner's decision")
    s = c.get("sizing") or {}
    tiers = s.pop("tiered_multipliers", "")
    sizing = SizingConfig(**s, tiered_multipliers=parse_tiers(tiers) if isinstance(tiers, str) else [])
    errs = validate(sizing)
    if errs:
        sys.exit("sizing error: " + "; ".join(errs))
    db = STORE / "bot" / f"{c.get('name', 'paper')}.db"
    return {"cfg": c, "sizing": sizing, "filters": Filters(**(c.get("filters") or {})), "risk": Risk(**(c.get("risk") or {})),
            "slip": Slippage(**(c.get("slippage") or {})), "db": db}


def main() -> None:
    p = argparse.ArgumentParser(prog="pmx.bot", description="Polymarket copy bot – paper mode")
    sub = p.add_subparsers(dest="cmd", required=True)
    for n in ("paper", "report", "live"):
        sp = sub.add_parser(n)
        sp.add_argument("--config", default=str(ROOT / "bot.json"))
        sp.add_argument("--minutes", type=float, default=None)
    a = p.parse_args()
    if a.cmd == "live":
        sys.exit("real trading is not implemented: it is enabled only by the owner's decision, with a separate wallet and keys in .env")
    L = load(Path(a.config))
    led = Ledger(L["db"], float(L["cfg"].get("start_cash", 1000)))
    if a.cmd == "report":
        print(text(summary(led, market.get_book)))
        return
    broker = PaperBroker(led, L["sizing"], L["filters"], L["risk"], L["slip"], L["db"].parent, market.get_book, market.market_info)
    feeds = [Feed(w, led) for w in L["cfg"]["leaders"]]
    for f in feeds:
        f.prime()
    poll, stop = float(L["cfg"].get("poll_sec", 3)), time.time() + (a.minutes * 60 if a.minutes else 1e12)
    last_settle = 0.0
    print(f"paper: log {L['db']}, polling every {poll:g} s" + (f", {a.minutes:g} min" if a.minutes else "") + ". Stop with Ctrl+C or a STOP file next to the log.")
    try:
        while time.time() < stop:
            for f in feeds:
                try:
                    for k, t in f.poll():
                        lag = t["seen_ts"] - t["timestamp"]
                        act = broker.handle(k, t)
                        print(f"{time.strftime('%H:%M:%S')} {t['side']:4} {float(t['size']):>8.1f} × {float(t['price']):.3f} {str(t.get('outcome')):<5} "
                              f"lag {lag:4.0f} s → {act}")
                except Exception as e:                      # a network failure – retry on the next round, not a stop
                    print(f"   round failed: {e}")
            if time.time() - last_settle > 120:
                settle(led, market.market_info)
                last_settle = time.time()
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    settle(led, market.market_info)
    print("\n" + text(summary(led, market.get_book)))


if __name__ == "__main__":
    main()
