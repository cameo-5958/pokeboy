#!/bin/bash
# Build a kernel qemu-system-arm can boot, into $WORK/emu/zImage.
#
# QEMU models no AM335x machine, so the device kernel will not boot in
# emulation. Same 6.1.46 source, multi_v7_defconfig plus virtio, DRM and evdev.
# Only the kernel and machine differ from the device; userspace is unchanged.
set -euo pipefail
WORK=${WORK:-$HOME/buildroot-pokeboy}
BR=$WORK/buildroot/output
SRC=$WORK/linux-qemu
EMU=$WORK/emu
TARBALL=$(ls "$WORK"/buildroot/dl/linux/linux-*.tar.gz | head -1)

export PATH="$BR/host/bin:$BR/host/sbin:$PATH"
export ARCH=arm CROSS_COMPILE=arm-buildroot-linux-gnueabihf-
# Host GCC 15 defaults to -std=gnu23, which breaks the 6.1 host tools.
[ -x "$WORK/gcc-gnu17" ] && export HOSTCC="$WORK/gcc-gnu17"

mkdir -p "$EMU"
if [ ! -d "$SRC" ]; then
    echo "=== extracting $TARBALL"
    mkdir -p "$SRC"
    tar xf "$TARBALL" -C "$SRC" --strip-components=1
fi
cd "$SRC"

echo "=== multi_v7_defconfig"
make multi_v7_defconfig >/dev/null

echo "=== enabling virtio, framebuffer and evdev as built-ins (no initramfs needed)"
for o in VIRTIO VIRTIO_MENU VIRTIO_MMIO VIRTIO_MMIO_CMDLINE_DEVICES VIRTIO_PCI \
         VIRTIO_BLK VIRTIO_NET VIRTIO_INPUT VIRTIO_CONSOLE \
         DRM DRM_VIRTIO_GPU DRM_FBDEV_EMULATION FB INPUT_EVDEV \
         EXT4_FS DEVTMPFS DEVTMPFS_MOUNT; do
    ./scripts/config --enable "$o"
done
make olddefconfig >/dev/null
for o in VIRTIO_MMIO VIRTIO_BLK VIRTIO_INPUT DRM_VIRTIO_GPU EXT4_FS; do
    grep -q "^CONFIG_$o=y" .config || echo "WARNING: CONFIG_$o is not built in"
done

echo "=== building zImage on $(nproc) cores"
make -j"$(nproc)" zImage
cp arch/arm/boot/zImage "$EMU/zImage"
ls -la "$EMU/zImage"
