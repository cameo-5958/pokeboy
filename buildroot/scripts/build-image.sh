#!/bin/bash
# Build the SD-card image: Buildroot 2024.02.13 plus the external tree here,
# cross-compiled for the OSD3358. Writes to $WORK, reads the repo. First run
# builds a toolchain and takes 1-2 hours.
#
# Result: $WORK/buildroot/output/images/sdcard.img, 32 MiB FAT32 boot partition
# plus a 128 MiB ext4 root. The ROM and weights are not in it: copy them onto
# partition 1 as pokered-ai.gbc and pkai.weights, where S99pokeboy expects them.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${WORK:-$HOME/buildroot-pokeboy}
BR_VERSION=${BR_VERSION:-2024.02.13}
BR_URL=https://gitlab.com/buildroot.org/buildroot.git

mkdir -p "$WORK"
if [ ! -d "$WORK/buildroot" ]; then
    echo "=== cloning Buildroot $BR_VERSION"
    git clone --depth 1 --branch "$BR_VERSION" "$BR_URL" "$WORK/buildroot"
fi
cd "$WORK/buildroot"

echo "=== configuring against $REPO/buildroot"
make BR2_EXTERNAL="$REPO/buildroot" pokeboy_defconfig

echo "=== building ($(nproc) cores). First run also builds the cross toolchain."
make

echo
echo "=== images in $WORK/buildroot/output/images"
ls -la output/images/
