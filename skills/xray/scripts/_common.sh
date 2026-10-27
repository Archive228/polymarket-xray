# Shared by the skill scripts: find the pmx code, pick the data folder, check the Python packages.
# The skill may live in the repo, in an installed plugin, be linked in (~/.claude/skills/…), or be copied alone
# by a skill installer or another agent (Grok Bot, Cursor, Codex) – then setup.sh puts the code in default_home.

skill_dir() {
  python3 -c 'import os, sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))' "$1"
}

# Where setup.sh clones the repo: /workspace survives a rebuild of a cloud agent's computer, ~/.polymarket-xray elsewhere.
default_home() {
  if [ -d /workspace ] && [ -w /workspace ]; then
    echo "/workspace/polymarket-xray"
  else
    echo "$HOME/.polymarket-xray/src"
  fi
}

find_pmx() {
  local from_skill
  from_skill="$(cd "$1/../.." 2>/dev/null && pwd || true)"
  for c in "${PMX_HOME:-}" "$from_skill" "$(default_home)"; do
    if [ -n "$c" ] && [ -f "$c/pmx/__main__.py" ]; then
      echo "$c"
      return 0
    fi
  done
  echo "pmx not found: run \"$1/scripts/setup.sh\" (clones github.com/Archive228/polymarket-xray into $(default_home) and installs its Python packages), or set PMX_HOME" >&2
  return 1
}

# Exports, cache and paper journals. An installed plugin's folder is replaced on every update, and setup.sh's clone
# may be refreshed, so there the data goes to ~/.polymarket-xray (/workspace on a cloud agent's computer, which survives
# a rebuild); in a clone of your own it stays in data/ next to the code.
data_dir() {
  if [ -n "${PMX_DATA:-}" ]; then
    echo "$PMX_DATA"
  elif [[ "$1" == *"/.claude/plugins/"* || "$1" == *"/.cursor/plugins/"* || "$1" == "$HOME/.polymarket-xray/src" ]]; then
    if [ -d /workspace ] && [ -w /workspace ]; then echo "/workspace/polymarket-xray-data"; else echo "$HOME/.polymarket-xray"; fi
  else
    echo "$1/data"
  fi
}

check_python() {
  if ! python3 -W ignore -c "import requests, pandas, numpy, scipy, matplotlib, jinja2" 2>/dev/null; then
    echo "missing Python packages. Install with: python3 -m pip install -r \"$1/requirements.txt\"" >&2
    return 3
  fi
}
