#!/usr/bin/env bash
# Re-check wallets and say what changed since each one's previous check: watch.sh <wallet> [<wallet> …] [--days N]
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
skill="$(skill_dir "$0")"
root="$(find_pmx "$skill")"
check_python "$root"
export PMX_DATA="$(data_dir "$root")"

cd "$root"
python3 -W ignore -m pmx watch "$@"
