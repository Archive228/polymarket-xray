# polymarket-xray – instructions for AI agents

This repo reverse-engineers a Polymarket wallet from public data: is it a bot, what rule it trades by, where its money comes from, and whether a copy of it still makes money 1 to 60 minutes late. No API keys, no account, nothing is ever bought or sold.

Claude Code users install it as a plugin (see README). Any other agent that can run Python 3.9+ and reach the internet (github.com, data-api.polymarket.com, gamma-api.polymarket.com, clob.polymarket.com, api.binance.com) uses it like this.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 -m pmx all <wallet>        # a 0x address, a polymarket.com profile link, or a profile name
```

A wallet with up to ~10,000 trades takes under a minute; millions of trades take 15+ minutes. For wallets with more than 300,000 trades add `--days 30`. If the repo is not on disk yet, `skills/xray/scripts/setup.sh` clones it into `/workspace/polymarket-xray` (or `~/.polymarket-xray/src`) and installs the packages.

Results go to `data/wallets/<address>/` (or `$PMX_DATA/wallets/<address>/`).

## Read, in this order

1. `analysis.json` → `reliability.level` and `reliability.reasons`: how much of the rest you may state as fact.
2. `recipe.md`: what the wallet does step by step, each step with a number, and where the money comes from.
3. `summary.md`: the same in plain sentences, with the copy check.
4. `copysim.json` → `overall`: return for `his`, `m0` (right after him), `m1`, `m5`, `m15`, `m60`, and `copied_share`.

`skills/xray/SKILL.md` has the full answer format and the rules behind it; `skills/xray/references/fields.md` explains every field.

## Answer

In the user's language, short:

- bot or human (and how many of the 8 bot signals fired), what it trades;
- the recipe in 5 to 9 steps, each with its number;
- where the money comes from;
- **Copy? yes, up to N minutes late / no: reason / unclear: reason**, then four checks:
  1. one minute late: if the copy is already negative, don't copy;
  2. how long its markets live: minutes favour speed, hours or days leave a copy time;
  3. where the money comes from: "approximate" or "unreliable", or profit from conversions or rebates, means copying the trades won't repeat it;
  4. sample size: under ~30 positions it could be luck;
- link `report.html`.

## Rules

- Every number comes from the output files. Never estimate or invent one.
- Paper trading only (`python3 -m pmx bot …`). There is no live-trading code; do not write any.
- This is analysis of public data, not financial advice. Don't tell the user to deposit, bet or copy.
