#!/bin/bash
# Run the image's ARM binary under qemu-user. No kernel, no boot, seconds.
#
#   run-user.sh --bench-ai 20     time one AI decision by operator kind
#   run-user.sh --bench 600       time emulation, with and without the AI slice
#
# Emulated NEON is 20-50x slower than the Cortex-A8. Correctness only, never
# quote these against the frame budget.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
WORK=${WORK:-$HOME/buildroot-pokeboy}
TARGET=$WORK/buildroot/output/target
ROM=${ROM:-$REPO/pred-patch/pokered-ai.gbc}
WEIGHTS=${WEIGHTS:-$REPO/ai/checkpoints/pep/current/pkai.weights}

[ -x "$TARGET/usr/bin/pokeboy" ] || { echo "no image at $TARGET - run build-image.sh"; exit 1; }
[ -f "$ROM" ] || { echo "no ROM at $ROM - build it with pred-patch/build_ai.sh"; exit 1; }
exec docker run --rm \
  -v "$TARGET":/rootfs:ro \
  -v "$ROM":/data/pokered-ai.gbc:ro \
  -v "$WEIGHTS":/data/pkai.weights:ro \
  pokeboy-emu qemu-arm-static -cpu cortex-a8 -L /rootfs \
  /rootfs/usr/bin/pokeboy /data/pokered-ai.gbc --weights /data/pkai.weights "$@"
