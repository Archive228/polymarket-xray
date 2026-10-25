# What we read, what we took, what we left

Before building, we read the code (not just the READMEs) of 18 open-source Polymarket analyzers and copy-bots, October 2026. Nothing from them was run; their code was only read. Code was taken only from permissively licensed repos (MIT/ISC), with the copyright kept in each file header and the full license in `THIRD_PARTY_LICENSES/`. Repos without a license were used for ideas only.

## Taken

| what | from | why |
|---|---|---|
| 8-signal bot score, archetype rules, whale and carry detection, position ledger with merges/splits | [Dennim89/polymarket-wallet-analyzer](https://github.com/Dennim89/polymarket-wallet-analyzer) (MIT) | the most careful rules in the niche; ported from Go with its tests |
| Wilson interval and edge z-test | same | changed to test the position outcome instead of PnL > 0 |
| nearest-opposite-leg search, complete-set lock | [ent0n29/polybot](https://github.com/ent0n29/polybot) (MIT) | the core of "pair" strategies on short crypto markets; Up/Down names replaced by outcome index so it works on any binary market |
| order-book fill engine (FOK/FAK, price limit, fee curve) | [agent-next/polymarket-paper-trader](https://github.com/agent-next/polymarket-paper-trader) (MIT) | exact and well tested (109 tests) |
| copy sizing (percentage / fixed / adaptive, tiers, caps) | [shmlkv/polymarket-copy-trading-bot](https://github.com/shmlkv/polymarket-copy-trading-bot) (ISC) | the best part of that repo, with 31 tests; gaps between tiers now rejected, trades below the first tier no longer get the last tier's multiplier |

## Ideas only

- Time to market end from the timestamp in a 5/15-minute market's slug; flips and hedges – fatemarcus-hub/polymarket-strategy-reverse (no license file).
- Exact Poisson-binomial tail for "is this edge luck"; PnL-curve quality – PipiClaw/polymarket_trader_analysis (no license).
- Methodology of polybot: hypotheses tested against data, with their own refutations ("bias to DOWN" was just Bitcoin falling 5% that week).

## Left, and why

| repo | what we found in the code |
|---|---|
| shmlkv (except sizing) | `PREVIEW_MODE` ("simulate without trading") is never read by the bot – it trades for real; the web panel runs npm scripts from an unauthenticated POST; MongoDB exposed without a password; the "too old" filter compares seconds with hours and never fires |
| duolaAmengweb3/duola | 5 of 6 risk limits are never enforced; the backtest counts a sell as a buy |
| jhcdx9999/polymarket-copy-trade | exits on any market order; `.gitignore` saved in UTF-16 so `.env` is not ignored; RPC key written to a log |
| realfishsam/Polymarket-Copy-Trader | detects trades by diffing positions, so a payout looks like a sale; the private key goes into an unaudited package |
| runesleo/polymarket-toolkit `compute_precise_pnl.py` | does not reconcile on wallets with neg-risk conversions |
| pselamy/polymarket-insider-tracker | at runtime only trade size is scored, max 0.45 against a 0.80 threshold – an alert can't fire |
| leolopez007/polymarket-trade-tracker | V1 exchange addresses; 500 trades take 5–10 minutes on a free RPC |
| Jon-Becker/prediction-market-analysis | good market-level datasets; three bugs in `statistical_tests.py` (effect-size sign, `np.repeat` instead of weights, no multiple-test correction) |

## What none of them had

A copy simulation with delay. Every backtest we read copies at the leader's own price with no delay, no fee and no spread.
