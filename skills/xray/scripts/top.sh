#!/usr/bin/env bash
# Copy verdicts for the top of a Polymarket leaderboard: top.sh [--category SPORTS] [--period WEEK] [--n 10] [--days N]
# → TOP_<category>_<period>_<date>.md in the data folder.
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
skill="$(skill_dir "$0")"
root="$(find_pmx "$skill")"
check_python "$root"
export PMX_DATA="$(data_dir "$root")"

cd "$root"
python3 -W ignore -m pmx top "$@"
