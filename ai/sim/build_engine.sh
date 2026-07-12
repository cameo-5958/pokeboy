#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor
cd vendor

if [ ! -d engine ]; then
  git clone --depth 50 https://github.com/pkmn/engine.git
fi
cd engine

# Pin zig to the version the engine demands.
ZIG_VERSION=$(grep -oP 'minimum_zig_version\s*=\s*"\K[^"]+' build.zig.zon)
ZIG_DIR="../zig-${ZIG_VERSION}"
if [ ! -x "${ZIG_DIR}/zig" ]; then
  mkdir -p "${ZIG_DIR}"
  ok=""
  for ARCHIVE in "zig-x86_64-linux-${ZIG_VERSION}.tar.xz" "zig-linux-x86_64-${ZIG_VERSION}.tar.xz"; do
    for BASE in "https://ziglang.org/download/${ZIG_VERSION}" "https://ziglang.org/builds"; do
      if curl -fsSL "${BASE}/${ARCHIVE}" -o "/tmp/${ARCHIVE}"; then
        tar -xJf "/tmp/${ARCHIVE}" -C "${ZIG_DIR}" --strip-components=1
        ok=1; break 2
      fi
    done
  done
  [ -n "$ok" ] || { echo "failed to download zig ${ZIG_VERSION}" >&2; exit 1; }
fi
ZIG="$(readlink -f "${ZIG_DIR}/zig")"

"$ZIG" build -Doptimize=ReleaseFast -Dshowdown=true -Ddynamic=true

mkdir -p ../lib ../include
find zig-out -name 'libpkmn*' -exec cp {} ../lib/ \;
find . -name pkmn.h -path '*include*' -exec cp {} ../include/ \; -quit
[ -f ../include/pkmn.h ] || find . -name pkmn.h -exec cp {} ../include/ \; -quit

cd ../lib
LIBFILE=$(ls libpkmn*showdown*.so* 2>/dev/null | head -1 || true)
[ -n "$LIBFILE" ] || LIBFILE=$(ls libpkmn*.so* 2>/dev/null | head -1 || true)
[ -n "$LIBFILE" ] || { echo "no shared lib produced; check zig build options" >&2; ls -la; exit 1; }
if [ "$LIBFILE" != "libpkmn-showdown.so" ]; then cp "$LIBFILE" libpkmn-showdown.so; fi

cd ../engine
cat > ../../sim/mechanics_flags.json <<EOF
{
  "mode": "showdown",
  "engine_commit": "$(git rev-parse HEAD)",
  "zig_version": "${ZIG_VERSION}",
  "build_flags": ["-Doptimize=ReleaseFast", "-Dshowdown=true", "-Ddynamic=true"]
}
EOF
echo "OK: $(ls ../lib)"
