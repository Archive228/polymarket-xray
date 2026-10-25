# Fields of the pmx output

Read only the section you need.

## analysis.json

| key | meaning |
|---|---|
| `passport.pnl_economic` | official Polymarket PnL (with rewards and rebates) – the headline number |
| `passport.trades_all_time` / `trades_exported` | trades by Polymarket's count / exported (with `--days` the second is smaller by design) |
| `passport.share_from_rewards_and_rebates` | share of PnL that is liquidity rewards and fee rebates, not bets |
| `reliability.level` | "reliable" – own ledger within 5% of official; "approximate" – warnings only; "unreliable" – a reason below makes price-bucket analysis untrustworthy |
| `reliability.reasons` | the reasons, in order of importance |
| `reliability.coverage_buy_usd` | share of buy money in resolved positions (the rest is in open markets) |
| `reliability.conversion_merge_split_share` | share of buy money in markets with merges, splits or neg-risk conversions – the trade ledger cannot see those |
| `money_flow` | sums by activity type: `TRADE_BUY`, `TRADE_SELL`, `REDEEM`, `MERGE`, `SPLIT`, `CONVERSION`, `REWARD`, `MAKER_REBATE`, `TAKER_REBATE` |
| `bot.verdict` | "bot" (≥4 of 8 signals or a hard signal), "unclear" (2–3), "likely human" |
| `bot.style.label` / `axes` / `override` | type label, the signals that fired (text), the pmx hard signal (thousands of conversions/merges) |
| `carry` | collecting the nearly decided: `carry_frac` – money at ≥85¢, `carry_win_rate`, `type` hold/flip |
| `pairs.pairing` | `both_legs_share` – markets where he bought both sides; `opposite_leg_delay_sec` p50/p90 |
| `pairs.complete_sets` | `avg_edge_per_share` (1 − price of the pair), `locked_pnl` (earned or lost on pairs), `directional_pnl` (tilt to one side) |
| `timing_to_end` | seconds to market end at purchase: `quantiles_sec`, `buckets`, `short_series` (5/15-minute crypto) |
| `entry.by_price_bucket[]` | per entry-price bucket: `buy_usd_share`, `pnl`, `roi`, `test` (n, wins, expected_wins, p_value), `p_adjusted`, `verdict` |
| `by_outcome`, `direction_warning` | PnL by outcome; warning when all profit is one side over a short span (could be the market move) |
| `by_week` | PnL of resolved positions by ISO week of entry – shows whether the result is steady or a few big weeks |
| `entry.maker_share_trades` / `maker_share_usd` | share of fills / of money that were his resting orders (maker); they can differ a lot |
| `sizing` | `trade_usd` quantiles, `position_cost` quantiles, `fixed_lot_share` (share of trades at the most common size), `price_vs_size_spearman` |
| `timing.hours_utc`, `timing.minutes_from_event_start` | share of trades by UTC hour; minutes from event start (negative – before the start) |
| `roi_resolved`, `pnl_resolved_by_trades` | return and PnL of all resolved positions by his own trades, fees included |
| `recipe` | the same content as recipe.md: `steps`, `spec`, `economics`, `unseen` |

Bucket `verdict`: "beats the price" – wins more often than the entry price implies, significant after Holm correction; "indistinguishable from chance"; "too little data" (<30 positions).

## copysim.json

| key | meaning |
|---|---|
| `modes` | `his` – his own prices and fees; `m0` – a copy right after him at the market; `m1` `m5` `m15` `m60` – copy N minutes later |
| `overall[mode].roi` | return weighted by his money in each price bucket |
| `overall[mode].copied_share` | share of his buy money the copy managed to repeat (markets closed before the copy → lower) |
| `overall[mode].slip_pp_per_cent` | how many percentage points each extra 1¢ of slippage costs |
| `population_positions` / `sample` | resolved positions available / sampled (latest from each bucket) |

Copy price = median of minute history in the 2 minutes after the copy moment, never better than his price; the copy is a taker and pays the market fee.

## signal.json (crypto Up/Down only)

| key | meaning |
|---|---|
| `on_lead_usd` | share of his money on the side leading by price since the window start (Binance minute prices) |
| `by_move[]` | the same by size of the move: `on_lead_usd` rising with the move means he follows the price |
| `buy_leader` / `buy_against` | win rate, average price and return before fees of buys on / against the leader |
| `tilt_matches_lead`, `tilt_wins` | two-sided markets: share where more money sits on the leader, and how often that side wins |

## Paper bot (<data dir>/bot/<name>.db, report printed at the end)

`lag` – seconds from his trade to our decision (includes the 3 s poll). Skip reasons: "best price … is above the limit" – the book moved or he was filled inside the spread; "less than 60 s until trading ends"; "price outside the corridor" (0.02–0.98); "leader trade older than N s"; risk limits by name; "below the minimum order" – the sized copy is under $1. Account value at the end marks open positions at the order-book midpoint.
