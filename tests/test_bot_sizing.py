"""Tests of the size calculation: a port of src/config/__tests__/copyStrategy.test.ts from shmlkv/polymarket-copy-trading-bot @ f11b2d3
(ISC, Copyright (c) 2024, Polymarket Copy Trading Bot Contributors) – the same inputs and expectations. Below – tests of the pmx fixes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmx.bot.sizing import (ADAPTIVE, FIXED, PERCENTAGE, SizingConfig, Tier, calculate_order_size, parse_tiers,  # noqa: E402
                            trade_multiplier, validate)

BASE = dict(strategy=PERCENTAGE, copy_size=10.0, max_order_usd=100.0, min_order_usd=1.0)


def cfg(**kw):
    return SizingConfig(**{**BASE, **kw})


# --- calculateOrderSize: PERCENTAGE
def test_percentage_correct():
    r = calculate_order_size(cfg(), 100, 1000, 0)
    assert r.final_amount == 10 and r.strategy == PERCENTAGE and not r.below_minimum


def test_percentage_cap_max():
    r = calculate_order_size(cfg(), 2000, 10000, 0)
    assert r.final_amount == 100 and r.capped_by_max


def test_percentage_below_minimum():
    r = calculate_order_size(cfg(), 5, 1000, 0)
    assert r.final_amount == 0 and r.below_minimum


def test_percentage_reduce_to_balance():
    r = calculate_order_size(cfg(), 100, 5, 0)
    assert r.final_amount <= 5 * 0.99 and r.reduced_by_balance


# --- FIXED
FIXED_CFG = dict(strategy=FIXED, copy_size=50.0, max_order_usd=100.0, min_order_usd=1.0)


def test_fixed_amount():
    r = calculate_order_size(SizingConfig(**FIXED_CFG), 1000, 10000, 0)
    assert r.final_amount == 50 and r.strategy == FIXED


def test_fixed_cap_max():
    r = calculate_order_size(SizingConfig(**{**FIXED_CFG, "copy_size": 200.0}), 1000, 10000, 0)
    assert r.final_amount == 100 and r.capped_by_max


# --- position limit
def test_position_within_limit():
    assert calculate_order_size(cfg(max_position_usd=50.0), 100, 1000, 40).final_amount == 10


def test_position_reduce_to_limit():
    assert calculate_order_size(cfg(max_position_usd=50.0), 100, 1000, 45).final_amount <= 5


# --- validateCopyStrategyConfig
def test_validate_ok():
    assert validate(cfg()) == []


def test_validate_negative_copy_size():
    assert validate(cfg(copy_size=-5.0))


def test_validate_percentage_over_100():
    assert any("copySize" in e for e in validate(cfg(copy_size=150.0)))


def test_validate_min_over_max():
    assert any("minOrderSizeUSD" in e for e in validate(cfg(max_order_usd=10.0, min_order_usd=20.0)))


# --- parseTieredMultipliers
def test_parse_valid():
    t = parse_tiers("1-10:2.0,10-100:1.0,100-500:0.2,500+:0.1")
    assert t == [Tier(1, 10, 2.0), Tier(10, 100, 1.0), Tier(100, 500, 0.2), Tier(500, None, 0.1)]


def test_parse_infinite():
    assert parse_tiers("1000+:0.001") == [Tier(1000, None, 0.001)]


def test_parse_empty():
    assert parse_tiers("") == []


def test_parse_invalid_format():
    with pytest.raises(ValueError, match="Invalid tier format"):
        parse_tiers("invalid")
    with pytest.raises(ValueError, match="Invalid range format"):
        parse_tiers("100:0.5")
    with pytest.raises(ValueError, match="Invalid maximum value"):
        parse_tiers("100-50:0.5")


def test_parse_overlap():
    with pytest.raises(ValueError, match="Overlapping tiers"):
        parse_tiers("1-100:2.0,50-200:1.0")


def test_parse_infinite_not_last():
    with pytest.raises(ValueError, match="infinite upper bound must be last"):
        parse_tiers("1-100:2.0,100+:1.0,200-300:0.5")


def test_parse_invalid_multiplier():
    with pytest.raises(ValueError, match="Invalid multiplier"):
        parse_tiers("100-200:abc")
    with pytest.raises(ValueError, match="Invalid multiplier"):
        parse_tiers("100-200:-1")


# --- getTradeMultiplier
TIERED = cfg(max_order_usd=1000.0, tiered_multipliers=[Tier(1, 10, 2.0), Tier(10, 100, 1.0), Tier(100, 500, 0.2), Tier(500, 1000, 0.1),
                                                       Tier(1000, 5000, 0.02), Tier(5000, None, 0.001)])


def test_multiplier_small():
    assert trade_multiplier(TIERED, 5) == 2.0 and trade_multiplier(TIERED, 9.99) == 2.0


def test_multiplier_medium():
    assert [trade_multiplier(TIERED, x) for x in (10, 50, 100, 250)] == [1.0, 1.0, 0.2, 0.2]


def test_multiplier_large():
    assert [trade_multiplier(TIERED, x) for x in (500, 1000, 5000, 100000)] == [0.1, 0.02, 0.001, 0.001]


def test_multiplier_none():
    assert trade_multiplier(cfg(), 100) == 1.0


def test_multiplier_single():
    assert trade_multiplier(cfg(trade_multiplier=2.5), 100) == 2.5


def test_multiplier_prefers_tiers():
    assert trade_multiplier(SizingConfig(**{**TIERED.__dict__, "trade_multiplier": 5.0}), 5) == 2.0


# --- calculateOrderSize with tiers
T2 = cfg(max_order_usd=1000.0, tiered_multipliers=[Tier(1, 10, 2.0), Tier(10, 100, 1.0), Tier(100, 1000, 0.2), Tier(1000, None, 0.01)])


def test_tiers_small():
    r = calculate_order_size(T2, 5, 1000, 0)
    assert r.base_amount == 0.5 and r.final_amount == 1.0


def test_tiers_medium():
    assert calculate_order_size(T2, 50, 1000, 0).final_amount == 5.0


def test_tiers_large():
    assert calculate_order_size(T2, 500, 1000, 0).final_amount == 10.0


def test_tiers_very_large():
    assert calculate_order_size(T2, 250000, 10000, 0).final_amount == 250.0


def test_tiers_reasoning():
    assert "2x multiplier" in calculate_order_size(T2, 5, 1000, 0).reasoning


def test_tiers_fixed():
    c = SizingConfig(strategy=FIXED, copy_size=50.0, max_order_usd=1000.0, min_order_usd=1.0,
                     tiered_multipliers=[Tier(1, 1000, 2.0), Tier(1000, None, 0.1)])
    assert calculate_order_size(c, 100, 1000, 0).final_amount == 100.0
    assert calculate_order_size(c, 10000, 1000, 0).final_amount == 5.0


# --- pmx fixes
def test_pmx_gap_between_tiers_rejected():
    with pytest.raises(ValueError, match="Gap between tiers"):
        parse_tiers("1-10:2.0,20-100:1.0")


def test_pmx_below_first_tier_uses_first():
    assert trade_multiplier(TIERED, 0.5) == 2.0          # the author had 0.001 – the multiplier for trades from $5000


def test_pmx_adaptive_reaches_min_at_2x_threshold():
    c = SizingConfig(strategy=ADAPTIVE, copy_size=10.0, max_order_usd=1e9, min_order_usd=0.01,
                     adaptive_min_percent=5.0, adaptive_max_percent=15.0, adaptive_threshold=300.0)
    assert calculate_order_size(c, 600, 1e9).final_amount == pytest.approx(600 * 0.05)
    assert calculate_order_size(c, 300, 1e9).final_amount == pytest.approx(300 * 0.10)
    assert calculate_order_size(c, 150, 1e9).final_amount == pytest.approx(150 * 0.125)
