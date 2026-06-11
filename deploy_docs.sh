#!/usr/bin/env bash
# Build the site locally and push to the gh-pages branch (manual deploy).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi

export HOUSE_CONFIG=house_config_rehgraeble.toml
"$PY" build_docs.py

# Split docs/ into an orphan gh-pages branch and force-push it.
git subtree split --prefix docs -b gh-pages
git push -f origin gh-pages

echo "Deployed. GitHub Pages should use branch gh-pages, folder / (root)."
