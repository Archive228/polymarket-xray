#!/usr/bin/env bash
# Put the pmx code where the skill finds it, for agents that copied only this skill folder.
# setup.sh [target dir]  – default: /workspace/polymarket-xray if /workspace exists, else ~/.polymarket-xray/src
set -euo pipefail
here="$(dirname "$0")"
source "$here/_common.sh"
dest="${1:-$(default_home)}"

if [ -f "$dest/pmx/__main__.py" ]; then
  echo "pmx is already in $dest – updating"
  git -C "$dest" pull --ff-only --quiet || echo "could not update $dest, using it as is" >&2
else
  mkdir -p "$(dirname "$dest")"
  git clone --depth 1 --quiet https://github.com/Archive228/polymarket-xray "$dest"
fi

# a plain install first; Debian-style systems refuse it (externally managed Python), so then the user's site-packages.
# A cloud agent's computer drops installed packages on a rebuild: running this again puts them back.
python3 -m pip install --quiet -r "$dest/requirements.txt" 2>/dev/null \
  || python3 -m pip install --quiet --user -r "$dest/requirements.txt" 2>/dev/null \
  || python3 -m pip install --quiet --user --break-system-packages -r "$dest/requirements.txt"
echo "pmx is ready in $dest"
[ "$dest" = "$(default_home)" ] || echo "it is not in the default place: export PMX_HOME=\"$dest\""
