# Mod sources

Each `*.mod.json` here is a declarative [gbmod](../tools/compile_mod.py) manifest —
sections, patches, symbols and relocations described as data, with code given as
raw gbz80 hex. There is no per-mod build script; the generic compiler turns any
manifest into a `.gbmod` package:

```sh
python3 gameboy/tools/compile_mod.py gameboy/mods/tradeback-npc.mod.json \
    backend/mods/tradeback-npc.gbmod
```

Omit the output path to write `<manifest>.gbmod` next to the source.

`battle-link.mod.json` can be compiled the same way from its checked-in RGBDS
binary. After changing `battle-link.asm`, first run
`python3 gameboy/tools/build_battle_link.py`; that refreshes the binary,
verifies all assembled symbol/host offsets, and emits the installable package.

ROM symbol references (`"reference": "rom"`) are resolved by name at apply time
against `backend/mods/pokemon-red.sym`, so the compiler itself needs only the
manifest.
