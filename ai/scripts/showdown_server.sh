#!/usr/bin/env bash
# Run the local Pokémon Showdown server (foreground). poke-env clients connect to localhost:$SHOWDOWN_PORT.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$SHOWDOWN_DIR" && exec node pokemon-showdown start --no-security --port "$SHOWDOWN_PORT"
