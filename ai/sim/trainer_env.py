"""Trainer-seat environment over libpkmn.

The enemy trainer seat sees the battle through a pkai.Observation (public
information about the player, full information about its own party) and acts
through the 16-way head: 4 move slots, 6 party slots, 6 item classes. Items
follow the native trainer semantics (spec §9.1): one item class per trainer
class, a per-send-out use count, effects applied to the libpkmn buffer in the
trainer's ordered slot while the engine executes only the player's move
(trainer choice = PASS). Enemy PP never decrements, as on the cartridge.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from sim import engine
from sim.battle import _ORDER_OFF, _POKE, _SIDE, Battle
from sim.engine import MOVE, PASS, RESULT_NONE, SWITCH, RawBattle
from sim.gen1data import MOVES
from sim.pack import PokemonSpec
from sim.pkai_bridge import (FULL_HEAL, FULL_RESTORE, GUARD_SPEC, HEAL_AMOUNT, ITEM_SLOT, ROM_TYPE, X_ATTACK, X_DEFEND,
                             X_SPEED, Bridge, bridge, pkai)

_ACTIVE_OFF = 144
_SEED_OFF = 376
MOVES_BY_ID = {v[0]: k for k, v in MOVES.items()}
# StatModifierRatios (numerator, denominator) for stages 1..13.
_RATIOS = [(25, 100), (28, 100), (33, 100), (40, 100), (50, 100), (66, 100), (1, 1), (15, 10), (2, 1), (25, 10), (3, 1), (35, 10), (4, 1)]
EVENT_DIM = 64


def public_status(raw: int) -> int:
    return 1 if raw & 7 else raw & 0xF8


def _u16(buf: bytes, o: int) -> int:
    return buf[o] | (buf[o + 1] << 8)


def _i4(v: int) -> int:
    return v - 16 if v >= 8 else v


@dataclass
class Snapshot:
    """Last public view of a player Pokémon (taken while it was on the field)."""
    species: int = 0
    level: int = 0
    hp: int = 0
    max_hp: int = 0
    status: int = 0
    types: tuple[int, int] = (0, 0)
    round: int = 0


@dataclass
class TrainerEnv:
    trainer_specs: list[PokemonSpec]
    trainer_class: int
    player_specs: list[PokemonSpec]
    seed: int
    trainer_side: int = 1
    rng_seed: int = 0
    br: Bridge = field(default_factory=bridge)

    def __post_init__(self) -> None:
        teams = [None, None]
        teams[self.trainer_side] = self.trainer_specs
        teams[1 - self.trainer_side] = self.player_specs
        self.b = Battle(teams[0], teams[1], self.seed)
        self.me, self.opp = self.trainer_side, 1 - self.trainer_side
        if self.trainer_class == 0:   # no trainer class: no item candidates (OU / player-like seat)
            self.item, self.item_divisor, self.item_status = None, 0, 0
            self.count_max = 0
        else:
            self.item, self.item_divisor, self.item_status = self.br.class_item(self.trainer_class)
            self.count_max = self.br.class_item_count(self.trainer_class)
        self.count = self.count_max
        self.rng = random.Random(self.rng_seed)
        self.round = 0
        self.last_active = self._active_ix(self.me)
        self.snapshots: dict[int, Snapshot] = {}
        self.last_event = [0.0] * EVENT_DIM
        self._refill_pp()
        self._observe_player()

    @property
    def buf(self) -> bytes:
        return self.b.raw.bytes

    def _order(self, side: int) -> bytes:
        o = _SIDE[side] + _ORDER_OFF
        return self.buf[o:o + 6]

    def _active_ix(self, side: int) -> int:
        first = self._order(side)[0]
        return first - 1 if first else 0

    def _slot(self, side: int, ix: int) -> int:
        return _SIDE[side] + ix * _POKE

    def _active(self, side: int) -> int:
        return _SIDE[side] + _ACTIVE_OFF

    def _volatiles(self, side: int) -> int:
        o = self._active(side) + 16
        return int.from_bytes(self.buf[o:o + 8], "little")

    def _write(self, off: int, data: bytes) -> None:
        raw = self.b.raw._buf
        raw[off:off + len(data)] = data

    def _refill_pp(self) -> None:
        """Enemy trainer PP never decrements in Gen I."""
        for ix, spec in enumerate(self.trainer_specs):
            o = self._slot(self.me, ix)
            for m in range(4):
                if self.buf[o + 10 + m * 2]:
                    self._write(o + 11 + m * 2, bytes([40]))
        # active copy of the moves
        a = self._active(self.me)
        for m in range(4):
            if self.buf[a + 24 + m * 2]:
                self._write(a + 25 + m * 2, bytes([40]))

    # Bide, Thrashing, Charging, Binding, Recharging, Rage: the ROM never polls the AI in these states.
    _FORCED_BITS = (1 << 0) | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 11) | (1 << 12)

    def forced(self) -> bool:
        """True when the cartridge would not ask the trainer for a decision this round."""
        if self._volatiles(self.me) & self._FORCED_BITS:
            return True
        if self.buf[self._slot(self.me, self._active_ix(self.me)) + 20] & 0x27:   # asleep or frozen
            return True
        return bool(self._volatiles(self.opp) & (1 << 5))                        # caught in the player's Wrap

    def request_kind(self) -> int | None:
        """0 turn decision, 1 replacement switch, None = no trainer decision this update."""
        r = self.b.raw.requests()[self.me]
        if r == MOVE and self.forced():
            return None
        return {MOVE: 0, SWITCH: 1, PASS: None}[r]

    def auto_choice(self) -> int:
        """Engine choice the trainer takes when no decision is asked (forced move or PASS)."""
        r = self.b.raw.requests()[self.me]
        if r == PASS:
            return 0
        choices = self.b.raw.choices(self.me, r) or [0]
        moves = [c for c in choices if engine.choice_type(c) == MOVE]
        return moves[0] if moves else choices[0]

    def auto_step(self, player_choice: int) -> None:
        """Advance one update without a trainer decision."""
        pre = self._snapshot_sides()
        c = self.auto_choice()
        self.b.raw.update(*((c, player_choice) if self.me == 0 else (player_choice, c)))
        self._after_update(pre, action=-1)

    def done(self) -> bool:
        return self.b.raw.result_type() != RESULT_NONE

    def winner_is_trainer(self) -> bool | None:
        w = self.b.winner
        if w is None or w == "tie":
            return None
        return (w == "p1") == (self.me == 0)

    def _observe_player(self) -> None:
        """Refresh the public snapshot of the player's active Pokémon."""
        ix = self._active_ix(self.opp)
        o = self._slot(self.opp, ix)
        types = self.buf[o + 22]
        self.snapshots[ix] = Snapshot(species=self.buf[o + 21], level=self.buf[o + 23], hp=_u16(self.buf, o + 18),
                                      max_hp=_u16(self.buf, o), status=public_status(self.buf[o + 20]),
                                      types=(ROM_TYPE[types & 15], ROM_TYPE[types >> 4]), round=self.round)

    def _own_mon(self, ix: int, active: bool) -> pkai.OwnMon:
        m = pkai.OwnMon()
        o = self._slot(self.me, ix)
        spec = self.trainer_specs[ix]
        m.species = self.br.species_from_dex(self.buf[o + 21]); m.level = self.buf[o + 23]
        m.status = public_status(self.buf[o + 20]); m.hp = _u16(self.buf, o + 18); m.max_hp = _u16(self.buf, o)
        src = self._active(self.me) if active else o
        types = self.buf[src + 22] if not active else self.buf[src + 11]
        m.types[0] = ROM_TYPE[types & 15]; m.types[1] = ROM_TYPE[types >> 4]
        for i in range(4):
            m.moves[i] = self.buf[(src + 24 if active else o + 10) + i * 2]
        for i in range(4):
            m.stats[i] = _u16(self.buf, src + 2 + i * 2)
        _, atk, dfn, spe, spc = spec.dvs
        m.dvs[0] = (atk << 4) | dfn; m.dvs[1] = (spe << 4) | spc
        return m

    def observation(self) -> pkai.Observation:
        o = pkai.Observation()
        o.round = self.round
        o.own_count = len(self.trainer_specs); o.player_count = len(self.player_specs)
        o.own_slot = self._active_ix(self.me); o.player_slot = self._active_ix(self.opp)
        o.trainer_class = self.trainer_class; o.count = self.count
        for ix in range(o.own_count):
            o.own[ix] = self._own_mon(ix, active=False)
        o.active = self._own_mon(o.own_slot, active=True)
        v = self._volatiles(self.me)
        o.disabled = (v >> 56) & 7 if (v >> 52) & 15 else 0
        for side, stages, status in ((self.me, o.stages, o.battle_status), (self.opp, o.player_stages, o.player_visible_status)):
            a = self._active(side)
            b12, b13, b14 = self.buf[a + 12], self.buf[a + 13], self.buf[a + 14]
            for i, nib in enumerate((b12 & 15, b12 >> 4, b13 & 15, b13 >> 4, b14 & 15, b14 >> 4)):
                stages[i] = 7 + _i4(nib)
            vol = self._volatiles(side)
            status[0] = vol & 0xFF                                  # Bide..Confusion map 1:1 to wBattleStatus1
            status[1] = (((vol >> 8) & 1) << 1) | (((vol >> 9) & 1) << 2) | (((vol >> 10) & 1) << 4) \
                | (((vol >> 11) & 1) << 5) | (((vol >> 12) & 1) << 6) | (((vol >> 13) & 1) << 7)
            status[2] = ((vol >> 14) & 1) | (((vol >> 15) & 1) << 1) | (((vol >> 16) & 1) << 2) | (((vol >> 17) & 1) << 3)
        o.player_visible_status[0] &= 0xA3; o.player_visible_status[1] &= 0x96; o.player_visible_status[2] &= 0x0E
        o.substitute = (v >> 40) & 0xFF
        o.confusion_counter = (v >> 18) & 7; o.toxic_counter = (v >> 59) & 31
        self._observe_player()
        for ix in range(o.player_count):
            p = o.player[ix]
            known = ix in self.b._revealed[self.opp]
            p.known = known
            if not known:
                continue
            s = self.snapshots.get(ix)
            if s is None:  # revealed by the engine but never sampled on the field yet
                slot = self._slot(self.opp, ix); types = self.buf[slot + 22]
                s = Snapshot(self.buf[slot + 21], self.buf[slot + 23], _u16(self.buf, slot + 18), _u16(self.buf, slot),
                             public_status(self.buf[slot + 20]), (ROM_TYPE[types & 15], ROM_TYPE[types >> 4]), self.round)
            p.species = self.br.species_from_dex(s.species); p.level = s.level; p.hp = s.hp; p.max_hp = s.max_hp
            p.status = s.status; p.types[0] = s.types[0]; p.types[1] = s.types[1]; p.observed_round = s.round
            for i, name in enumerate(sorted(self.b._revealed_moves[self.opp].get(ix, set()))[:4]):
                p.moves[i] = MOVES[name][0]
                mid = MOVES[name][0]
                p.revealed_moves[mid // 8] |= 1 << (mid % 8)
        return o

    def features(self) -> tuple[pkai.Features, int, int]:
        """(features, legal mask, request_kind) for the current trainer decision."""
        kind = self.request_kind()
        assert kind is not None, "no trainer decision pending"
        feats, mask = self.br.features(self.observation(), kind)
        return feats, mask, kind

    def legal_actions(self) -> list[int]:
        _, mask, _ = self.features()
        return [a for a in range(16) if mask >> a & 1]

    def player_choices(self) -> list[int]:
        r = self.b.raw.requests()[self.opp]
        return self.b.raw.choices(self.opp, r) or [0]

    def _engine_choice(self, action: int) -> int | None:
        """Engine choice for a move/switch action, or None for an item."""
        r = self.b.raw.requests()[self.me]
        choices = self.b.raw.choices(self.me, r)
        if action < 4:
            for c in choices:
                if engine.choice_type(c) == MOVE and engine.choice_data(c) == action + 1:
                    return c
            moves = [c for c in choices if engine.choice_type(c) == MOVE]
            if moves:
                return moves[0]   # Struggle (data 0) or an engine-side restriction the mask cannot see
            raise ValueError(f"move slot {action} not offered by the engine")
        if action < 10:
            target = action - 4
            order = self._order(self.me)
            for c in choices:
                if engine.choice_type(c) == SWITCH and order[engine.choice_data(c) - 1] - 1 == target:
                    return c
            raise ValueError(f"switch to slot {target} not offered by the engine")
        return None

    def _apply_item(self, item: int) -> None:
        ix = self._active_ix(self.me)
        slot = self._slot(self.me, ix); act = self._active(self.me)
        if item in HEAL_AMOUNT:
            hp, max_hp = _u16(self.buf, slot + 18), _u16(self.buf, slot)
            amt = HEAL_AMOUNT[item]
            hp = max_hp if amt is None else min(max_hp, hp + amt)
            self._write(slot + 18, hp.to_bytes(2, "little"))
        if item in (FULL_RESTORE, FULL_HEAL):
            self._write(slot + 20, b"\0")
            v = self._volatiles(self.me) & ~(1 << 14) & ~(31 << 59)
            self._write(act + 16, v.to_bytes(8, "little"))
        if item in (X_ATTACK, X_DEFEND, X_SPEED):
            stat = {X_ATTACK: 0, X_DEFEND: 1, X_SPEED: 2}[item]
            byte = act + 12 + stat // 2
            b = self.buf[byte]
            lo, hi = b & 15, b >> 4
            cur = _i4(lo if stat % 2 == 0 else hi)
            new = min(6, cur + 1)
            nib = new & 15
            b = (hi << 4) | nib if stat % 2 == 0 else (nib << 4) | lo
            self._write(byte, bytes([b]))
            num, den = _RATIOS[7 + new - 1]
            unmod = _u16(self.buf, slot + 2 + stat * 2)
            self._write(act + 2 + stat * 2, max(1, min(999, unmod * num // den)).to_bytes(2, "little"))
        if item == GUARD_SPEC:
            v = self._volatiles(self.me) | (1 << 8)
            self._write(act + 16, v.to_bytes(8, "little"))
        self.count = max(0, self.count - 1)

    def _snapshot_sides(self):
        out = []
        for side in (self.me, self.opp):
            ix = self._active_ix(side)
            o = self._slot(side, ix)
            out.append((ix, _u16(self.buf, o + 18), _u16(self.buf, o), self.buf[o + 20]))
        return out

    def step(self, action: int, player_choice: int) -> None:
        """Advance one update: trainer takes `action` (16-way), the player takes an engine choice."""
        kind = self.request_kind()
        assert kind is not None
        pre = self._snapshot_sides()
        choice = self._engine_choice(action)
        item_after = False
        if choice is None:
            item = self.item if action == ITEM_SLOT.get(self.item, -1) else None
            if item is None:
                raise ValueError(f"item action {action} not available to class {self.trainer_class}")
            my_spe = _u16(self.buf, self._active(self.me) + 6); pl_spe = _u16(self.buf, self._active(self.opp) + 6)
            first = my_spe > pl_spe or (my_spe == pl_spe and self.rng.random() < 0.5)
            if first:
                self._apply_item(item)
            else:
                item_after = True
            # libpkmn has no "use item" choice and PASS is only valid without a move request, so the
            # trainer's turn is consumed the way the engine already models a lost turn: the
            # Recharging volatile makes beforeMove skip the move (and clears the flag) without
            # selecting a move, touching PP or last-move state. Choice data 1 is what the engine
            # expects for a forced Pokémon.
            act = self._active(self.me)
            self._write(act + 16, (self._volatiles(self.me) | (1 << 11)).to_bytes(8, "little"))
            choice = engine.choice_init(MOVE, 1)
            self.b.raw.update(*((choice, player_choice) if self.me == 0 else (player_choice, choice)))
            if item_after and not self.done():
                self._apply_item(item)
        else:
            self.b.raw.update(*((choice, player_choice) if self.me == 0 else (player_choice, choice)))
        self._after_update(pre, action)

    def _after_update(self, pre, action: int) -> None:
        self.b._track_reveals()
        self._refill_pp()
        active = self._active_ix(self.me)
        if active != self.last_active:   # every enemy send-out resets the native item count
            self.count = self.count_max; self.last_active = active
        self.round += 1
        self.last_event = self._event(pre, action)
        self._observe_player()

    def _event(self, pre, action: int) -> list[float]:
        post = self._snapshot_sides()
        ev = [0.0] * EVENT_DIM
        if action >= 0:
            ev[0 if action < 4 else 1 if action < 10 else 2] = 1.0
        for i, (side_pre, side_post) in enumerate(zip(pre, post)):
            _, hp0, max0, st0 = side_pre; ix1, hp1, max1, st1 = side_post
            base = 3 + i * 6
            ev[base] = max(0.0, (hp0 - hp1) / max(1, max0)) if side_pre[0] == ix1 else 0.0
            ev[base + 1] = 1.0 if hp1 == 0 else 0.0
            ev[base + 2] = 1.0 if side_pre[0] != ix1 else 0.0
            ev[base + 3] = 1.0 if st0 == 0 and st1 != 0 else 0.0
            ev[base + 4] = hp1 / max(1, max1)
            ev[base + 5] = 1.0 if st1 else 0.0
        ev[15] = min(self.round, 50) / 50.0
        return ev

    def clone(self, seed_bytes: bytes | None = None) -> "TrainerEnv":
        c = copy.copy(self)
        c.b = copy.copy(self.b)
        buf = self.buf
        c.b.raw = RawBattle(buf[:_SEED_OFF] + (seed_bytes or buf[_SEED_OFF:]))
        c.b.raw.last_result = self.b.raw.last_result
        c.b._revealed = (set(self.b._revealed[0]), set(self.b._revealed[1]))
        c.b._revealed_moves = ({k: set(v) for k, v in self.b._revealed_moves[0].items()},
                               {k: set(v) for k, v in self.b._revealed_moves[1].items()})
        c.b._hist = list(self.b._hist)
        c.snapshots = dict(self.snapshots)
        c.rng = random.Random(self.rng.random())
        return c
