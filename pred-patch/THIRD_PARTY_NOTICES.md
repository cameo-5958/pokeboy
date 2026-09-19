# Third-party notice

This directory is the pret/pokered disassembly, <https://github.com/pret/pokered>,
with the Pokeboy AI patch on top: the `$DB`/`$EB`/`$EC` opcodes and
`tools/generate_ai.py`. Everything else is upstream work under pret's terms.

The patch lives mostly in `engine/battle/ai_protocol.asm`, with call sites in
`engine/battle/core.asm`, `trainer_ai.asm` and `used_move_text.asm`, and a few
bytes of state in `ram/wram.asm`. To see all of it:

```sh
git diff a41af38 HEAD -- pred-patch
```

`a41af38` is the untouched upstream import.

The patch itself is MIT, same as the rest of Pokeboy; see `LICENSE` in the
parent repository.

Pokemon is © Nintendo / Creature Inc. / GAME FREAK inc. No ROM is distributed
here; `build_ai.sh` assembles one from this source on your machine.
