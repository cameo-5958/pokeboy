#!/bin/bash
# Build $WORK/emu/data.vfat, standing in for the SD card's boot partition: the
# patched ROM and the int8 weights, which the image expects at /boot. Uses
# Buildroot's mtools, so no loop mount and no root.
#
#   ROM=... WEIGHTS=... make-data-disk.sh
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
WORK=${WORK:-$HOME/buildroot-pokeboy}
EMU=$WORK/emu
OUT=$EMU/data.vfat
ROM=${ROM:-$REPO/pred-patch/pokered-ai.gbc}
WEIGHTS=${WEIGHTS:-$REPO/ai/checkpoints/pep/current/pkai.weights}

export PATH="$WORK/buildroot/output/host/bin:$WORK/buildroot/output/host/sbin:$PATH"
[ -f "$ROM" ] || { echo "no ROM at $ROM - build it with pred-patch/build_ai.sh"; exit 1; }
mkdir -p "$EMU"
rm -f "$OUT"
mkdosfs -C "$OUT" 16384 >/dev/null
MTOOLS_SKIP_CHECK=1 mcopy -i "$OUT" "$ROM" ::pokered-ai.gbc
if [ -f "$WEIGHTS" ]; then
    MTOOLS_SKIP_CHECK=1 mcopy -i "$OUT" "$WEIGHTS" ::pkai.weights
else
    echo "note: no weights at $WEIGHTS - the emulator will run without the AI"
fi
MTOOLS_SKIP_CHECK=1 mdir -i "$OUT" ::
