"""Paper broker on a fake order book: buy, selling the leader's share, filters, every risk limit, resolution, no signing."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmx.bot.broker import PaperBroker, Slippage, settle  # noqa: E402
from pmx.bot.ledger import Ledger  # noqa: E402
from pmx.bot.models import OrderBook, OrderBookLevel  # noqa: E402
from pmx.bot.rules import Filters, Risk  # noqa: E402
from pmx.bot.sizing import FIXED, SizingConfig  # noqa: E402

W, TOK, CID = "0xleader", "111", "0xmarket"
BOOK = OrderBook(bids=[OrderBookLevel(0.49, 500), OrderBookLevel(0.48, 500)], asks=[OrderBookLevel(0.51, 100), OrderBookLevel(0.52, 1000)])
INFO = {"accepting": True, "fee_rate": 0.0, "end_ts": time.time() + 3600, "category": "sports", "winner_index": None}


def make(tmp_path, book=BOOK, info=INFO, risk=None, filters=None, cash=1000.0, size=20.0):
    led = Ledger(tmp_path / "t.db", cash)
    sizing = SizingConfig(strategy=FIXED, copy_size=size, max_order_usd=100, min_order_usd=1)
    b = PaperBroker(led, sizing, filters or Filters(), risk or Risk(), Slippage(rel=0.05, abs=0.02), tmp_path,
                    lambda tok: (book, {"min_shares": 5}), lambda cid, max_age=300: info, log=lambda *a: None)
    return led, b


def trade(side, size, price, ts=None, n=0):
    return f"k{side}{n}{size}{price}", {"side": side, "token_id": TOK, "condition_id": CID, "price": price, "size": size,
                                        "timestamp": ts or time.time() - 5, "proxy_wallet": W, "outcome": "Yes", "outcome_index": 0, "title": "t"}


def test_buy_fills_within_slippage(tmp_path):
    led, b = make(tmp_path)
    assert b.handle(*trade("BUY", 100, 0.50)) == "bought"
    p = led.position(TOK)
    assert p["shares"] == pytest.approx(20 / 0.51, rel=1e-3)          # $20 at the best ask 0.51
    assert led.cash() == pytest.approx(980.0)


def test_buy_skipped_when_book_above_limit(tmp_path):
    book = OrderBook(bids=[OrderBookLevel(0.40, 100)], asks=[OrderBookLevel(0.60, 100)])
    led, b = make(tmp_path, book=book)
    assert b.handle(*trade("BUY", 100, 0.50)) == "skip"             # limit min(0.525, 0.52) = 0.52 < 0.60
    assert led.position(TOK) is None and led.cash() == 1000.0


def test_sell_follows_leader_fraction(tmp_path):
    led, b = make(tmp_path)
    led.move_leader_pos(W, TOK, 200)                                  # the leader had 200 shares
    b.handle(*trade("BUY", 100, 0.50, n=1))                           # +100 for the leader, we bought ~39
    ours = led.position(TOK)["shares"]
    assert b.handle(*trade("SELL", 150, 0.50, n=2)) == "sold"        # the leader sold 150 of 300 – half
    assert led.position(TOK)["shares"] == pytest.approx(ours / 2, rel=1e-6)


def test_leader_full_exit_closes_ours(tmp_path):
    led, b = make(tmp_path)
    b.handle(*trade("BUY", 100, 0.50, n=1))
    b.handle(*trade("SELL", 100, 0.50, n=2))
    p = led.position(TOK)
    assert p["shares"] == pytest.approx(0, abs=1e-6) and p["closed"] is not None


def test_old_signal_skipped_but_sell_still_copied(tmp_path):
    led, b = make(tmp_path, filters=Filters(max_age_sec=30))
    b.handle(*trade("BUY", 100, 0.50, n=1))
    assert b.handle(*trade("BUY", 100, 0.50, ts=time.time() - 120, n=2)) == "skip"
    assert b.handle(*trade("SELL", 100, 0.50, ts=time.time() - 120, n=3)) == "sold"


def test_price_corridor_and_time_to_end(tmp_path):
    led, b = make(tmp_path, filters=Filters(min_price=0.1, max_price=0.9))
    assert b.handle(*trade("BUY", 100, 0.95)) == "skip"
    led2, b2 = make(tmp_path / "x", info={**INFO, "end_ts": time.time() + 30})
    assert b2.handle(*trade("BUY", 100, 0.50)) == "skip"


@pytest.mark.parametrize("risk,cash,reason", [
    (Risk(max_daily_buy_usd=10), 1000, "daily buy limit"),
    (Risk(max_market_usd=10), 1000, "market limit"),
    (Risk(max_total_exposure_usd=10), 1000, "total exposure limit"),
])
def test_each_risk_limit_blocks(tmp_path, risk, cash, reason):
    led, b = make(tmp_path, risk=risk, cash=cash)
    assert b.handle(*trade("BUY", 100, 0.50)) == "skip"
    assert reason in led.db.execute("SELECT reason FROM decisions").fetchone()["reason"]


def test_open_positions_limit(tmp_path):
    led, b = make(tmp_path, risk=Risk(max_open_positions=1))
    b.handle(*trade("BUY", 100, 0.50, n=1))
    k, t = trade("BUY", 100, 0.50, n=2)
    t.update(token_id="222", condition_id="0xother")
    assert b.handle(k, t) == "skip"


def test_drawdown_limit(tmp_path):
    led, b = make(tmp_path, risk=Risk(max_drawdown_usd=50))
    led.db.execute("UPDATE account SET cash = 900")                   # lost $100 from a $1000 start
    assert b.handle(*trade("BUY", 100, 0.50)) == "skip"


def test_kill_switch(tmp_path):
    led, b = make(tmp_path)
    (tmp_path / "STOP").write_text("")
    assert b.handle(*trade("BUY", 100, 0.50)) == "skip"


def test_settle_pays_winner(tmp_path):
    led, b = make(tmp_path)
    b.handle(*trade("BUY", 100, 0.50))
    shares = led.position(TOK)["shares"]
    settle(led, lambda cid, max_age=60: {**INFO, "winner_index": 0}, log=lambda *a: None)
    assert led.cash() == pytest.approx(980 + shares) and led.position(TOK)["payout"] == pytest.approx(shares)


def test_paper_never_loads_signing():
    code = ("import sys; sys.path.insert(0, %r); import pmx.bot.__main__, pmx.bot.broker, pmx.bot.feed; "
            "bad = [m for m in sys.modules if any(x in m for x in ('py_clob_client', 'eth_account', 'web3', 'ethers'))]; print(bad)") % str(Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True).stdout.strip()
    assert out == "[]"


def test_live_refuses():
    r = subprocess.run([sys.executable, "-W", "ignore", "-m", "pmx.bot", "live"], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert r.returncode != 0 and "not implemented" in r.stderr
