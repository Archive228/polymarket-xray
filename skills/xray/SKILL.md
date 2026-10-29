---
name: xray
description: Reverse-engineer any Polymarket wallet with the local pmx tool – download every trade, tell whether it is a bot and which strategy it runs (step-by-step recipe with evidence), where the money really comes from, and whether copying it still pays after a 1–60 minute delay; optionally copy it on a paper account against live order books. Use this whenever the user gives a Polymarket wallet address, a profile link (polymarket.com/@name or /profile/0x…) or a trader name and asks what it does, how it makes money, whether it is a bot, whether to copy-trade it, "analyze this wallet", "what is his strategy", "can he be copied", or wants several Polymarket traders compared, or asks which top traders on a Polymarket leaderboard can be copied, or wants wallets watched for changes – even if pmx is not mentioned.
compatibility: Needs python3, git and outbound HTTPS to github.com, data-api.polymarket.com, gamma-api.polymarket.com, clob.polymarket.com and api.binance.com. No API keys.
---

# Polymarket wallet X-ray

pmx turns one wallet into an evidence-backed strategy breakdown using only public Polymarket data (no keys):

- every fill (maker and taker), activity, positions, markets;
- **recipe** – what the wallet does, step by step, each step with a number from its own trades and a strength word ("always" ≥90%, "almost always" ≥75%, "mostly" ≥50%, "sometimes");
- **reliability** – its own trade ledger reconciled against Polymarket's official PnL and fees, with reasons when they do not match;
- **bot or human** – 8 independent signals (rules from Dennim89/polymarket-wallet-analyzer), plus pairs of legs (ent0n29/polybot);
- **copyability** – his own result vs a copy made right after him and 1/5/15/60 minutes later, as a taker with fees;
- **signal check** for crypto Up/Down markets – compared with Binance minute prices;
- a paper copy-bot that follows the wallet live (no real money, no keys).

## 1. Run the breakdown

```bash
bash <this skill's directory>/scripts/xray.sh <wallet> [--days N]
```

`<wallet>` – a 0x address, a profile link or a profile name. The script finds the pmx code, checks Python packages, runs the whole pipeline and prints where the results are.

How long it takes, so you can tell the user and pick foreground or background:
- up to ~10k trades – under a minute;
- ~200k trades – several minutes;
- millions of trades – 15+ minutes and ~3 GB of memory. Above 300k official trades the script itself limits the export to the last 30 days and says so; the strategy is described by the recent period anyway. Pass `--days` to override.

Run long breakdowns in the background and tell the user it is running. A failure on a temporary Polymarket API error is fixed by running the same command again: the export resumes from the days already saved.

## 2. Read the results – in this order

All files are in the wallet folder the script prints (`<data folder>/wallets/<address>/`; the data folder is `~/.polymarket-xray` for the installed plugin, `data/` in a clone, or `$PMX_DATA`).

1. `analysis.json` → `reliability`: `level` ("reliable" / "approximate" / "unreliable") and `reasons`. Read this first, because it decides how much of the rest you can state as fact.
2. `recipe.md` – the main result: steps, technical spec, where the money comes from, what the trades cannot show.
3. `summary.md` – the same in plain sentences, including the copyability line.
4. `verdict.json` → `line` and `score`: the copy verdict and the Copy Score (0–100), computed by the rule below. Use them as they are.
5. `copysim.json` → `overall`: `roi` and `copied_share` for `his`, `m0` (right after him), `m1`, `m5`, `m15`, `m60`; `population_positions` and `sample`.
6. `signal.json` – only for crypto Up/Down wallets.

For a question the files above do not answer directly, `references/fields.md` explains every field.

## 3. Answer

Answer in the user's language. Lead with the verdict, then the recipe, then copyability – the user came to learn what the wallet does and whether it can be repeated. Keep it short: the full detail is in `report.html`, which you link at the end.

Use this shape:

```
**<name> – <type label>** (<bot verdict>, <N>/8 bot signals)
Official Polymarket PnL <x> · volume <y> · <trades> trades · reliability: <level>

### What it does
1. <recipe step> – <its key number>
… (5–9 steps from recipe.md, most telling first)

### Where the money comes from
<2–4 lines from recipe.md "Where the money comes from">

### Can it be copied
| | himself | right after him | +1 min | +15 min | +60 min |
|---|---|---|---|---|---|
| return | … | … | … | … | … |
| share of his money copied | … | … | … | … | … |

**Copy? <yes, up to N minutes late / no: <reason> / unclear: <reason>> · Copy Score <score>/100**
1. One minute late: <m1 return> – <negative: the copy already loses>
2. How long its markets live: <minutes / hours / days, from the recipe's markets and entry window> – <minutes: speed wins and a copy arrives late; hours or days: a copy has time>
3. Where the money comes from: reliability <level> – <if conversions, merges or rebates drive the profit: copying the trades will not repeat it>
4. Sample: <positions> positions – <under ~30: it could be luck>

### What the trades cannot show
<1–3 lines>

Report: <path to report.html> · recipe: <path to recipe.md>
```

Why these rules matter:
- **Numbers only from the output files.** The tool's value is that every number traces back to the wallet's own trades and reconciles with Polymarket; one invented number makes the whole breakdown untrustworthy.
- **"unreliable" changes what you may claim.** Say which part cannot be trusted (the first reason) and do not present price-bucket conclusions. The official PnL, the money flow and the recipe's behaviour steps (markets, timing, order type, size) stay valid.
- **Two-sided wallets** (buys both sides in ≥30% of markets): price buckets describe two legs of one pair, so skip bucket conclusions; explain the pair economics from the recipe instead.
- **Copyability.** The gap between "himself" and "right after him" is entry price and fees (his maker fills, the spread), not delay; the further columns are the delay. Copy prices are conservative – never better than his. If `population_positions` or the per-mode `positions` is under ~30, say the sample is small. A `copied_share` below 10% means the markets close before a copy can enter – say "the copy no longer gets in" rather than quoting a return.
- **The copy sample is his latest positions.** If `analysis.json → roi_resolved` (all resolved positions) differs noticeably from "himself" in copysim, give both: the recent period may be better or worse than his history.
- **Say which period you describe.** The recipe covers the whole export; when it says "Last 30 days it trades something else:", the wallet has switched markets – lead with what it trades now.
- **Maker vs taker by trades and by money can disagree** (many small fills from his resting orders, most money taken from the book). Report both, as the recipe does; it changes how copyable he is.
- **Bot vs human is a heuristic** (≥4 of 8 signals = bot, 2–3 = unclear). Name the signals that fired instead of asserting intent.
- **The Copy Score** (0–100) puts the four checks in one number: how much a copy one minute late still makes (+25% or more is full marks), how many of the delays still pay, and how far to trust it (sample size, reliability). 0 means "no". Quote it from `verdict.json`; never compute your own.
- **The copy verdict** answers "can I copy it?" in one line, then the four checks show why. "no" when the copy is already negative one minute late (or right after him); "yes, up to N minutes late" for the longest delay that is still positive with at least half of his money copied; "unclear" when the sample is under ~30 positions or reliability is "unreliable" – say which. Each check states its number, never a guess.
- **This is analysis of public data, not investment advice.** Do not tell the user to deposit, bet or copy; describe what the numbers show.

## 4. Copy it on paper (only when the user asks)

```bash
bash <this skill's directory>/scripts/paper.sh <wallet> [--usd 10] [--minutes 20]
```

The bot polls the wallet every 3 seconds, sizes each copy (fixed `--usd` per trade), and executes it on a paper account against the live order book with the market's taker fee, a price limit (no worse than his price by 5% and by 2¢) and risk limits. It runs for `--minutes` and prints a report: lag from his trade to our decision, what was copied and why the rest was skipped, our price vs his, fees, account value. Run it in the background and report when it finishes; if the wallet is quiet, say so – no trades in the window is a result, not an error.

The most common skip reasons are themselves the finding: "best price … is above the limit" means the price had already moved or he was filled inside the spread; "less than 60 s until trading ends" means the market was about to close.

Real trading is not implemented. If the user asks for it, explain that it needs their own decision, a separate wallet and their keys in `.env`, and is outside this skill.

## 5. Several wallets

Run the breakdown for each, then:

```bash
bash <this skill's directory>/scripts/summary.sh
```

It writes `WALLET_SUMMARY.md` in the data folder: one table with type, reliability and copyability for every analysed wallet, plus short recipes.

## 6. Scout a leaderboard

When the user asks who on Polymarket can be copied, which top traders are worth following, or to scout a category:

```bash
bash <this skill's directory>/scripts/top.sh --category POLITICS --period WEEK --n 10
```

Categories: OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE, ESPORTS. Periods: DAY, WEEK, MONTH, ALL. It runs the whole breakdown on each wallet (a few minutes each; huge wallets get their last 30 days), so run it in the background. It prints and saves `TOP_<category>_<period>_<date>.md`: one row per wallet with its copy verdict, and a short recipe for every "yes". Lead your answer with the "yes" wallets and how many minutes late a copy still pays; if there are none, say so.

## 7. Watch wallets

When the user wants to follow wallets over time:

```bash
bash <this skill's directory>/scripts/watch.sh <wallet> [<wallet> …]
```

It re-runs the breakdown and compares each verdict with the wallet's previous check (`verdicts.jsonl` in its folder): the verdict, the one-minute-late return turning negative, the main market, a 5-point move in its own return. Tell the user only what changed; "no change" is a one-line answer. On an agent with a schedule (Grok Bot routines, cron), run it daily.

The verdict in sections 6 and 7 uses the same rule as "Copy?" in section 3, computed by `pmx/scout.py`.

## Troubleshooting

- **Missing Python packages** – the script prints the `pip install -r requirements.txt` command; ask the user before installing.
- **"could not find a wallet"** – the name search found no exact profile; ask for the 0x address or the profile link.
- **pmx not found** – the script looks in `$PMX_HOME`, then the plugin or repo this skill lives in, then the folder `scripts/setup.sh` installs to. When only this skill folder was copied (a skill installer, Grok Bot, Cursor, Codex), run `bash <this skill's directory>/scripts/setup.sh`: it clones github.com/Archive228/polymarket-xray into `/workspace/polymarket-xray` (or `~/.polymarket-xray/src`) and installs the Python packages. Ask the user first if you cannot install on your own.
