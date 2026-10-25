#!/usr/bin/env bash
# Copy a wallet on a paper account against live order books: paper.sh <wallet> [--usd 10] [--minutes 20] [--cash 1000]
# pmx has no real trading – paper only.
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
skill="$(skill_dir "$0")"
root="$(find_pmx "$skill")"
check_python "$root"
export PMX_DATA="$(data_dir "$root")"

target="${1:?specify a wallet: a 0x… address, a profile link or a name}"
shift
usd=10 minutes=20 cash=1000
while [ $# -gt 0 ]; do
  case "$1" in
    --usd) usd="$2"; shift 2 ;;
    --minutes) minutes="$2"; shift 2 ;;
    --cash) cash="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$root"
cfg="$(python3 -W ignore -c '
import json, os, sys
from pathlib import Path
from pmx import api
w, name = api.resolve_wallet(sys.argv[1])
usd, cash = float(sys.argv[2]), float(sys.argv[3])
c = json.loads(Path("bot.example.json").read_text())
slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or w))[:40]
c.update(name=f"paper-{slug}", leaders=[w], start_cash=cash)
c["sizing"].update(strategy="FIXED", copy_size=usd, max_order_usd=max(2 * usd, usd + 1), max_position_usd=6 * usd)
c["risk"].update(max_daily_buy_usd=cash, max_market_usd=10 * usd, max_total_exposure_usd=0.8 * cash, max_drawdown_usd=0.3 * cash)
p = Path(os.environ["PMX_DATA"]) / "bot" / f"bot_{slug}.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(c, ensure_ascii=False, indent=1))
print(p)' "$target" "$usd" "$cash")"
echo "config: $cfg (copy \$$usd per trade, account \$$cash, $minutes min)"
python3 -W ignore -u -m pmx.bot paper --config "$cfg" --minutes "$minutes"
