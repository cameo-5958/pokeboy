#!/bin/bash
# Boot the real root filesystem under qemu-system-arm, root shell on serial.
# --vnc also serves the framebuffer on localhost:5900. Quit with Ctrl-A X.
#
# At the prompt:
#   mkdir -p /boot && mount -t vfat /dev/vdb /boot
#   /usr/bin/pokeboy /boot/pokered-ai.gbc --weights /boot/pkai.weights \
#       --fb /dev/fb0 --input /dev/input/event0 --audio none
#
# Differs from the device: multi_v7 kernel, virtio disks (no /dev/mmcblk0p1, so
# S99pokeboy cannot autostart), virtio-gpu at 1280x800, no audio codec.
set -euo pipefail
WORK=${WORK:-$HOME/buildroot-pokeboy}
EMU=$WORK/emu
IMAGES=$WORK/buildroot/output/images
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

DISPLAY_ARGS="-display none -serial mon:stdio"
PORTS=""
if [ "${1:-}" = "--vnc" ]; then
    DISPLAY_ARGS="-display vnc=:0 -serial mon:stdio"
    PORTS="-p 5900:5900"
    echo "=== framebuffer on vnc://localhost:5900"
fi

[ -f "$EMU/zImage" ] || { echo "no $EMU/zImage - run build-kernel.sh"; exit 1; }
[ -f "$EMU/data.vfat" ] || "$HERE/make-data-disk.sh"

# Scratch copy, so a test run never writes to the build output.
cp -f "$IMAGES/rootfs.ext4" "$EMU/rootfs-run.ext4"

echo "=== booting; quit with Ctrl-A X"
# highmem=off keeps the PCIe ECAM window under 4 GiB, which a 32-bit kernel needs.
# The data disk is declared first so the root filesystem lands on /dev/vda.
exec docker run --rm -it $PORTS -v "$EMU":/work pokeboy-emu \
  qemu-system-arm -M virt,highmem=off -cpu cortex-a15 -m 512 -smp 2 -no-reboot \
    -kernel /work/zImage \
    -append "root=/dev/vda rw rootfstype=ext4 rootwait console=ttyAMA0,115200 init=/bin/sh" \
    -drive file=/work/data.vfat,format=raw,if=none,id=data \
    -device virtio-blk-device,drive=data \
    -drive file=/work/rootfs-run.ext4,format=raw,if=none,id=root \
    -device virtio-blk-device,drive=root \
    -device virtio-gpu-pci -device virtio-keyboard-pci -nic none \
    $DISPLAY_ARGS
