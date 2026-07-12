# Breadwinner: Battle Link

Battle Link replaces Pokémon Red trainer move, item, and switch decisions with
a public HTTPS long-poll request. Wild battles, link battles, and forced actions
(including recharge, Bide, Thrash/Petal Dance, charging, Rage, trapping,
sleep, and freeze) remain native.

## Build

The build requires RGBDS (`rgbasm` and `rgblink`) and the local, uncommitted
`backend/roms/pokemon-red.gb` ROM matching CRC32 `9f7fdd53`.

```sh
python3 gameboy/tools/build_battle_link.py
```

This assembles `battle-link.asm`, verifies module-symbol and host-relocation
offsets against RGBDS output, refreshes the checked-in `battle-link.bin`,
applies the guarded manifest, and writes the locally excluded
`backend/mods/battle-link.gbmod` package. The source manifest verifies
the supported ROM size, CRC, zero-filled code range, and original bytes at all
five patch entrances.

## API protocol

Configure one public HTTPS URL from the Battle Link mod configuration screen.
New installations default to
`https://pokeboy.cameo.moe/battle-link/decision`; its live command console is
at `https://pokeboy.cameo.moe/battle-link`.
The WebView sends:

```text
GET <endpoint>?state=<base64url(JSON)>
```

The decoded version-1 state contains a random per-battle `battleId`, `turn`,
`attempt`, complete trainer and player party/active Pokémon data, current
battle conditions, and a `legalActions` array. Each legal action has a unique
integer `code` and descriptive move, item, or party-slot fields.

The endpoint keeps the request open until it can respond:

```json
{ "action": 18 }
```

Only a code offered in that request is accepted. HTTP failures, invalid JSON,
and illegal codes are retried until the original 30-second deadline. At the
deadline Battle Link chooses uniformly from the legal actions. The endpoint
must allow the app's WebView origin through CORS and support the encoded query
length.

While waiting, the ROM displays `AWAITING MOVE DECISION` and `B BACK`. Pressing
B aborts the request and returns to the player's battle menu; the reselected
turn retains its battle/turn identity and increments `attempt`.

## Action codes

Codes are negotiated by each request; consumers must choose from
`legalActions`, not synthesize arbitrary values. The current version uses
`0..3` for move slots, `16..21` for party switches, and `32..41` for supported
native trainer items. The descriptive fields are the source of truth so later
protocol versions can extend the code space safely.
