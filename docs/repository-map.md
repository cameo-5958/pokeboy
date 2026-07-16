# Repository map

Responsibility follows component boundaries rather than a single top-level
build. The app and backend are independent Node projects; the emulator core is
a CMake C++ project shared by several frontends.

| Area | Responsibility | Important boundaries |
| --- | --- | --- |
| `app/app/` | Expo Router screens and application UI | `app/app.json` supplies the installed build version |
| `app/src/` | API client, settings, emulator asset loading, mod runtime, theme, and audio helpers | Runtime backend URL and device key come from settings |
| `app/assets/emulator/` | WebView document, Emscripten glue/WASM, and mod host snapshot bundled in the IPA | Generated core assets must remain compatible with the C++ adapter exports |
| `backend/src/routes/` | HTTP API resources, Battle Link, telemetry, and development controls | `/api/*` uses device-key authentication after keys are configured; Battle Link has public routes by design |
| `backend/src/content.ts` and `storage.ts` | Local/remote content abstraction and registry record lookup | ROMs are local-only; repository credentials are backend-only |
| `backend/data/registry.json` | Cache and release identity for ROMs, mods, and host core | Version changes must accompany changed release bytes |
| `backend/roms/`, `backend/mods/` | Locally served ROMs, symbols, and packaged mods | ROMs and generated packages stay local; selected `.sym` and `.gbmod` payloads are tracked |
| `backend/src/discord/` | Discord gateway, REST client, and Battle Link bot manager | Bot token belongs on the server |
| `gameboy/core/` | CPU, PPU, APU, bus, cartridge, timer, state, and mod execution | Portable C++17 library |
| `gameboy/adapter/` | C ABI exported to native and WebAssembly frontends | ABI changes require matching generated app/web assets |
| `gameboy/tests/` | Core and mod tests plus manual package integration harnesses | Some package tests require a local ROM and symbols |
| `gameboy/tools/` and `gameboy/mods/` | Mod compiler, assembly builder, manifests, and source payloads | Battle Link assembly requires RGBDS and the matching local ROM |
| `emulator/headless/` | Portable command-line frontend | Built by the CMake project |
| `emulator/web/` | Standalone browser frontend and Emscripten build | Generated `gbcore.js`/`gbcore.wasm` and ROM files are local build/runtime inputs |
| `emulator/win32/` | Native Windows GUI frontend | Windows APIs and MSVC libraries make this frontend Windows-only |
| `.github/workflows/ios-build.yml` | Unsigned iOS packaging | Artifact identity is source SHA plus `app/app.json` version |

## Portability boundaries

- `gameboy/build.bat` and the Win32 frontend require Windows with Visual Studio
  C++ build tools. Other hosts should use CMake for the headless target.
- `app/scripts/build-emulator.bat` is a Windows Emscripten entry point. Its
  `EMSDK` value is a caller-supplied machine setting; a checked-out SDK location
  is not a repository dependency.
- `emulator/web/build.sh` is the shell-based Emscripten entry point and accepts
  either `emcc` on `PATH` or an `EMSDK` environment variable.
- `backend/scripts/setup-battle-link-server.sh` targets a Linux host managed by
  systemd and nginx. It is a deployment helper, not a cross-platform local
  setup script.
- Private-repository content mode is a server deployment option. It requires a
  backend-only token and a compatible raw-content endpoint.
- The telemetry MCP listener's configured address may be a private Tailscale
  address. Override `TELEMETRY_MCP_BIND` for another tailnet or use loopback for
  local development; the backend already falls back to loopback if the bind
  address is unavailable.
