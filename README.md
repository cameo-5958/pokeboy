# gm-emu monorepo

```
gameboy/     C++ Game Boy emulator core + native builds
web/         web frontend (WASM build of the core)
pred-patch/  pokered disassembly patch exposing battle AI hooks
ai/          (reserved)
```

## gameboy

A small, dependency-free Game Boy (DMG) emulator written in C++17. The core is a
plain library exposed through a C ABI, with a native Win32 frontend and a
headless test harness. It's structured so the same core can be bridged to iOS.

## Features

- Cycle-based CPU, PPU, timer, APU, and joypad
- MBC cartridges with battery-backed saves (`.sav`)
- 160×144 output, 4-shade Game Boy palette (color is chosen by the frontend)
- 44.1 kHz stereo audio
- C ABI (`adapter/gb_api.h`) for embedding in any frontend

## Layout

```
gameboy/core/       emulator core
gameboy/adapter/    gb_api.h (C ABI over C++ core)
gameboy/frontend/   basic windows frontend
gameboy/ios/        planned iOS frontend

gameboy/build.bat   MSVC build script
```

## Building

Requires MSVC (Visual Studio Build Tools). From `gameboy/`:

```bat
build.bat
```

Produces two executables in `build/`:

- `build\gbemu.exe` (Win32 emulator)
- `build\gbemu_headless.exe` (headless test harness)

## Running

Launch the Win32 app and open a ROM:

```bat
build\gbemu.exe "path\to\game.gb"
```

Battery saves are written next to the ROM as a `.sav` file and reloaded automatically on the next launch.

## Self-embeding the core

Every frontend talks to the emulator through the C ABI in `adapter/gb_api.h`:

```c
#include "adapter/gb_api.h"
```

To create an instance, allocate memory for the ROM, then passing that to the gb_handle:

```c
gb_handle* gb = gb_create();
gb_load_rom(gb, rom, len);

// Free the memory after you're done with it
free(rom);
```

Here's a sample tick frame that you can run:

```c
gb_set_input(gb, buttons, dpad);   // two 4-bit masks, 1 = held
gb_run_frame(gb);                  // exactly one video frame

uint32_t pixels[GB_SCREEN_W * GB_SCREEN_H];
uint32_t palette[4] = { 0xFFFFFFFF, 0xFFAAAAAA, 0xFF555555, 0xFF000000 };
gb_framebuffer_argb(gb, pixels, palette);   // blit these

float audio[1024 * 2];
int n = gb_read_audio(gb, audio, 1024);     // stereo floats @ 44100 Hz
```

Once you're done, de-allocate the instance:

```c
gb_destroy(gb);
```

C++ users can instead use the `GameBoy` class directly from `core/core.h`.