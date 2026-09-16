# Emulation harness: QEMU for ARM, both user-mode and full-system.
# Containerised so testing the image needs no QEMU installed on the host.
#   docker build -t pokeboy-emu buildroot/scripts/qemu
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-user-static qemu-system-arm qemu-utils file e2fsprogs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
