#!/usr/bin/env python3
"""Boot the image, run the emulator on /dev/fb0, capture the screen.

Unattended end-to-end check: root mounts, the ARM binary starts, the weights
load, frames reach the framebuffer. Drives the guest over serial and captures
through QEMU's monitor, so it needs no display.

Writes $WORK/emu/screen.ppm, transcript in $WORK/emu/screenshot.log.
"""
import os
import shutil
import socket
import subprocess
import sys
import time

WORK = os.environ.get("WORK", os.path.expanduser("~/buildroot-pokeboy"))
EMU = os.path.join(WORK, "emu")
IMAGES = os.path.join(WORK, "buildroot", "output", "images")
MON_PORT = int(os.environ.get("MON_PORT", "45454"))
BOOT_SECONDS = 16
RENDER_SECONDS = 45

for needed in (os.path.join(EMU, "zImage"), os.path.join(EMU, "data.vfat")):
    if not os.path.exists(needed):
        sys.exit(f"missing {needed} - run build-kernel.sh and make-data-disk.sh")

shutil.copyfile(os.path.join(IMAGES, "rootfs.ext4"), os.path.join(EMU, "rootfs-run.ext4"))
shot_host = os.path.join(EMU, "screen.ppm")
if os.path.exists(shot_host):
    os.remove(shot_host)

log = open(os.path.join(EMU, "screenshot.log"), "wb")
qemu = subprocess.Popen(
    ["docker", "run", "--rm", "-i", "-p", f"{MON_PORT}:{MON_PORT}",
     "-v", f"{EMU}:/work", "pokeboy-emu",
     "qemu-system-arm", "-M", "virt,highmem=off", "-cpu", "cortex-a15",
     "-m", "512", "-smp", "2", "-no-reboot",
     "-kernel", "/work/zImage",
     "-append", "root=/dev/vda rw rootfstype=ext4 rootwait panic=1 "
                "console=ttyAMA0,115200 init=/bin/sh",
     "-drive", "file=/work/data.vfat,format=raw,if=none,id=data",
     "-device", "virtio-blk-device,drive=data",
     "-drive", "file=/work/rootfs-run.ext4,format=raw,if=none,id=root",
     "-device", "virtio-blk-device,drive=root",
     "-device", "virtio-gpu-pci", "-device", "virtio-keyboard-pci", "-nic", "none",
     "-display", "none", "-serial", "stdio",
     "-monitor", f"tcp:0.0.0.0:{MON_PORT},server,nowait"],
    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)


def send(line, wait=1.0):
    qemu.stdin.write((line + "\n").encode())
    qemu.stdin.flush()
    time.sleep(wait)


time.sleep(BOOT_SECONDS)
send("mkdir -p /boot && mount -t vfat /dev/vdb /boot && echo BOOT-MOUNT-OK")
send("/usr/bin/pokeboy /boot/pokered-ai.gbc --weights /boot/pkai.weights "
     "--fb /dev/fb0 --input /dev/input/event0 --audio none &")
print(f"emulator started; rendering for {RENDER_SECONDS} s")
time.sleep(RENDER_SECONDS)

mon = socket.create_connection(("127.0.0.1", MON_PORT))
time.sleep(0.5)
mon.recv(65536)
mon.sendall(b"screendump /work/screen.ppm\n")
time.sleep(3)
mon.close()

send("poweroff -f", wait=6)
qemu.terminate()
log.close()

transcript = open(os.path.join(EMU, "screenshot.log"), "rb").read().decode(errors="replace")
for marker in ("BOOT-MOUNT-OK", "ai: model", "cannot"):
    for line in transcript.splitlines():
        if marker in line:
            print(line.strip())
            break

if os.path.exists(shot_host):
    print(f"screenshot: {shot_host} ({os.path.getsize(shot_host)} bytes)")
else:
    sys.exit("no screenshot captured - see screenshot.log")
