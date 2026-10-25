"""Tests of the Dennim89/polymarket-wallet-analyzer port: the same synthetic wallets and expectations as in
analytics/style_test.go and analytics/carry_test.go @ 4c169fb (MIT, Copyright (c) 2026 Denys).
Run: python3 -m pytest tests/ or python3 tests/test_style_port.py"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmx.style import BOT_ARCH, bot_archetype, bot_score, classify_archetype, detect_carry  # noqa: E402

ZERO = {"total_trades": 0, "unique_markets": 0, "trades_per_market": 0.0, "median_inter_trade_secs": 0.0, "hours_active_per_day": 0.0,
        "both_sides_ratio": 0.0, "median_hold_secs": 0.0, "size_coef_var": 0.0, "avg_position_size_usd": 0.0, "max_position_size_usd": 0.0,
        "total_cost_usd": 0.0, "active_days": 0, "span_days": 0}


def sig(**kw):
    return {**ZERO, **kw}


def arch(s):
    return classify_archetype(s, bot_score(s)[0])


def test_bot_score_threshold():
    three = sig(total_trades=100, both_sides_ratio=0.50, median_inter_trade_secs=10, hours_active_per_day=20)
    assert bot_score(three)[0] < 0.65
    assert bot_score({**three, "size_coef_var": 0.20})[0] >= 0.65
    diversified = sig(total_trades=12000, unique_markets=600, trades_per_market=20, median_inter_trade_secs=3, hours_active_per_day=21,
                      both_sides_ratio=0.30)
    assert bot_score(diversified)[0] >= 0.65
    persistent = sig(total_trades=20000, unique_markets=350, trades_per_market=57, median_inter_trade_secs=4, hours_active_per_day=15,
                     both_sides_ratio=0.33, active_days=80, span_days=84)
    assert bot_score(persistent)[0] >= 0.65
    assert bot_score({**persistent, "active_days": 40})[0] < 0.65
    assert bot_score(sig(total_trades=29, both_sides_ratio=1, median_inter_trade_secs=1, hours_active_per_day=24, size_coef_var=0.1))[0] == 0


def test_classify_archetype():
    assert arch(sig(total_trades=100, unique_markets=3, avg_position_size_usd=5_000_000, total_cost_usd=200_000_000,
                    median_hold_secs=100_000)) in BOT_ARCH
    assert arch(sig(total_trades=100, unique_markets=3, avg_position_size_usd=200_000, both_sides_ratio=0.10, median_hold_secs=100_000,
                    total_cost_usd=600_000)) == "whale_concentrated"
    assert arch(sig(total_trades=15000, unique_markets=14, trades_per_market=1000, both_sides_ratio=0.50, avg_position_size_usd=1_300_000,
                    total_cost_usd=18_000_000)) == "whale_concentrated"
    assert arch(sig(total_trades=15000, unique_markets=14, trades_per_market=1000, both_sides_ratio=0.50, avg_position_size_usd=20_000,
                    total_cost_usd=280_000)) in BOT_ARCH
    conviction = sig(total_trades=10000, unique_markets=400, trades_per_market=25, median_inter_trade_secs=2, both_sides_ratio=0.09,
                     hours_active_per_day=15, active_days=28, span_days=28, avg_position_size_usd=5_000, max_position_size_usd=100_000,
                     total_cost_usd=500_000)
    assert arch(conviction) not in BOT_ARCH
    assert arch({**conviction, "max_position_size_usd": 8_000, "avg_position_size_usd": 3_000}) in BOT_ARCH
    assert arch(sig(total_trades=6000, unique_markets=280, trades_per_market=22, median_inter_trade_secs=46, hours_active_per_day=20,
                    both_sides_ratio=0.01, size_coef_var=2.8, median_hold_secs=120_000, avg_position_size_usd=3_500,
                    max_position_size_usd=13_000, total_cost_usd=900_000, active_days=23, span_days=22)) not in BOT_ARCH
    assert arch(sig(total_trades=5000, unique_markets=1, trades_per_market=5000, both_sides_ratio=0.0, avg_position_size_usd=5_000_000,
                    median_hold_secs=50_000, total_cost_usd=5_000_000)) == "whale_concentrated"
    assert arch(sig(total_trades=12000, unique_markets=110, trades_per_market=100, both_sides_ratio=0.96)) in BOT_ARCH
    assert arch(sig(total_trades=60, unique_markets=2, trades_per_market=30, both_sides_ratio=1.0, total_cost_usd=8000)) not in BOT_ARCH
    assert classify_archetype(sig(total_trades=29), 0) == "insufficient_data"


def test_bot_archetype_subtypes():
    assert bot_archetype(sig(unique_markets=600, both_sides_ratio=0.45, median_inter_trade_secs=5)) == "algo_basket_mm"
    assert bot_archetype(sig(unique_markets=40, both_sides_ratio=0.60, median_hold_secs=120)) == "algo_arbitrage"
    assert bot_archetype(sig(unique_markets=40, both_sides_ratio=0.45, median_hold_secs=4000)) == "algo_market_maker"
    assert bot_archetype(sig(unique_markets=40, both_sides_ratio=0.10)) == "algo_hft"


def _flats(n, wins, entry):
    return pd.DataFrame({"entry": [entry] * n, "buy_cost": [100 * entry] * n, "won": [i < wins for i in range(n)]})


def _buys(n, price, extra=None):
    rows = [{"side": "BUY", "size": 100, "price": price}] * n + (extra or [])
    return pd.DataFrame(rows)


def test_detect_carry():
    hold = detect_carry(_flats(40, 38, 0.95), _buys(40, 0.95), 0.02)
    assert hold["is_carry"] and hold["type"] == "carry-hold"
    flip = detect_carry(_flats(40, 38, 0.95), _buys(40, 0.95, [{"side": "SELL", "size": 3800, "price": 0.99}]), 0.02)
    assert flip["is_carry"] and flip["type"] == "carry-flip"
    assert not detect_carry(_flats(40, 20, 0.95), _buys(40, 0.95), 0.02)["is_carry"]
    assert not detect_carry(_flats(40, 30, 0.50), _buys(40, 0.50), 0.05)["is_carry"]
    assert not detect_carry(_flats(40, 38, 0.95), _buys(40, 0.95), 0.85)["is_carry"]
    assert not detect_carry(_flats(20, 20, 0.95), _buys(20, 0.95), 0.0)["is_carry"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
