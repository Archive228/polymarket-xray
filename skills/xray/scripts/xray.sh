#!/usr/bin/env bash
# Polymarket wallet breakdown: export all trades → analysis and recipe → signal check → copyability → report.
# xray.sh <0x… address | profile link | name> [--days N]
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
skill="$(skill_dir "$0")"
root="$(find_pmx "$skill")"
check_python "$root"
export PMX_DATA="$(data_dir "$root")"

target="${1:?specify a wallet: a 0x… address, a profile link or a name}"
shift
days=""
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$root"
# the wallet and how many trades it has per Polymarket – so as not to export millions of rows without need
read -r wallet trades < <(python3 -W ignore -c '
import sys
from pmx import api
w, _ = api.resolve_wallet(sys.argv[1])
print(w, (api.stats(w).get("all_time_pnl") or {}).get("trade_count") or 0)' "$target") || true
if [ -z "${wallet:-}" ]; then
  echo "could not find a wallet '$target' – pass a 0x… address or a profile link" >&2
  exit 1
fi
echo "wallet $wallet, trades per Polymarket: $trades"
if [ -z "$days" ] && [ "$trades" -gt 300000 ]; then
  days=30
  echo "more than 300,000 trades – exporting the last $days days (the recent period describes the strategy; for everything: --days 3650)"
fi

python3 -W ignore -m pmx all "$wallet" ${days:+--days "$days"}

d="$PMX_DATA/wallets/$wallet"
echo
echo "results: $d"
for f in recipe.md summary.md report.html analysis.json copysim.json signal.json; do
  [ -f "$d/$f" ] && echo "   $d/$f"
done
