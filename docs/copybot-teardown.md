# Taking apart an open-source copy-bot with REA

Target: [shmlkv/polymarket-copy-trading-bot](https://github.com/shmlkv/polymarket-copy-trading-bot) – a popular permissively licensed TypeScript copy-bot for Polymarket (ISC, 49★, October 2026). It was not run and its dependencies were not installed: REA reads the source as text.

## How

[REA](https://github.com/morluto/rea) (MIT) 6.8.0, CLI only, with Node 24:

```bash
rea analyze-javascript-application /path/to/polymarket-copy-trading-bot \
  --detail summary --evidence_output shmlkv_evidence.json --format json
```

17 seconds. 108 JS/TS files parsed out of 1,168, 107,288 syntax nodes, 1,238 findings; an application graph of 1,199 nodes and 1,428 edges, a semantic graph of 58,935 nodes; every file hashed in the evidence bundle.

## What REA showed

**Every setting the bot actually reads – 33 environment variables**, each with file and line:

| what | variables | where |
|---|---|---|
| whom to follow, how often | `USER_ADDRESSES`, `FETCH_INTERVAL`, `TOO_OLD_TIMESTAMP` | `src/config/env.ts:271, 68, 82` |
| copy size | `COPY_STRATEGY`, `COPY_PERCENTAGE`, `COPY_SIZE`, `TRADE_MULTIPLIER`, `TIERED_MULTIPLIERS`, `ADAPTIVE_*` | `src/config/env.ts:182–253` |
| limits | `MAX_ORDER_SIZE_USD`, `MIN_ORDER_SIZE_USD`, `MAX_POSITION_SIZE_USD`, `MAX_DAILY_VOLUME_USD` | `src/config/env.ts:195–201` |
| wallet and exchange | `PRIVATE_KEY`, `PROXY_WALLET`, `CLOB_HTTP_URL`, `CLOB_WS_URL`, `RPC_URL`, `USDC_CONTRACT_ADDRESS` | `src/config/env.ts` |
| database | `MONGO_URI` | `src/config/env.ts:124` |

**The simulation mode does not exist.** The README promises `PREVIEW_MODE` – "simulate without trading". It is not among the bot's configuration reads; a plain search confirms it appears only in the README, `.env.example` and the web settings panel, never in the trading code. Turning "simulation" on still trades real money.

**Two child processes:**
- `spawn("npm", …)` in `web/src/app/api/actions/route.ts:24` – the web panel runs npm scripts on request, with an argument taken from the POST body and no authentication, so any page open in the browser can post to `localhost:3000` and run commands, including sells;
- `spawn("ts-node", …)` in `src/scripts/runSimulations.ts:71` – a utility script.

## Confirmed by reading the code

- Leader trades are detected by polling `/activity`; duplicates are removed by transaction hash only, so a second fill in the same transaction is lost.
- The "trade too old" filter compares unix seconds with hours and never fires.
- `MAX_DAILY_VOLUME_USD` is parsed and never applied.
- Copy sizing (`src/config/copyStrategy.ts:72–161`) is the best part of the repo.

## What polymarket-xray took and what it replaced

| theirs | problem | ours |
|---|---|---|
| sizing `copyStrategy.ts` | gaps between tiers, multiplier applied twice on sells | **taken** – ported with its tests, both fixed |
| polling `/activity`, dedup by hash | loses fills, no delay measured | Data API v2 `/trades` with an overlapping window, full-key dedup, the delay of every copy journaled |
| `PREVIEW_MODE` | not implemented | a real paper mode: no live-trading module exists, a test checks nothing that can sign is loaded |
| `postOrder` execution | fixed 5¢ tolerance, no order book | fills against the live order book (agent-next engine: FOK/FAK, price cap, fee) |
| age filter | never fires | working `max_age_sec`, skip reason journaled |
| daily limit | not applied | every risk limit either works and has a test, or isn't in the config |
| web panel with `spawn` | unauthenticated command execution | no panel, CLI only |
| MongoDB | a separate server | one SQLite file |
