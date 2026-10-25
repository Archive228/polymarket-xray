"""Copy size: how many dollars to bet on a leader's trade.

Ported from TypeScript 1-to-1 from shmlkv/polymarket-copy-trading-bot @ f11b2d3, src/config/copyStrategy.ts
(ISC, Copyright (c) 2024, Polymarket Copy Trading Bot Contributors; the license is in THIRD_PARTY_LICENSES/).
Three strategies: PERCENTAGE – a percent of the leader's amount, FIXED – a fixed amount, ADAPTIVE – the smaller the leader's trade,
the larger the percent. On top – a multiplier by the leader's trade size (tiers) and caps: order, position, balance, minimum.

pmx: fixes for what the review of the original found:
- gaps between tiers are now a configuration error (the author promised the check in a comment but never wrote it),
  and a trade below the first tier gets the first tier's multiplier, not the last one's;
- the ADAPTIVE comment is corrected to match the actual formula (minPercent is reached at 2× the threshold, not at 10×).
The tier multiplier applies to a buy only; a sell is computed as a share of the position (broker), without a multiplier."""
from __future__ import annotations

from dataclasses import dataclass, field

PERCENTAGE, FIXED, ADAPTIVE = "PERCENTAGE", "FIXED", "ADAPTIVE"


@dataclass
class Tier:
    min: float
    max: float | None          # None – no upper bound
    multiplier: float


@dataclass
class SizingConfig:
    strategy: str
    copy_size: float
    max_order_usd: float
    min_order_usd: float
    adaptive_min_percent: float | None = None
    adaptive_max_percent: float | None = None
    adaptive_threshold: float | None = None
    tiered_multipliers: list[Tier] = field(default_factory=list)
    trade_multiplier: float | None = None
    max_position_usd: float | None = None
    max_daily_volume_usd: float | None = None


@dataclass
class OrderSize:
    trader_order_usd: float
    base_amount: float
    final_amount: float
    strategy: str
    capped_by_max: bool
    reduced_by_balance: bool
    below_minimum: bool
    reasoning: str


def calculate_order_size(cfg: SizingConfig, trader_order_usd: float, available_balance: float, current_position_usd: float = 0.0) -> OrderSize:
    # step 1: the base by strategy
    if cfg.strategy == PERCENTAGE:
        base = trader_order_usd * (cfg.copy_size / 100)
        why = f"{cfg.copy_size}% of trader's ${trader_order_usd:.2f} = ${base:.2f}"
    elif cfg.strategy == FIXED:
        base = cfg.copy_size
        why = f"Fixed amount: ${base:.2f}"
    elif cfg.strategy == ADAPTIVE:
        pct = _adaptive_percent(cfg, trader_order_usd)
        base = trader_order_usd * (pct / 100)
        why = f"Adaptive {pct:.1f}% of trader's ${trader_order_usd:.2f} = ${base:.2f}"
    else:
        raise ValueError(f"Unknown strategy: {cfg.strategy}")
    # step 1.5: multiplier by the leader's trade size
    mult = trade_multiplier(cfg, trader_order_usd)
    final = base * mult
    if mult != 1.0:
        why += f" → {mult:g}x multiplier: ${base:.2f} → ${final:.2f}"
    capped = reduced = below = False
    # step 2: order cap
    if final > cfg.max_order_usd:
        final, capped = cfg.max_order_usd, True
        why += f" → Capped at max ${cfg.max_order_usd:g}"
    # step 3: position cap
    if cfg.max_position_usd:
        if current_position_usd + final > cfg.max_position_usd:
            allowed = max(0.0, cfg.max_position_usd - current_position_usd)
            if allowed < cfg.min_order_usd:
                final = 0.0
                why += " → Position limit reached"
            else:
                final = allowed
                why += " → Reduced to fit position limit"
    # step 4: balance with a 1% reserve
    affordable = available_balance * 0.99
    if final > affordable:
        final, reduced = affordable, True
        why += f" → Reduced to fit balance (${affordable:.2f})"
    # step 5: minimum
    if final < cfg.min_order_usd:
        below = True
        why += f" → Below minimum ${cfg.min_order_usd:g}"
        final = 0.0
    return OrderSize(trader_order_usd, base, final, cfg.strategy, capped, reduced, below, why)


def _adaptive_percent(cfg: SizingConfig, trader_order_usd: float) -> float:
    lo = cfg.adaptive_min_percent if cfg.adaptive_min_percent is not None else cfg.copy_size
    hi = cfg.adaptive_max_percent if cfg.adaptive_max_percent is not None else cfg.copy_size
    threshold = cfg.adaptive_threshold if cfg.adaptive_threshold is not None else 500
    if trader_order_usd >= threshold:
        # a large trade: from copy_size at the threshold to minPercent at 2× the threshold and above
        return _lerp(cfg.copy_size, lo, min(1.0, trader_order_usd / threshold - 1))
    # a small one: from maxPercent at $0 to copy_size at the threshold
    return _lerp(hi, cfg.copy_size, trader_order_usd / threshold)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def validate(cfg: SizingConfig) -> list[str]:
    errors = []
    if cfg.copy_size <= 0:
        errors.append("copySize must be positive")
    if cfg.strategy == PERCENTAGE and cfg.copy_size > 100:
        errors.append("copySize for PERCENTAGE strategy should be <= 100")
    if cfg.max_order_usd <= 0:
        errors.append("maxOrderSizeUSD must be positive")
    if cfg.min_order_usd <= 0:
        errors.append("minOrderSizeUSD must be positive")
    if cfg.min_order_usd > cfg.max_order_usd:
        errors.append("minOrderSizeUSD cannot be greater than maxOrderSizeUSD")
    if cfg.strategy == ADAPTIVE:
        if not cfg.adaptive_min_percent or not cfg.adaptive_max_percent:
            errors.append("ADAPTIVE strategy requires adaptiveMinPercent and adaptiveMaxPercent")
        if cfg.adaptive_min_percent and cfg.adaptive_max_percent and cfg.adaptive_min_percent > cfg.adaptive_max_percent:
            errors.append("adaptiveMinPercent cannot be greater than adaptiveMaxPercent")
    return errors


def parse_tiers(s: str) -> list[Tier]:
    """'1-10:2.0,10-100:1.0,100-500:0.2,500+:0.1' → tiers in ascending order."""
    if not s or not s.strip():
        return []
    tiers = []
    for d in (x.strip() for x in s.split(",") if x.strip()):
        parts = d.split(":")
        if len(parts) != 2:
            raise ValueError(f'Invalid tier format: "{d}". Expected "min-max:multiplier" or "min+:multiplier"')
        rng, m = parts
        try:
            mult = float(m)
        except ValueError:
            mult = float("nan")
        if mult != mult or mult < 0:
            raise ValueError(f'Invalid multiplier in tier "{d}": {m}')
        if rng.endswith("+"):
            lo = _num(rng[:-1])
            if lo is None or lo < 0:
                raise ValueError(f'Invalid minimum value in tier "{d}": {rng}')
            tiers.append(Tier(lo, None, mult))
        elif "-" in rng:
            a, b = rng.split("-", 1)
            lo, hi = _num(a), _num(b)
            if lo is None or lo < 0:
                raise ValueError(f'Invalid minimum value in tier "{d}": {a}')
            if hi is None or hi <= lo:
                raise ValueError(f'Invalid maximum value in tier "{d}": {b} (must be > {lo:g})')
            tiers.append(Tier(lo, hi, mult))
        else:
            raise ValueError(f'Invalid range format in tier "{d}". Use "min-max" or "min+"')
    tiers.sort(key=lambda t: t.min)
    for cur, nxt in zip(tiers, tiers[1:]):
        if cur.max is None:
            raise ValueError(f"Tier with infinite upper bound must be last: {cur.min:g}+")
        if cur.max > nxt.min:
            raise ValueError(f"Overlapping tiers: [{cur.min:g}-{cur.max:g}] and [{nxt.min:g}-{nxt.max if nxt.max is not None else '∞'}]")
        if cur.max < nxt.min:                      # pmx: a gap between tiers is also an error
            raise ValueError(f"Gap between tiers: {cur.max:g} … {nxt.min:g}")
    return tiers


def _num(x: str) -> float | None:
    try:
        return float(x)
    except ValueError:
        return None


def trade_multiplier(cfg: SizingConfig, trader_order_usd: float) -> float:
    if cfg.tiered_multipliers:
        for t in cfg.tiered_multipliers:
            if trader_order_usd >= t.min and (t.max is None or trader_order_usd < t.max):
                return t.multiplier
        # pmx: below the first tier – the first tier (the author returned the last one here: $0.5 got the multiplier for $500+)
        if trader_order_usd < cfg.tiered_multipliers[0].min:
            return cfg.tiered_multipliers[0].multiplier
        return cfg.tiered_multipliers[-1].multiplier
    if cfg.trade_multiplier is not None:
        return cfg.trade_multiplier
    return 1.0
