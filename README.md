# Pokeboy

Game Boy handheld with its own emulator and a Gen 1 Pokemon battle AI that runs
on the device. No ROMs in here, bring your own.

## How the AI works

Trainers are driven by a small transformer with a GRU on top. It is integer
only: int8 weights, int16 hidden state, no floats on the device.

The patched ROM runs an unused opcode (`$DB`, `$EB`, `$EC`) wherever the game
would normally pick a trainer's move. The emulator traps it, reads the battle
out of WRAM, runs the model and writes the choice back.

One decision is too slow for a single frame on the Cortex-A8, so the model is
cut into small steps (one GEMM block, one attention head, one GRU step) and the
scheduler fits them in between frames. `ai/models/pep_int.py` is the reference
and the C++ in `pkai/` has to match it bit for bit. There is a test for that.

## Layout

- `gameboy/` emulator core, tests, tools
- `emulator/` frontends: linux, headless, win32
- `pkai/` battle AI
- `pred-patch/` pokered with the opcodes the AI hooks into
- `ai/` simulator, training, weight export
- `buildroot/` device image (PocketBeagle / OSD3358)
- `hardware/` KiCad project
- `app/` Expo app
- `backend/` API for ROMs, mods and cartridge metadata

## Build

```sh
# core, C++17
cmake -S gameboy -B build -DCMAKE_BUILD_TYPE=Release -DPKAI_BUILD_ROM=OFF
cmake --build build -j
build/gbemu_headless game.gb 1800 out.bmp

# patched ROM (needs rgbds) and AI
pred-patch/build_ai.sh
cd ai && uv sync --group dev && sim/build_engine.sh && scripts/build_pkai.sh

# device image
buildroot/scripts/build-image.sh
buildroot/scripts/qemu/run-system.sh

# backend (:4000) and app
cd backend && npm install && npm run dev
cd app && npm install && npm start
```

Most of the `pkai` tests skip themselves until the patched ROM and the weights
have been built.

## License

MIT, see `LICENSE`. `pred-patch/` is the pret/pokered disassembly under its own
terms, see `pred-patch/THIRD_PARTY_NOTICES.md`.
