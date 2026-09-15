#!/usr/bin/env bash
# Builds the web frontend's core: emulator/web/gbcore.js + gbcore.wasm, which
# index.html loads via createGBCore(). Without these two files the page boots
# straight into its "Core missing" error, which is the state the old top-level
# web/ directory was left in.
#
# This is deliberately NOT app/scripts/build-emulator.bat: that one is Windows
# only, and it emits gbcore.bin (renamed so Metro treats the glue as an asset
# instead of executing it) into app/assets/emulator/ for the native app. The web
# page wants ordinary .js next to itself. The two builds share the core sources
# and the export list below, so keep the lists in step when the ABI changes.
#
# Requires emscripten on PATH (`emcc`), or EMSDK pointing at an emsdk checkout.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"

if ! command -v emcc >/dev/null 2>&1; then
    if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
        # shellcheck disable=SC1091
        source "$EMSDK/emsdk_env.sh" >/dev/null 2>&1
    fi
fi
if ! command -v emcc >/dev/null 2>&1; then
    echo "error: emcc not found. Install emscripten or set EMSDK to an emsdk checkout." >&2
    exit 1
fi

cd "$root"

CORE=(
    gameboy/core/gb.cpp
    gameboy/core/bus/bus.cpp
    gameboy/core/cpu/cpu.cpp
    gameboy/core/ppu/ppu.cpp
    gameboy/core/timer/timer.cpp
    gameboy/core/apu/apu.cpp
    gameboy/core/cart/cart.cpp
    pkai/hook.cpp
    pkai/tracker.cpp
    pkai/observe.cpp
    pkai/mask.cpp
    pkai/scheduler.cpp
    pkai/calc.cpp
    pkai/features.cpp
    pkai/event.cpp
    pkai/weights.cpp
    pkai/model.cpp
    pkai/kernels/gemm.cpp
    pkai/kernels/attention.cpp
    pkai/kernels/gru.cpp
    gameboy/adapter/gb_api.cpp
)

# Every symbol index.html cwraps. gb_save_state/gb_load_state are what let the
# page offer state slots; malloc/free are needed to hand buffers across.
EXPORTS='_gb_create,_gb_destroy,_gb_load_rom,_gb_reset,_gb_reset_post_boot,'\
'_gb_run_frame,_gb_framebuffer,_gb_framebuffer_argb,_gb_set_input,'\
'_gb_read_audio,_gb_has_battery,_gb_save_ram,_gb_load_save_ram,'\
'_gb_save_state,_gb_load_state,_gb_rom_title,_gb_read_mem,_gb_write_mem,'\
'_gb_set_audio_mask,_gb_ai_step,_malloc,_free'

emcc "${CORE[@]}" \
    -O2 -std=c++17 \
    -I . -I gameboy/core -I gameboy/adapter \
    -sMODULARIZE=1 -sEXPORT_NAME=createGBCore -sENVIRONMENT=web \
    -sALLOW_MEMORY_GROWTH=1 \
    -sALLOW_TABLE_GROWTH=1 \
    -sEXPORTED_FUNCTIONS="$EXPORTS" \
    -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,addFunction,removeFunction,HEAPU8,HEAPF32,HEAPU32 \
    -o emulator/web/gbcore.js

echo "Built emulator/web/gbcore.js and emulator/web/gbcore.wasm"
