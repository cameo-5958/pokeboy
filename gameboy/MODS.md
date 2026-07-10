# Runtime mod linking

Pokeboy loads precompiled Game Boy mods without rebuilding the emulator or the
base ROM. A mod package contains guarded ROM patch bytes, relocatable Game Boy
machine-code sections, module exports, ROM/host imports, relocations, and opaque
metadata for the frontend.

The runtime deliberately does not embed a JavaScript engine. Native and WASM
frontends register the same C callback ABI; the WASM frontend can use
`app/src/mods/runtime.ts` to bind those imports to a TypeScript object.

## Runtime flow

1. Load the pristine ROM with `gb_load_rom`. Loading another ROM clears the
   symbol document and all active mods.
2. Load that ROM's RGBDS/pret `.sym` document with `gb_mod_load_symbols`.
   Records use the standard `BB:AAAA SymbolName` form. Blank lines and `;`
   comments are accepted.
3. Register a synchronous host callback with `gb_mod_set_host_callback`, or
   create a `GbModRuntime` in TypeScript.
4. Pass each compiled `.gbmod` payload to `gb_mod_load`. The loader returns a
   stable nonzero handle and atomically installs the newly linked ROM image.
5. Run the emulator normally. Patch code executes as ordinary Game Boy code,
   linked module code executes as ordinary Game Boy code, and only a linked
   host trap crosses into TypeScript.
6. Call `gb_mod_unload` while emulation is paused at a known-safe point. The
   runtime rebuilds the remaining overlay from the pristine ROM, so unloading
   never depends on mod order or reverse-patch logic.

Loading, unloading, or replacing symbols either commits a complete linked image
or leaves the current image untouched. Package changes are rejected from inside
a host callback. A frontend must not unload a mod while the emulated PC or stack
is inside that mod; reset first or arrange a game-level safe point.

## Link model

ROM symbols carry a bank and CPU address. Their file offsets follow RGBDS rules:

- bank 0 addresses `$0000-$3fff` map directly;
- bank N addresses `$4000-$7fff` map to
  `N * $4000 + (address - $4000)`.

Module sections have one of three placements:

- `FIXED`: an explicit bank and address;
- `SYMBOL`: a ROM symbol plus a signed, same-bank addend;
- `APPEND`: aligned allocation beginning in the first bank after the pristine
  ROM. Multiple appended sections are packed in load order.

Fixed/symbol sections may request a fill-byte check before being installed.
Appended ROMs are padded to a power-of-two bank count, subject to the active
MBC's limit. The linker updates ROM-size and header/global checksum fields.
Mods cannot change the mapper or RAM-size header fields.

Patches target a fixed address or ROM symbol and can include expected original
bytes. Every mod should provide both a target ROM size/CRC32 and expected patch
bytes. These checks catch different revisions of the same game before any byte
is changed. Any overlap between active patch/section ranges is a hard conflict.

`CALL16` is the checked direct-call relocation. It permits a target in ROM0 or
in the same switched bank as the call site. A call from ROM0 into ROMX is
rejected because the linker cannot prove which bank is selected. For a far
call, the module must perform the mapper switch explicitly and use `BANK8` or
`BANK16` together with `ABS16`.

## TypeScript host calls

An ASM module emits an otherwise-illegal three-byte instruction and a `HOST16`
relocation over its operand:

```asm
    db $d3
    dw 0 ; HOST16 relocation -> a declared host import
```

The linker writes a compact 16-bit dispatch slot and registers the exact ROM
file offset as a trap. Opcode `$d3` anywhere else keeps the core's old illegal
opcode/NOP behavior and does not consume the following two bytes.

The callback receives the mod handle, package-local import index, and mutable
`AF`, `BC`, `DE`, `HL`, `SP`, `PC`, `IME`, and halted state. It may also use
`gb_read_mem`/`gb_write_mem`. The updated registers are restored before the next
Game Boy instruction; the low nibble of `F` is always cleared.

TypeScript cores are plain name-to-function maps:

```ts
const core = {
  "typescript.tick": (cpu, memory) => {
    const counter = memory.read8(0xc100);
    memory.write8(0xc100, counter + 1);
    cpu.a = counter;
  },
};

const mods = new GbModRuntime(emscriptenModule, gbHandle);
mods.loadSymbols(symbolText);
const loaded = mods.loadPackage(packageBytes, core);
```

Import names are resolved once at package load. A trap dispatch is a numeric
handle lookup followed by an array lookup. Host functions must be synchronous;
`Promise`-based work cannot suspend the emulated CPU. The wrapper captures
exceptions inside a WASM callback and exposes them through
`throwPendingHostError`, which should be checked after a frame.

## `.gbmod` v1 binary format

All integers are little-endian. Table/blob offsets are absolute package offsets.
String fields are offsets relative to the string table and must point to a NUL-
terminated UTF-8 string. `0xffffffff` is the optional-string sentinel.

### Header (96 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u8[8]` | `GBMOD1\r\n` |
| 8 | `u16` | format version (`1`) |
| 10 | `u16` | host ABI version (`1`) |
| 12 | `u32` | header size (at least `96`) |
| 16 | `u32` | total package size |
| 20 | `u32` | package flags (reserved in v1) |
| 24 | `u32` | pristine target ROM CRC32; zero disables the check |
| 28 | `u32` | pristine target ROM byte size; zero disables the check |
| 32 | `u32` | required mod-id string |
| 36 | `u32` | optional display-name string |
| 40, 44 | `u32, u32` | opaque metadata offset and size |
| 48, 52 | `u32, u32` | string-table offset and size |
| 56, 60 | `u32, u32` | import-table offset and record count |
| 64, 68 | `u32, u32` | section-table offset and record count |
| 72, 76 | `u32, u32` | patch-table offset and record count |
| 80, 84 | `u32, u32` | module-symbol-table offset and record count |
| 88, 92 | `u32, u32` | relocation-table offset and record count |

Metadata is intentionally opaque to the C++ linker. JSON is recommended for
frontend-facing version, dependency, author, permission, and configuration data.

### Import record (8 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | required import-name string |
| 4 | `u32` | flags (reserved in v1) |

### Section record (32 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | section-name string |
| 4 | `u32` | machine-code blob offset |
| 8 | `u32` | blob size; must be `1..$4000` |
| 12 | `u32` | anchor-symbol string or `0xffffffff` |
| 16 | `i32` | anchor addend |
| 20 | `u16` | fixed CPU address |
| 22 | `u16` | fixed ROM bank (`0..$1ff`) |
| 24 | `u16` | power-of-two alignment; zero means one |
| 26 | `u8` | placement: `0=FIXED`, `1=SYMBOL`, `2=APPEND` |
| 27 | `u8` | flags; bit 0 enables fill verification |
| 28 | `u8` | required fill byte when flag bit 0 is set |
| 29 | `u8[3]` | reserved, zero |

### Patch record (32 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | replacement blob offset |
| 4 | `u32` | replacement size; nonzero |
| 8 | `u32` | expected-original blob offset |
| 12 | `u32` | expected size: zero or replacement size |
| 16 | `u32` | target-symbol string or `0xffffffff` |
| 20 | `i32` | target addend |
| 24 | `u16` | fixed CPU address |
| 26 | `u16` | fixed ROM bank |
| 28 | `u8` | target: `0=FIXED`, `1=SYMBOL` |
| 29 | `u8[3]` | reserved, zero |

### Module symbol record (16 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u32` | export-name string |
| 4 | `u32` | section index |
| 8 | `u32` | byte offset within section |
| 12 | `u32` | flags (reserved in v1) |

### Relocation record (24 bytes)

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `u8` | target: `0=SECTION`, `1=PATCH` |
| 1 | `u8` | relocation type (below) |
| 2 | `u8` | reference: `0=ROM_SYMBOL`, `1=MODULE_SYMBOL`, `2=HOST_IMPORT` |
| 3 | `u8` | flags (reserved in v1) |
| 4 | `u32` | target section/patch index |
| 8 | `u32` | write offset within target blob |
| 12 | `u32` | ROM string offset, module-symbol index, or import index |
| 16 | `i32` | reference addend |
| 20 | `u32` | reserved, zero |

Relocation types are:

| Value | Name | Result |
| ---: | --- | --- |
| 0 | `ABS8` | low 8-bit CPU address; range checked |
| 1 | `ABS16` | 16-bit CPU address |
| 2 | `BANK8` | 8-bit ROM bank |
| 3 | `REL8` | signed PC-relative byte; same bank and range checked |
| 4 | `CALL16` | 16-bit address attached to a Game Boy `CALL`; bank checked |
| 5 | `HOST16` | 16-bit host slot attached to opcode `$d3` |
| 6 | `BANK16` | 9-bit-capable ROM bank in a 16-bit field |

The fixture builders in `tests/mod_tests.cpp` are executable examples of v1
package construction and link behavior.

## Performance contract

- Package parsing, CRC checks, placement, conflict detection, relocation, ROM
  growth, and checksums happen only when the mod set changes.
- The normal `run_frame`, bus read/write, cartridge read, PPU, timer, and APU
  paths contain no mod polling, callback, hash lookup, or overlay lookup.
- The CPU adds one case for illegal opcode `$d3`. A sparse trap-table lookup is
  performed only if that opcode actually executes.
- TypeScript runs only for an explicit host import. Prefer coarse host calls and
  pass data in registers/WRAM; do not use host calls for per-pixel or per-cycle
  work.

This preserves unmodded and ASM-only mod performance while making the cost of a
TypeScript transition explicit and proportional to how often the mod requests it.
