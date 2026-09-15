#!/usr/bin/env bash
# Source me. Paths for the on-device AI toolchain; everything defaults to a location inside ai/.
# Override any variable before sourcing to point at an existing install elsewhere.
AI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AI_ROOT
export AI_EXTERNAL="${AI_EXTERNAL:-$AI_ROOT/external}"           # third-party checkouts (gitignored)
export SHOWDOWN_DIR="${SHOWDOWN_DIR:-$AI_EXTERNAL/pokemon-showdown}"
export SHOWDOWN_PORT="${SHOWDOWN_PORT:-8000}"
export METAMON_DIR="${METAMON_DIR:-$AI_EXTERNAL/metamon}"
export METAMON_VENV="${METAMON_VENV:-$AI_EXTERNAL/metamon-venv}"
export METAMON_CACHE_DIR="${METAMON_CACHE_DIR:-$AI_EXTERNAL/metamon-cache}"   # read by metamon itself
export METAMON_TEAM_DIR="${METAMON_TEAM_DIR:-$METAMON_CACHE_DIR/teams/competitive/gen1ou}"
export PKAI_BUILD="${PKAI_BUILD:-$AI_ROOT/build/pkai}"            # C++ build tree with pkai_model_tests
export PKAI_ROM_CRC32="${PKAI_ROM_CRC32:-0x133efc7c}"            # Pokémon Red (UE) [S][!]
export CKPT_ROOT="${CKPT_ROOT:-$AI_ROOT/checkpoints/pep}"
