# Local development

Run commands from the repository root unless a section changes directories.
Do not commit ROMs, saves, generated native projects, or build directories.

## Prerequisites

- Git, Node.js 20, and npm
- Python 3 for mod packaging tools
- CMake 3.20 or newer and a C++17 compiler for the portable core
- Emscripten (`emcc`) only when rebuilding a browser or app WebAssembly core
- RGBDS (`rgbasm` and `rgblink`) only when rebuilding Battle Link assembly
- For native app builds: Xcode and CocoaPods on macOS, or a JDK and Android SDK
  for Android

## Backend

The backend defaults to port `4000` and local content. Place an authorized ROM
at the filename named by `backend/data/registry.json` (for the current catalog,
`backend/roms/pokemon-red.gb`). Then run:

```sh
cd backend
npm ci
npm run dev
```

Verify it in another terminal:

```sh
curl http://localhost:4000/health
curl http://localhost:4000/api/registry
```

Authentication is open until the first device key is created. Use
`npm run key:new -- "local device"` inside `backend/` when testing the keyed
flow. See [`backend/README.md`](../backend/README.md) for all environment
variables, content-source behavior, and API routes.

## Expo app

Start the backend first, then:

```sh
cd app
npm ci
EXPO_PUBLIC_API_URL=http://localhost:4000 npm start
```

The URL must be reachable from the selected runtime. A simulator on the same
machine can generally use loopback; a physical device needs an HTTPS endpoint
or the development machine's reachable LAN address. The backend URL and API key
can also be changed in Settings.

Useful checks from `app/` are:

```sh
npm run typecheck
npm run lint
npm run test:battle-link
```

Expo generates `app/ios/` and `app/android/` during prebuild. They are local
build products, not version authorities, and should not be hand-edited for a
release version.

## Portable core and tests

The CMake project produces the headless frontend and the automated core tests:

```sh
cmake -S gameboy -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

The package integration executables are built but are intentionally not normal
CTest entries because they need a local ROM, symbol file, and `.gbmod` package.
The Win32 GUI is added only on Windows. On Windows, `gameboy/build.bat` is an
MSVC-specific convenience wrapper that also builds the GUI and test binaries.

## Standalone web emulator

Install and activate Emscripten, or set `EMSDK` to an Emscripten SDK checkout.
Then generate the web frontend's core:

```sh
./emulator/web/build.sh
mkdir -p emulator/web/roms
# Copy an authorized ROM to the filename declared in emulator/web/carts.json.
cd emulator/web
python3 -m http.server 8080
```

Open `http://localhost:8080`. Do not open `index.html` directly: the page fetches
`carts.json`, ROM bytes, and the generated WebAssembly files over HTTP.

## Rebuilding app emulator assets

The tracked files in `app/assets/emulator/` are part of the installed binary.
`app/scripts/build-emulator.bat` rebuilds the Emscripten glue and WASM on
Windows. Put `emcc` on `PATH`, or set `EMSDK` to the local SDK before running it.

Any adapter export or generated WASM change must be reviewed together with
`app/assets/emulator/embed.html` and the mod host interface. Rebuilding a
bundled asset is an app change and therefore requires a new app version and a
new IPA.

## Mod packages

A manifest containing prebuilt bytes can be packaged with Python alone:

```sh
python3 gameboy/tools/compile_mod.py gameboy/mods/tradeback-npc.mod.json \
  backend/mods/tradeback-npc.gbmod
```

Battle Link also needs RGBDS and the matching local ROM:

```sh
python3 gameboy/tools/build_battle_link.py
```

Before distributing a package, follow the synchronized version checklist in
[Builds, releases, and versions](release-versioning.md).
