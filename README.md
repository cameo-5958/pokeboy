# Pokeboy

A Game Boy emulator platform. The client is a React Native (Expo) app; a backend
stores and serves ROMs, mods, and cartridge metadata. This replaces the old
static `web/` app.

## Layout

| Path       | What it is                                                              |
| ---------- | ---------------------------------------------------------------------- |
| `app/`     | The local React Native app (Expo). Builds for iOS and Android.         |
| `backend/` | The backend that stores and serves ROMs, mods, and cartridge metadata. |
| `gameboy/` | The native Game Boy emulator core and frontends.                       |
| `web/`     | Legacy static web emulator (being replaced by `app/`).                 |

The C++ core supports precompiled, dynamically linked ROM/ASM/TypeScript mods.
See [`gameboy/MODS.md`](gameboy/MODS.md) for the package format, linker ABI, and
performance contract.

## Getting started

`app/` and `backend/` are independent Node projects, each with its own `package.json`.

```sh
# Backend
cd backend
npm install
npm run dev            # starts the API on http://localhost:4000

# App (in a second terminal)
cd app
npm install
npm start              # Expo dev server; press i / a for iOS / Android
```

The app reads the backend URL from the `EXPO_PUBLIC_API_URL` env var and falls
back to `http://localhost:4000`. Point it at your machine's LAN IP when running
on a physical device, e.g. `EXPO_PUBLIC_API_URL=http://192.168.1.20:4000 npm start`.
