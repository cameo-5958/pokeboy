# Pokeboy

Pokeboy is a Game Boy emulator platform. An Expo app hosts the WebAssembly
emulator, while an Express backend serves the cartridge registry, ROMs, mods,
symbols, and Battle Link services.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`app/`](app/) | Expo/React Native app and its bundled emulator assets |
| [`backend/`](backend/) | Express API, content registry, telemetry, and Battle Link services |
| [`gameboy/`](gameboy/) | C++17 emulator core, mod ABI, tests, and mod tools |
| [`emulator/`](emulator/) | Headless, Win32, and standalone web frontends |
| [`docs/`](docs/) | Architecture, setup, repository ownership, and release/versioning guidance |

Start with the [documentation index](docs/README.md), or follow the
[local development guide](docs/local-development.md) to run a component.

The mod package format and linker ABI are documented separately in
[`gameboy/MODS.md`](gameboy/MODS.md).
