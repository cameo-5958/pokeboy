# Pokeboy

A Game Boy handheld built around a custom emulator, with an on-device battle AI
for Gen I Pokémon. ROMs are not included.

## Layout

| Path          | What it is                                                                 |
| ------------- | -------------------------------------------------------------------------- |
| `gameboy/`    | C++ Game Boy emulator core, tests and tools.                               |
| `emulator/`   | Frontends: Linux device (fbdev/evdev/tinyalsa), headless, web, win32.      |
| `pkai/`       | On-device battle AI runtime: Gen I damage math, featurizer, int8 inference, ROM opcode hook. |
| `pred-patch/` | pokered with the `$DB`/`$EB`/`$EC` battle-AI opcode protocol.              |
| `ai/`         | Simulator, training pipeline (imitation → league PPO → QAT), weight export, Showdown evaluation. |
| `buildroot/`  | Buildroot external tree for the device image (PocketBeagle / OSD3358).     |
| `hardware/`   | KiCad design, fabrication exports and PCBWay manufacturing files.          |
| `app/`        | React Native (Expo) client for iOS and Android.                            |
| `backend/`    | API that stores and serves ROMs, mods and cartridge metadata.              |
| `web/`        | Browser player shell.                                                      |

## Getting started

```sh
# Emulator core
cmake -S gameboy -B build && cmake --build build

# Battle AI toolchain
cd ai && uv sync && scripts/setup_external.sh && scripts/build_pkai.sh

# Backend and app
cd backend && npm install && npm run dev      # http://localhost:4000
cd app && npm install && npm start            # Expo; i / a for iOS / Android
```

The app reads the backend URL from `EXPO_PUBLIC_API_URL` (default
`http://localhost:4000`); use your LAN IP on a physical device.
