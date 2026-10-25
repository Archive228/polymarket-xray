#!/usr/bin/env bash
# One table for every wallet analysed so far: summary.sh → WALLET_SUMMARY.md in the data folder.
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
skill="$(skill_dir "$0")"
root="$(find_pmx "$skill")"
check_python "$root"
export PMX_DATA="$(data_dir "$root")"

cd "$root"
python3 -W ignore -m pmx.summary
