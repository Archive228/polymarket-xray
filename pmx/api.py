"""Clients for the public Polymarket APIs: Data API v2, Gamma, CLOB. No keys.

Data v2 – cursor pagination (pagination.next_cursor), limit up to 500, time windows via start/end.
Gamma /markets – several condition_ids as repeated parameters; closed markets only with closed=true.
CLOB /prices-history – the token price at a step of 1 minute and up.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

import requests

DATA = "https://data-api.polymarket.com/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# exports, cache and paper journals: PMX_DATA, otherwise data/ next to the code
STORE = Path(os.environ.get("PMX_DATA") or Path(__file__).resolve().parent.parent / "data")
CACHE = Path(os.environ.get("PMX_CACHE") or STORE / "_cache")

_session = requests.Session()
_session.headers.update({"User-Agent": "pmx/0.1 (+polymarket wallet analyzer)"})
_lock = threading.Lock()
_last: dict[str, float] = {}
MIN_GAP = {"data-api.polymarket.com": 0.04, "gamma-api.polymarket.com": 0.03, "clob.polymarket.com": 0.02}


def _throttle(host: str) -> None:
    gap = MIN_GAP.get(host, 0.05)
    with _lock:
        wait = _last.get(host, 0) + gap - time.time()
        _last[host] = max(time.time(), _last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def get(url: str, params=None, tries: int = 9):
    """GET with retries: 429/5xx and network errors – wait (Retry-After if present) with a doubling pause, up to a minute,
    so a rate limit hit while scouting many wallets at once waits it out instead of failing the wallet."""
    host = urllib.parse.urlparse(url).netloc
    status = None
    for a in range(tries):
        _throttle(host)
        pause = min(60.0, 1.5 * 2 ** a)
        try:
            r = _session.get(url, params=params, timeout=45)
        except requests.RequestException:
            time.sleep(pause)
            continue
        status = r.status_code
        if status == 200:
            return r.json()
        if status in (400, 404):
            return None
        time.sleep(float(r.headers.get("Retry-After") or pause))
    raise RuntimeError(f"request failed ({status}): {url} {params}")


def cached(name: str, fn):
    """Disk cache for immutable responses (closed markets, price history of closed windows)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / (hashlib.sha1(name.encode()).hexdigest() + ".json")
    if p.exists():
        return json.loads(p.read_text())
    v = fn()
    if v is not None:
        p.write_text(json.dumps(v))
    return v


# ---------- wallet ----------
ADDR = re.compile(r"0x[0-9a-fA-F]{40}")


def resolve_wallet(s: str) -> tuple[str, str]:
    """Address, profile link (polymarket.com/profile/0x… or /@name) or name → (address, name)."""
    s = s.strip()
    m = ADDR.search(s)
    if m:
        w = m.group(0).lower()
        return w, profile_name(w) or w
    name = re.sub(r".*polymarket\.com/(?:\w{2}/)?@?", "", s).split("?")[0].strip("/@ ")
    d = get(f"{GAMMA}/public-search", {"q": name, "search_profiles": "true", "limit_per_type": 10}) or {}
    for p in d.get("profiles") or []:
        if (p.get("name") or "").lower() == name.lower():
            return p["proxyWallet"].lower(), p.get("name")
    raise SystemExit(f"could not find a wallet '{s}' – pass a 0x… address or a profile link")


def profile_name(w: str) -> str | None:
    """Polymarket profile name by address (or pseudonym)."""
    try:
        d = get(f"{GAMMA}/public-profile", {"address": w}, tries=2) or {}
    except RuntimeError:
        return None
    return d.get("name") or d.get("pseudonym")


def paged(path: str, params: dict, max_pages: int = 100000):
    """All pages of a Data v2 cursor endpoint."""
    params = {**params, "limit": params.get("limit", 500)}
    cur = None
    for _ in range(max_pages):
        q = {**params, **({"cursor": cur} if cur else {})}
        d = get(f"{DATA}{path}", q) or {}
        items = d.get("data") or []
        if isinstance(items, dict):
            yield items
            return
        yield from items
        pg = d.get("pagination") or {}
        cur = pg.get("next_cursor")
        if not items or not pg.get("has_more") or not cur:
            return


def stats(w: str) -> dict:
    return (get(f"{DATA}/user-stats", {"user": w}) or {}).get("data") or {}


def pnl_series(w: str, interval: str = "max", fidelity: str = "1d") -> list:
    return ((get(f"{DATA}/user-pnl", {"user": w, "interval": interval, "fidelity": fidelity}) or {}).get("data") or {}).get("points") or []


def leaderboard_rank(w: str) -> dict:
    out = {}
    for per in ("day", "week", "month", "all"):
        d = (get(f"{DATA}/leaderboard", {"time_period": per, "user": w, "sort_by": "PNL"}) or {}).get("data")
        if isinstance(d, list):
            d = d[0] if d else None
        if d:
            out[per] = {"pnl": d.get("pnl"), "volume": d.get("volume"), "rank_pnl": d.get("rank_pnl"), "rank_volume": d.get("rank_volume")}
    return out


# ---------- markets ----------
def markets_by_condition(ids: list[str]) -> dict[str, dict]:
    """condition_id → Gamma market. We ask in batches for both open and closed (otherwise Gamma does not return closed ones)."""
    out: dict[str, dict] = {}
    todo = list(dict.fromkeys(i.lower() for i in ids if i))
    CACHE.mkdir(parents=True, exist_ok=True)
    mdir = CACHE / "markets"
    mdir.mkdir(exist_ok=True)
    need = []
    for c in todo:                       # a closed market no longer changes – take it from the cache
        p = mdir / f"{c}.json"
        if p.exists():
            out[c] = json.loads(p.read_text())
        else:
            need.append(c)
    failed: list[str] = []

    def fetch(chunk: list[str], depth: int = 0) -> None:
        """A batch of markets; if Gamma does not respond – pause, retry, then split the batch in half. A market that never answers is skipped."""
        for closed in ("true", "false"):
            q = [("condition_ids", c) for c in chunk] + [("closed", closed), ("limit", 100)]
            try:
                res = get(f"{GAMMA}/markets", q) or []
            except RuntimeError:
                if len(chunk) > 1:
                    time.sleep(10)
                    half = len(chunk) // 2
                    fetch(chunk[:half], depth + 1)
                    fetch(chunk[half:], depth + 1)
                else:
                    failed.extend(chunk)
                return
            for m in res:
                c = (m.get("conditionId") or "").lower()
                out[c] = m
                if m.get("closed"):
                    (mdir / f"{c}.json").write_text(json.dumps(m))

    for i in range(0, len(need), 40):
        fetch(need[i:i + 40])
    if failed:
        print(f"   Gamma did not return {len(failed)} markets – they go on without category and outcome; a re-run will fetch them")
    return out


def prices_history(token: str, start: int, end: int, fidelity: int = 1) -> list[dict]:
    """Price history of a token [start, end] at a step of fidelity minutes. Windows in the past are cached."""
    def fetch():
        return (get(f"{CLOB}/prices-history", {"market": token, "startTs": int(start), "endTs": int(end), "fidelity": fidelity}) or {}).get("history") or []
    if end < time.time() - 3600:
        return cached(f"ph:{token}:{int(start)}:{int(end)}:{fidelity}", fetch) or []
    return fetch()
