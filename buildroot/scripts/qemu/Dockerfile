# QEMU for ARM, user-mode and full-system, so testing the image needs no QEMU
# on the host.  docker build -t pokeboy-emu buildroot/scripts/qemu
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-user-static qemu-system-arm qemu-utils file e2fsprogs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
