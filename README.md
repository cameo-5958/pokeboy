# Pokeboy

Game Boy handheld with its own emulator and a battle AI for Gen 1 Pokemon that runs on the device.
No ROMs in here, bring your own.

## What's where

- `gameboy/` - the C++ emulator core, tests, tools
- `emulator/` - frontends: linux (fbdev/evdev/tinyalsa), headless, web, win32
- `pkai/` - the on-device battle AI. Gen 1 damage math, feature builder, int8 inference, the ROM opcode hook
- `pred-patch/` - pokered with the $DB/$EB/$EC opcodes the AI hooks into
- `ai/` - simulator, training (imitation, then league PPO, then QAT), weight export, Showdown eval
- `buildroot/` - buildroot external tree for the device image (PocketBeagle / OSD3358)
- `hardware/` - KiCad project, fab exports, PCBWay files
- `app/` - React Native (Expo) app, iOS and Android
- `backend/` - API that stores and serves ROMs, mods and cartridge metadata
- `web/` - browser player shell

## Building

Emulator core, frontends and tests (needs cmake and a C++17 compiler):

```sh
cmake -S gameboy -B build -DCMAKE_BUILD_TYPE=Release -DPKAI_BUILD_ROM=OFF
cmake --build build -j
build/gbemu_headless yourgame.gb 1800 out.bmp    # runs 1800 frames, dumps the screen
```

Drop `-DPKAI_BUILD_ROM=OFF` to also rebuild the patched ROM, which needs
[rgbds](https://rgbds.gbdev.io/). To run the test suite, configure with
`-DBUILD_TESTING=ON` and run `ctest` in the build directory. Tests that need a
trained checkpoint skip when one is absent.

Patched ROM (needs rgbds and python3):

```sh
cd pred-patch && ./build_ai.sh          # -> pokered-ai.gbc, pokered-ai.sym
```

Web frontend core (needs emscripten):

```sh
emulator/web/build.sh                   # -> emulator/web/gbcore.{js,wasm}
```

AI toolchain (needs uv):

```sh
cd ai
uv sync --group dev
sim/build_engine.sh                     # libpkmn Gen I engine, downloads its own zig
scripts/build_pkai.sh                   # libpkai_c for the python bridge
uv run pytest
```

`ai/scripts/setup_external.sh` is only for the Showdown/metamon evaluation rig;
it clones two large repos and pulls team corpora, and nothing else needs it.

Device image (Buildroot, PocketBeagle / OSD3358):

```sh
buildroot/scripts/build-image.sh        # first run builds a toolchain, 1-2 hours
buildroot/scripts/rebuild-app.sh        # after editing gameboy/, pkai/ or emulator/
```

`buildroot/scripts/qemu/` emulates the result: `run-user.sh` for a quick
functional check of the ARM binary, `run-system.sh` for a full boot.

Backend and app:

```sh
cd backend && npm install && npm run dev   # localhost:4000
cd app && npm install && npm start         # expo, press i or a
```

The app talks to the backend at `EXPO_PUBLIC_API_URL`, default `http://localhost:4000`.
On a real phone set it to your LAN IP.
