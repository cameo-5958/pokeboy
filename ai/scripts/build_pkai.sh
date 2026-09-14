#!/usr/bin/env bash
# Build the C++ pkai targets into $PKAI_BUILD: the shared library the Python bridge loads
# (libpkai_c, used by sim/pkai_bridge.py, serve/int_agent.py and their tests) and the
# bit-exactness test pkai_model_tests, which reads checkpoints/pep/current by default.
# The sources live in ../gameboy and ../pkai.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cmake -S "$AI_ROOT/../gameboy" -B "$PKAI_BUILD" -DCMAKE_BUILD_TYPE=Release \
  -DPOKEBOY_LINUX_FRONTEND=OFF -DPKAI_CHECKPOINT="$CKPT_ROOT/current" "$@"
cmake --build "$PKAI_BUILD" --target pkai_c pkai_model_tests -j "$(nproc)"
echo "built in $PKAI_BUILD:"; find "$PKAI_BUILD" -name 'libpkai_c.*' -o -name 'pkai_model_tests' | sed 's|^|  |'
