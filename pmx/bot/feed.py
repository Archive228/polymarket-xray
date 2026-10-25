"""The leader's trade feed: polling Data API v2 /trades with overlapping windows, duplicates by the pmx key (tx, token, side, price,
size, time). A lag is recorded for each trade: how many seconds passed from his fill to the moment
we saw it (shmlkv did not measure the lag and caught duplicates only by the transaction hash)."""
from __future__ import annotations

import time

from .. import api
from ..export import _key
from .ledger import Ledger


class Feed:
    def __init__(self, wallet: str, ledger: Ledger, overlap: int = 180, log=print):
        self.w, self.l, self.overlap, self.log = wallet.lower(), ledger, overlap, log
        self.last_ts = int(time.time()) - overlap

    def prime(self) -> int:
        """Start: his open positions go into the leader's position tracking, recent trades go into the history (not copied)."""
        n = 0
        for p in api.paged("/positions", {"user": self.w, "status": "OPEN"}):
            if float(p.get("current_size") or 0) > 0 and self.l.leader_pos(self.w, str(p["token_id"])) == 0:
                self.l.move_leader_pos(self.w, str(p["token_id"]), float(p["current_size"]))
                n += 1
        old = self.poll(status="history")
        self.log(f"leader {self.w}: open positions {n}, recent trades in the history {len(old)}")
        return n

    def poll(self, status: str = "signal") -> list[tuple[str, dict]]:
        now = int(time.time())
        rows = list(api.paged("/trades", {"user": self.w, "start": self.last_ts - self.overlap, "end": now + 60, "taker_only": "false"}))
        seen_ts = time.time()
        out = []
        for r in sorted(rows, key=lambda x: x["timestamp"]):
            k = "|".join(str(x) for x in _key(r))
            if self.l.seen(k):
                continue
            r["seen_ts"] = seen_ts
            r.setdefault("proxy_wallet", self.w)
            self.l.add_leader_trade(k, r, status)
            if status == "history":                     # past trades move the leader's position but are not copied
                self.l.move_leader_pos(self.w, str(r["token_id"]), float(r["size"]) if r["side"] == "BUY" else -float(r["size"]))
            out.append((k, r))
        if rows:
            self.last_ts = max(self.last_ts, max(int(x["timestamp"]) for x in rows))
        return out
