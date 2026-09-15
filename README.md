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

Emulator core:

```sh
cmake -S gameboy -B build && cmake --build build
```

AI toolchain (needs uv):

```sh
cd ai
uv sync
scripts/setup_external.sh
scripts/build_pkai.sh
```

Backend and app:

```sh
cd backend && npm install && npm run dev   # localhost:4000
cd app && npm install && npm start         # expo, press i or a
```

The app talks to the backend at `EXPO_PUBLIC_API_URL`, default `http://localhost:4000`.
On a real phone set it to your LAN IP.
