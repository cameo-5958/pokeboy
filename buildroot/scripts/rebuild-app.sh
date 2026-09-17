#!/bin/bash
# Rebuild just the pokeboy package in an existing tree and regenerate the
# images. Use after editing gameboy/, pkai/ or emulator/.
set -euo pipefail
WORK=${WORK:-$HOME/buildroot-pokeboy}
cd "$WORK/buildroot"
make pokeboy-rebuild
make
ls -la output/images/
