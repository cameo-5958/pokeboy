#!/bin/bash
# Re-sync the emulator/AI sources into an existing Buildroot tree and rebuild
# just the pokeboy package, then regenerate the images. Minutes, not hours.
# Use after editing gameboy/, pkai/ or emulator/. Needs a completed build-image.sh.
set -euo pipefail
WORK=${WORK:-$HOME/buildroot-pokeboy}
cd "$WORK/buildroot"
make pokeboy-rebuild
make
ls -la output/images/
