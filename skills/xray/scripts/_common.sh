# Shared by the skill scripts: find the pmx code, pick the data folder, check the Python packages.
# The skill may live in the repo, in an installed plugin, or be linked in (~/.claude/skills/…), so paths go through realpath.

skill_dir() {
  python3 -c 'import os, sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))' "$1"
}

find_pmx() {
  local from_skill
  from_skill="$(cd "$1/../.." 2>/dev/null && pwd || true)"
  for c in "${PMX_HOME:-}" "$from_skill"; do
    if [ -n "$c" ] && [ -f "$c/pmx/__main__.py" ]; then
      echo "$c"
      return 0
    fi
  done
  echo "pmx not found: clone github.com/Archive228/polymarket-xray or set the path in PMX_HOME" >&2
  return 1
}

# Exports, cache and paper journals. An installed plugin's folder is replaced on every update,
# so there the data goes to ~/.polymarket-xray; in a clone it stays in data/ next to the code.
data_dir() {
  if [ -n "${PMX_DATA:-}" ]; then
    echo "$PMX_DATA"
  elif [[ "$1" == *"/.claude/plugins/"* ]]; then
    echo "$HOME/.polymarket-xray"
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
