"""The bot's SQLite log: the leader's trades (with the lag, when we saw them), decisions (what we did and why),
fills, our positions, the account. One transaction per account and position change – modeled on db.atomic() from
agent-next/polymarket-paper-trader (MIT)."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK (id = 1), start_cash REAL, cash REAL, created REAL);
CREATE TABLE IF NOT EXISTS leader_trades(key TEXT PRIMARY KEY, wallet TEXT, ts INTEGER, seen_ts REAL, lag REAL, side TEXT, token TEXT,
    cid TEXT, outcome TEXT, outcome_index INTEGER, price REAL, size REAL, title TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, ts REAL, action TEXT, reason TEXT, want_usd REAL,
    want_shares REAL, best_bid REAL, best_ask REAL, mid REAL, leader_price REAL, limit_price REAL);
CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER, ts REAL, side TEXT, token TEXT, cid TEXT,
    outcome TEXT, shares REAL, avg_price REAL, cost REAL, fee REAL, slippage_bps REAL, levels INTEGER, partial INTEGER, leader_price REAL);
CREATE TABLE IF NOT EXISTS positions(token TEXT PRIMARY KEY, cid TEXT, outcome TEXT, outcome_index INTEGER, title TEXT, shares REAL,
    cost REAL, opened REAL, closed REAL, payout REAL);
CREATE TABLE IF NOT EXISTS leader_pos(wallet TEXT, token TEXT, shares REAL, PRIMARY KEY (wallet, token));
"""


class Ledger:
    def __init__(self, path: Path, start_cash: float = 1000.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if not self.db.execute("SELECT 1 FROM account").fetchone():
            self.db.execute("INSERT INTO account VALUES (1, ?, ?, ?)", (start_cash, start_cash, time.time()))

    @contextmanager
    def atomic(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # --- the leader's trades
    def seen(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM leader_trades WHERE key = ?", (key,)).fetchone() is not None

    def add_leader_trade(self, key: str, r: dict, status: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO leader_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, r.get("proxy_wallet"), r["timestamp"], r["seen_ts"], r["seen_ts"] - r["timestamp"], r["side"], str(r["token_id"]),
                         (r.get("condition_id") or "").lower(), r.get("outcome"), r.get("outcome_index"), float(r["price"]), float(r["size"]),
                         r.get("title"), status))

    def leader_pos(self, wallet: str, token: str) -> float:
        row = self.db.execute("SELECT shares FROM leader_pos WHERE wallet = ? AND token = ?", (wallet, token)).fetchone()
        return row["shares"] if row else 0.0

    def move_leader_pos(self, wallet: str, token: str, delta: float) -> None:
        self.db.execute("INSERT INTO leader_pos VALUES (?,?,?) ON CONFLICT(wallet, token) DO UPDATE SET shares = max(0, shares + ?)",
                        (wallet, token, max(delta, 0.0), delta))

    # --- account and positions
    def cash(self) -> float:
        return self.db.execute("SELECT cash FROM account").fetchone()["cash"]

    def start_cash(self) -> float:
        return self.db.execute("SELECT start_cash FROM account").fetchone()["start_cash"]

    def position(self, token: str):
        return self.db.execute("SELECT * FROM positions WHERE token = ?", (token,)).fetchone()

    def open_positions(self) -> list:
        return self.db.execute("SELECT * FROM positions WHERE shares > 1e-9 AND closed IS NULL").fetchall()

    def decide(self, key: str, action: str, reason: str, **kw) -> int:
        cols = ("want_usd", "want_shares", "best_bid", "best_ask", "mid", "leader_price", "limit_price")
        cur = self.db.execute(f"INSERT INTO decisions (key, ts, action, reason, {', '.join(cols)}) VALUES (?,?,?,?,{','.join('?' * len(cols))})",
                              (key, time.time(), action, reason, *[kw.get(c) for c in cols]))
        return cur.lastrowid

    def bought_usd_today(self) -> float:
        day = time.time() - time.time() % 86400
        return self.db.execute("SELECT COALESCE(SUM(cost + fee), 0) s FROM fills WHERE side = 'BUY' AND ts >= ?", (day,)).fetchone()["s"]
