#!/usr/bin/env bash
# Build the C++ pkai library and its bit-exactness test (pkai_model_tests) into $PKAI_BUILD.
# The sources live in ../gameboy and ../pkai; the test reads checkpoints/pep/current by default.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cmake -S "$AI_ROOT/../gameboy" -B "$PKAI_BUILD" -DCMAKE_BUILD_TYPE=Release \
  -DPOKEBOY_LINUX_FRONTEND=OFF -DPKAI_CHECKPOINT="$CKPT_ROOT/current" "$@"
cmake --build "$PKAI_BUILD" --target pkai_model_tests -j "$(nproc)"
echo "built $PKAI_BUILD/pkai_model_tests"
