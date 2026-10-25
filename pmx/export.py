"""Full wallet export to <PMX_DATA or data>/wallets/<address>/: trades (taker and maker), activity, positions of all statuses,
the PnL curve, stats, leaderboard rank, all markets. A repeat run downloads only what is new."""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import api
from .markets import flat

ROOT = api.STORE / "wallets"
STATUSES = ("OPEN", "REDEEMABLE", "REDEEMABLE_LOST", "MERGEABLE", "CLOSED")


def _windows(start: int, end: int, step: int) -> list[tuple[int, int]]:
    # start and end in Data v2 are both inclusive, so the windows join without a shared second – otherwise duplicates at the seam
    out, t = [], start
    while t < end:
        out.append((t, min(t + step - 1, end)))
        t += step
    return out


def dedup_activity(df: pd.DataFrame) -> pd.DataFrame:
    """Data v2 returns REDEEM, rebates and rewards as pairs of identical rows – we remove the exact copies.
    TRADE rows are left alone: two identical fills in one transaction do happen for real."""
    if not len(df) or "type" not in df.columns:
        return df
    nt = df["type"] != "TRADE"
    return pd.concat([df[~nt], df[nt].drop_duplicates()]).sort_values("timestamp", kind="stable")


def markets_table(ids) -> pd.DataFrame:
    mk = api.markets_by_condition(sorted({i.lower() for i in ids if isinstance(i, str)}))
    return pd.DataFrame([flat(m) for m in mk.values()])


def rebuild_markets(d: Path) -> pd.DataFrame:
    """Rebuild markets.csv from the already exported trades (closed markets come from the cache, no network)."""
    ids = set()
    for n in ("trades", "activity", "positions"):
        p = d / f"{n}.csv"
        if p.exists():
            ids |= set(pd.read_csv(p, usecols=lambda c: c == "condition_id")["condition_id"].dropna().str.lower())
    markets = markets_table(ids)
    if len(markets):
        markets.to_csv(d / "markets.csv", index=False)
    return markets


def _fetch_windowed(path: str, wallet: str, start: int, end: int, extra: dict, parts: Path | None = None, workers: int = 6,
                    log=print) -> list[dict]:
    """Cut the history into one-day windows and download them in parallel. Each closed window is written to disk in parts/ right away:
    an interrupted export resumes where it stopped instead of starting over."""
    wins = _windows(start, end, 86400)
    rows: list[dict] = []
    done = [0]
    tag = path.strip("/") + "".join(f"_{k}-{v}" for k, v in sorted(extra.items()))
    if parts:
        parts.mkdir(parents=True, exist_ok=True)

    def one(w):
        f = parts / f"{tag}_{w[0]}.json" if parts else None
        if f and f.exists():
            r = json.loads(f.read_text())
        else:
            r = list(api.paged(path, {"user": wallet, "start": w[0], "end": w[1], **extra}))
            if f and w[1] < time.time() - 3600:          # a window in the past no longer changes
                f.write_text(json.dumps(r))
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == len(wins):
            log(f"   {path}: windows {done[0]}/{len(wins)}")
        return r

    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, wins):
            rows += r
    return rows


def _key(t: dict) -> tuple:
    return (t.get("transaction_hash"), t.get("token_id"), t.get("side"), t.get("price"), t.get("size"), t.get("timestamp"))


def export(target: str, days: int | None = None, log=print) -> Path:
    """days – export only the last N days (for huge wallets: the recent period describes the strategy)."""
    wallet, name = api.resolve_wallet(target)
    out = ROOT / wallet
    out.mkdir(parents=True, exist_ok=True)
    meta_p = out / "meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    now = int(time.time())
    log(f"wallet {wallet} ({name})")

    st = api.stats(wallet)
    join = int(st.get("join_date") or now - 3 * 365 * 86400)
    since = max(join - 86400, int(meta.get("last_ts", 0)) - 6 * 3600) if meta.get("last_ts") else join - 86400
    if days:
        since = max(since, now - days * 86400)
        meta["days"] = days
    parts = out / "_parts"

    # trades: taker_only=false – all fills (both maker and taker); taker_only=true – taker fills only
    log("trades…")
    all_fills = _fetch_windowed("/trades", wallet, since, now + 60, {"taker_only": "false"}, parts, log=log)
    taker = {_key(t) for t in _fetch_windowed("/trades", wallet, since, now + 60, {"taker_only": "true"}, parts, log=log)}
    for t in all_fills:
        t["is_maker"] = _key(t) not in taker
    trades = pd.DataFrame(all_fills)
    log("activity…")
    activity = pd.DataFrame(_fetch_windowed("/activity", wallet, since, now + 60, {}, parts, log=log))

    # top-up: from the previous export take everything before since, and from since a fresh copy (so the overlap yields no duplicates)
    for nm, df in (("trades", trades), ("activity", activity)):
        p = out / f"{nm}.csv"
        if p.exists():
            old = pd.read_csv(p, low_memory=False)
            df = pd.concat([old[old["timestamp"] < since], df], ignore_index=True) if len(df) else old
        if len(df):
            df = df.sort_values("timestamp", kind="stable")
            if nm == "activity":
                df = dedup_activity(df)
            df.to_csv(p, index=False)
        if nm == "trades":
            trades = df
        else:
            activity = df

    log("positions of all statuses…")
    pos = []
    for s in STATUSES:
        pos += list(api.paged("/positions", {"user": wallet, "status": s}))
    positions = pd.DataFrame(pos)
    if len(positions):
        positions.to_csv(out / "positions.csv", index=False)

    log("PnL curve, stats, leaderboard…")
    pd.DataFrame(api.pnl_series(wallet)).to_csv(out / "pnl.csv", index=False)
    (out / "stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    (out / "leaderboard.json").write_text(json.dumps(api.leaderboard_rank(wallet), ensure_ascii=False, indent=1))

    log("markets…")
    ids = set()
    for df in (trades, activity, positions):
        if len(df) and "condition_id" in df.columns:
            ids |= set(df["condition_id"].dropna().str.lower())
    markets = markets_table(ids)
    if len(markets):
        markets.to_csv(out / "markets.csv", index=False)

    official = (st.get("all_time_pnl") or {}).get("trade_count")
    meta.update({"wallet": wallet, "name": name, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "last_ts": int(trades["timestamp"].max()) if len(trades) else now,
                 "counts": {"trades": len(trades), "activity": len(activity), "positions": len(positions), "markets": len(markets)},
                 # completeness check: how many trades Polymarket itself counted vs how many were exported
                 "official_trade_count": official})
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    shutil.rmtree(parts, ignore_errors=True)             # export assembled – the parts are no longer needed
    log(f"done: {meta['counts']} → {out}")
    return out
