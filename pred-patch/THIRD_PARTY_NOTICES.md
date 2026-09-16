# Third-party notice

`pred-patch/` is a copy of the **pret/pokered** disassembly of Pokémon Red and
Blue, <https://github.com/pret/pokered>, with the Pokeboy AI patch applied on
top (the `$DB`/`$EB`/`$EC` opcodes the emulator's battle AI hooks into, plus the
generator in `tools/generate_ai.py`). Everything that is not part of that patch
is upstream work by the pret contributors.

The disassembly is source that reproduces the original game's machine code; the
underlying game is © Nintendo / Creature Inc. / GAME FREAK inc. No ROM is
distributed here. Building `pokered-ai.gbc` produces a ROM from this source on
your own machine.

Upstream documentation (`README.md`, `INSTALL.md`) ships with the pret
repository and is not reproduced in this tree; see the link above for build
prerequisites and the project's own terms.
