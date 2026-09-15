#!/usr/bin/env bash
# One-time setup of the external benchmark rig under ai/external:
#   pokemon-showdown  local server (node)            https://github.com/smogon/pokemon-showdown
#   metamon           pretrained Gen I OU agents      https://github.com/UT-Austin-RPL/metamon
#   metamon-venv      python 3.11 venv with metamon installed editable (torch 2.x, no flash-attn)
#   metamon-cache     METAMON_CACHE_DIR: models + team sets download here on first use
# The PyPI package named "metamon" is unrelated; always install from the clone.
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$AI_EXTERNAL" "$METAMON_CACHE_DIR"
if [ ! -d "$SHOWDOWN_DIR" ]; then
  git clone --depth 1 https://github.com/smogon/pokemon-showdown.git "$SHOWDOWN_DIR"
  (cd "$SHOWDOWN_DIR" && npm install)
fi
if [ ! -d "$METAMON_DIR" ]; then
  git clone --depth 1 https://github.com/UT-Austin-RPL/metamon "$METAMON_DIR"
fi
if [ ! -x "$METAMON_VENV/bin/python" ]; then
  uv venv --python 3.11 "$METAMON_VENV"
  uv pip install --python "$METAMON_VENV/bin/python" -e "$METAMON_DIR"
fi
# Gen I OU team corpora (the `ou` matchup mode reads these): ~30k parties under
# datasets/raw/metamon/jakegrigsby__metamon-teams/{competitive,paper_variety,modern_replays}.
(cd "$AI_ROOT" && uv run python -m data pull --source metamon --only teams)

echo "showdown: $SHOWDOWN_DIR"; echo "metamon:  $METAMON_DIR ($METAMON_VENV)"; echo "cache:    $METAMON_CACHE_DIR"
echo "teams:    $AI_ROOT/datasets/raw/metamon/jakegrigsby__metamon-teams"
echo "next: scripts/showdown_server.sh, then tools.showdown_eval / scripts/metamon_h2h.sh"
