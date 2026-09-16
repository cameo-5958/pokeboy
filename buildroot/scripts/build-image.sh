#!/bin/bash
# Build the Pokeboy SD-card image.
#
# Buildroot 2024.02.13 plus the external tree in buildroot/, cross-compiling the
# emulator and the on-device battle AI for the OSD3358 (Cortex-A8, NEON).
# Everything is written under $WORK; the repository is only ever read.
#
#   build-image.sh              build (first run clones Buildroot and the toolchain: ~1-2 h)
#   WORK=/scratch/br ./build-image.sh
#
# Result: $WORK/buildroot/output/images/sdcard.img
#   partition 1  32 MiB FAT32, bootable: MLO, u-boot.img, zImage, dtb, extlinux
#   partition 2 128 MiB ext4:  root filesystem with /usr/bin/pokeboy
#
# The ROM, the weights and the save file are NOT in the image. Copy them onto
# partition 1 as pokered-ai.gbc and pkai.weights; /etc/init.d/S99pokeboy mounts
# that partition at /boot and launches the emulator from it.
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
