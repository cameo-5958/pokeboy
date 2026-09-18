# Pokeboy

Game Boy handheld with its own emulator and a Gen 1 Pokemon battle AI that runs
on the device. No ROMs in here, bring your own.

## Layout

- `gameboy/` emulator core, tests, tools
- `emulator/` frontends: linux (fbdev/evdev/tinyalsa), headless, win32
- `pkai/` battle AI: damage math, features, int8 inference, ROM hook
- `pred-patch/` pokered with the $DB/$EB/$EC opcodes the AI hooks into
- `ai/` simulator, training, weight export, Showdown eval
- `buildroot/` external tree for the device image (PocketBeagle / OSD3358)
- `hardware/` KiCad project, fab exports
- `app/` React Native (Expo) app
- `backend/` API for ROMs, mods and cartridge metadata

## Build

Core (cmake, C++17):

```sh
cmake -S gameboy -B build -DCMAKE_BUILD_TYPE=Release -DPKAI_BUILD_ROM=OFF
cmake --build build -j
build/gbemu_headless game.gb 1800 out.bmp
```

`-DBUILD_TESTING=ON` adds the tests, `ctest` runs them. Dropping
`-DPKAI_BUILD_ROM=OFF` rebuilds the patched ROM too, which needs rgbds.

`pred-patch/` is a copy of a separate repository. Find patched pokered ROM
here: []

ROM, AI:

```sh
pred-patch/build_ai.sh
cd ai
uv sync --group dev && sim/build_engine.sh && scripts/build_pkai.sh
uv run python tools/dump_trainers.py     # trainer parties, needs the ROM
```

`ai/scripts/setup_external.sh` is for the Showdown and metamon eval rig only.

Device image:

```sh
buildroot/scripts/build-image.sh         # first run, 1-2 hours
buildroot/scripts/rebuild-app.sh         # after source changes
buildroot/scripts/qemu/run-system.sh     # boot it under QEMU
```

Backend and app:

```sh
cd backend && npm install && npm run dev   # :4000
cd app && npm install && npm start
```

`EXPO_PUBLIC_API_URL` points the app at the backend, default
`http://localhost:4000`. Use your LAN IP from a phone.

## License

MIT, see `LICENSE`. `pred-patch/` is the pret/pokered disassembly under its own
terms, see `pred-patch/THIRD_PARTY_NOTICES.md`.
