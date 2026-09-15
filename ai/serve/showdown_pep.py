"""Play Pokémon Showdown (via poke-env) with the PEP network.

The network only knows the trainer-seat observation (`pkai.Observation`): own party in
full, the other side as public information. This adapter builds that observation from a
poke-env `Battle` — our side from the request data (exact stats, DVs from IVs), the
opponent from what Showdown reveals (species, level, HP %, status, boosts, revealed
moves; stats estimated by the featurizer's public-info prior) — then runs the same C++
featurizer, mask and network as the simulator path. No item candidates (trainer class 0,
count 0). The GRU state and the event vector are kept per battle.

Known gaps vs the simulator observation: the opponent's absolute HP is derived from
Showdown's percentage and an estimated max HP; own crit-time "unmodified" stats are
recomputed by the featurizer from DVs without stat exp (as for an enemy trainer), so
they are lower than the real OU stats; a few volatiles (Bide, Thrash, Wrap) are only
partially visible through poke-env.
"""
from __future__ import annotations

import logging
import random
import re

import numpy as np
import torch

from models.pep import N_ACTIONS, PEP, features_to_tensors, load_checkpoint
from models.pep_data import decode_features
from sim.gen1data import MOVES, SPECIES
from sim.pkai_bridge import ROM_TYPE, bridge, pkai
from sim.trainer_env import EVENT_DIM, _RATIOS

log = logging.getLogger("showdown_pep")

TYPE_NAMES = ["NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST", "FIRE", "WATER",
              "GRASS", "ELECTRIC", "PSYCHIC", "ICE", "DRAGON"]
_ROM_TYPE_BY_NAME = {n: ROM_TYPE[i] for i, n in enumerate(TYPE_NAMES)}


def _ident(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_DEX_BY_ID = {_ident(name): row[0] for name, row in SPECIES.items()}
_MOVE_BY_ID = {_ident(name): row[0] for name, row in MOVES.items()}


def rom_type(t) -> int:
    name = getattr(t, "name", str(t)).upper()
    return _ROM_TYPE_BY_NAME.get(name, 0)


def status_byte(mon) -> int:
    """ROM status byte as the public view sees it (sleep -> 1, PSN 8, BRN 16, FRZ 32, PAR 64)."""
    st = mon.status
    if st is None:
        return 0
    n = st.name
    return {"SLP": 1, "PSN": 8, "TOX": 8, "BRN": 16, "FRZ": 32, "PAR": 64}.get(n, 0)


def stage(v: int) -> int:
    return 7 + max(-6, min(6, int(v)))


def _effects(mon) -> set[str]:
    return {e.name for e in getattr(mon, "effects", {}) or {}}


def battle_status(mon, side_conditions) -> tuple[int, int, int]:
    """(wBattleStatus1, 2, 3) bit layouts used by the featurizer."""
    ef = _effects(mon)
    sc = {getattr(c, "name", str(c)) for c in (side_conditions or {})}
    s0 = (1 << 0 if "BIDE" in ef else 0) | (1 << 1 if "LOCKED_MOVE" in ef else 0) \
        | (1 << 4 if getattr(mon, "preparing", False) else 0) | (1 << 7 if "CONFUSION" in ef else 0)
    s1 = (1 << 1 if "MIST" in ef else 0) | (1 << 2 if "FOCUS_ENERGY" in ef else 0) \
        | (1 << 4 if "SUBSTITUTE" in ef else 0) | (1 << 5 if getattr(mon, "must_recharge", False) else 0) \
        | (1 << 6 if "RAGE" in ef else 0) | (1 << 7 if "LEECH_SEED" in ef else 0)
    s2 = (1 << 0 if mon.status is not None and mon.status.name == "TOX" else 0) \
        | (1 << 1 if "LIGHT_SCREEN" in ef or "LIGHT_SCREEN" in sc else 0) \
        | (1 << 2 if "REFLECT" in ef or "REFLECT" in sc else 0) \
        | (1 << 3 if getattr(mon, "transformed", False) else 0)
    return s0, s1, s2


def calc_stat(base: int, dv: int, stat_exp: int, level: int, hp: bool) -> int:
    root = 0
    while root < 255 and root * root < stat_exp:
        root += 1
    v = ((2 * (base + dv) + root // 4) * level) // 100
    return v + (level + 10 if hp else 5)


def species_row(mon):
    return SPECIES.get(next((n for n in SPECIES if _ident(n) == _ident(mon.species)), ""), None)


class ShowdownObserver:
    """Builds pkai.Observation / legal mask / event vector for one battle."""

    def __init__(self):
        self.br = bridge()
        self.round = 0
        self._prev = None
        self._last_class: int | None = None
        self.last_event = [0.0] * EVENT_DIM

    # -- own side ------------------------------------------------------------------
    def _own_mon(self, mon, active: bool) -> pkai.OwnMon:
        m = pkai.OwnMon()
        dex = _DEX_BY_ID.get(_ident(mon.species))
        if dex is None:
            raise ValueError(f"unknown species {mon.species!r}")
        m.species = self.br.species_from_dex(dex)
        m.level = mon.level
        m.status = status_byte(mon)
        m.hp = int(mon.current_hp or 0); m.max_hp = int(mon.max_hp or 1)
        types = [t for t in (mon.type_1, mon.type_2) if t is not None]
        m.types[0] = rom_type(types[0]); m.types[1] = rom_type(types[1]) if len(types) > 1 else m.types[0]
        for i, mv in enumerate(list(mon.moves.values())[:4]):
            mid = _MOVE_BY_ID.get(_ident(mv.id), 0)
            m.moves[i] = mid
        st = mon.stats or {}
        base = {"atk": st.get("atk") or 0, "def": st.get("def") or 0, "spe": st.get("spe") or 0, "spa": st.get("spa") or 0}
        if active:
            b = mon.boosts or {}
            for i, k in enumerate(("atk", "def", "spe", "spa")):
                num, den = _RATIOS[stage(b.get(k, 0)) - 1]
                base[k] = max(1, min(999, base[k] * num // den))
        m.stats[0], m.stats[1], m.stats[2], m.stats[3] = base["atk"], base["def"], base["spe"], base["spa"]
        ivs = mon.ivs or {}
        dv = lambda k: min(15, int(ivs.get(k, 30)) // 2)
        m.dvs[0] = (dv("atk") << 4) | dv("def"); m.dvs[1] = (dv("spe") << 4) | dv("spa")
        return m

    # -- observation -----------------------------------------------------------------
    def observation(self, battle) -> tuple[pkai.Observation, list, list]:
        o = pkai.Observation()
        team = list(battle.team.values())
        opp_team = list(battle.opponent_team.values())
        me = battle.active_pokemon
        them = battle.opponent_active_pokemon
        o.round = self.round
        o.own_count = len(team); o.player_count = max(len(opp_team), battle.max_team_size or 6, 1)
        o.player_count = min(6, o.player_count)
        o.own_slot = next((i for i, p in enumerate(team) if p is me), 0)
        o.player_slot = next((i for i, p in enumerate(opp_team) if p is them), 0)
        o.trainer_class = 0; o.count = 0
        for i, p in enumerate(team[:6]):
            o.own[i] = self._own_mon(p, active=False)
        o.active = self._own_mon(me, active=True) if me is not None else o.own[o.own_slot]
        if me is not None and "DISABLE" in _effects(me):
            avail = {mv.id for mv in battle.available_moves}
            for i, mv in enumerate(list(me.moves.values())[:4]):
                if mv.id not in avail:
                    o.disabled = i + 1
                    break
        if me is not None:
            b = me.boosts or {}
            for i, k in enumerate(("atk", "def", "spe", "spa", "accuracy", "evasion")):
                o.stages[i] = stage(b.get(k, 0))
            s0, s1, s2 = battle_status(me, battle.side_conditions)
            o.battle_status[0], o.battle_status[1], o.battle_status[2] = s0, s1, s2
            o.substitute = 1 if "SUBSTITUTE" in _effects(me) else 0
        else:
            for i in range(6):
                o.stages[i] = 7
        for i in range(6):
            o.player_stages[i] = 7
        if them is not None:
            b = them.boosts or {}
            for i, k in enumerate(("atk", "def", "spe", "spa", "accuracy", "evasion")):
                o.player_stages[i] = stage(b.get(k, 0))
            s0, s1, s2 = battle_status(them, battle.opponent_side_conditions)
            o.player_visible_status[0] = s0 & 0xA3; o.player_visible_status[1] = s1 & 0x96
            o.player_visible_status[2] = s2 & 0x0E
        for i, p in enumerate(opp_team[:6]):
            pm = o.player[i]
            dex = _DEX_BY_ID.get(_ident(p.species))
            row = SPECIES.get(next((n for n in SPECIES if _DEX_BY_ID[_ident(n)] == dex), ""), None)
            if dex is None or row is None:
                continue
            pm.known = True
            pm.species = self.br.species_from_dex(dex); pm.level = p.level
            pm.status = status_byte(p)
            types = [t for t in (p.type_1, p.type_2) if t is not None]
            pm.types[0] = rom_type(types[0]); pm.types[1] = rom_type(types[1]) if len(types) > 1 else pm.types[0]
            base_hp = row[1]
            max_hp = calc_stat(base_hp, 8, min(65535, p.level * p.level * 4), p.level, True)
            pm.max_hp = max_hp
            frac = float(p.current_hp_fraction if p.current_hp_fraction is not None else 1.0)
            pm.hp = 0 if p.fainted else max(1 if frac > 0 else 0, int(round(frac * max_hp)))
            pm.observed_round = self.round
            for j, mv in enumerate(list(p.moves.values())[:4]):
                mid = _MOVE_BY_ID.get(_ident(mv.id), 0)
                pm.moves[j] = mid
                if mid:
                    pm.revealed_moves[mid // 8] |= 1 << (mid % 8)
        return o, team, opp_team

    # -- legal mask and event ------------------------------------------------------------
    def legal(self, battle, obs: pkai.Observation, team) -> tuple[int, int, pkai.Features]:
        kind = 1 if battle.force_switch else 0
        feats, mask = self.br.features(obs, kind)
        me = battle.active_pokemon
        avail_moves = {mv.id for mv in battle.available_moves}
        avail_switch = {id(p) for p in battle.available_switches}
        allowed = 0
        if me is not None and kind == 0:
            for i, mv in enumerate(list(me.moves.values())[:4]):
                if mv.id in avail_moves:
                    allowed |= 1 << i
            if not (mask & 15) and avail_moves:   # Struggle: featurizer put it in slot 0
                allowed |= 1
        for i, p in enumerate(team[:6]):
            if id(p) in avail_switch:
                allowed |= 1 << (4 + i)
        legal = mask & allowed
        if legal == 0:
            legal = mask & 0x3FF or allowed
        return legal, kind, feats

    def _snapshot(self, battle):
        def side(mon):
            if mon is None:
                return (None, 1.0, 0)
            return (mon.species + str(id(mon)), float(mon.current_hp_fraction or 0.0), status_byte(mon))
        return side(battle.active_pokemon), side(battle.opponent_active_pokemon)

    def update_event(self, battle) -> None:
        """Event vector for the decision about to be made (diff vs the previous decision)."""
        post = self._snapshot(battle)
        ev = [0.0] * EVENT_DIM
        if self._prev is not None:
            if self._last_class is not None:
                ev[self._last_class] = 1.0
            for i, (a, b) in enumerate(zip(self._prev, post)):
                id0, hp0, st0 = a; id1, hp1, st1 = b
                base = 3 + i * 6
                same = id0 == id1
                ev[base] = max(0.0, hp0 - hp1) if same else 0.0
                ev[base + 1] = 1.0 if hp1 <= 0 else 0.0
                ev[base + 2] = 0.0 if same else 1.0
                ev[base + 3] = 1.0 if st0 == 0 and st1 != 0 else 0.0
                ev[base + 4] = hp1
                ev[base + 5] = 1.0 if st1 else 0.0
        ev[15] = min(self.round, 50) / 50.0
        self.last_event = ev
        self._prev = post


class PEPShowdownAgent:
    """Chooses poke-env orders with a PEP checkpoint; one observer + GRU state per battle tag."""

    def __init__(self, checkpoint: str, temperature: float = 0.5, seed: int = 0):
        model, _ = load_checkpoint(checkpoint, map_location="cpu")
        self.model: PEP = model.eval()
        self.temperature = temperature
        self.gen = torch.Generator(device="cpu").manual_seed(seed)
        self._state: dict[str, tuple[ShowdownObserver, torch.Tensor | None]] = {}

    def forget(self, tag: str) -> None:
        self._state.pop(tag, None)

    @torch.no_grad()
    def choose(self, battle, player):
        tag = battle.battle_tag
        if len(self._state) > 32:
            self._state.clear()
        obs_state = self._state.get(tag)
        if obs_state is None:
            obs_state = (ShowdownObserver(), None)
        observer, h = obs_state
        observer.round = battle.turn
        observer.update_event(battle)
        obs, team, _opp = observer.observation(battle)
        legal, kind, feats = observer.legal(battle, obs, team)
        arrays = decode_features(bytes(feats))
        tensors = features_to_tensors({k: np.expand_dims(v, 0) for k, v in arrays.items()})
        tensors["legal"] = torch.tensor([legal], dtype=torch.int64)
        ev = torch.tensor(np.asarray(observer.last_event, dtype=np.float32)[None])
        logits, _value, h = self.model(tensors, ev, h)
        self._state[tag] = (observer, h)
        logits = logits[0].float()
        cands = [a for a in range(10) if legal >> a & 1]
        if not cands:
            return player.choose_random_move(battle)
        if self.temperature <= 0:
            a = max(cands, key=lambda i: float(logits[i]))
        else:
            probs = torch.softmax(logits / self.temperature, dim=-1)
            probs = torch.nan_to_num(probs, nan=0.0)
            probs[[i for i in range(N_ACTIONS) if i not in cands]] = 0.0
            a = cands[0] if float(probs.sum()) <= 0 else int(torch.multinomial(probs, 1, generator=self.gen))
        observer._last_class = 0 if a < 4 else 1
        if a < 4:
            moves = list(battle.active_pokemon.moves.values())
            mv = moves[a]
            target = next((m for m in battle.available_moves if m.id == mv.id), None)
            if target is None:   # Struggle or a stale slot
                target = battle.available_moves[0] if battle.available_moves else None
            return player.create_order(target) if target is not None else player.choose_random_move(battle)
        mon = team[a - 4]
        return player.create_order(mon)


def team_to_showdown(specs) -> str:
    blocks = []
    for spec in specs:
        blocks.append("\n".join([spec.species] + [f"- {m}" for m in spec.moves]))
    return "\n\n".join(blocks) + "\n"


def make_teambuilder(seed: int, team_dir: str | None = None):
    """Fresh team per battle: the built-in RBY OU standard sets, or one of the Showdown export files in `team_dir`
    (e.g. Metamon's `competitive/gen1ou` set, so both sides of a comparison draw from the same pool)."""
    import pathlib

    from poke_env.teambuilder import Teambuilder
    from sim.teams import sample_team

    pool = None
    if team_dir:
        def clean(text: str) -> str:   # Metamon exports carry trailing spaces and "Ability: none" lines
            lines = [ln.rstrip() for ln in text.splitlines()]
            return "\n".join(ln for ln in lines if not ln.startswith("Ability:")) + "\n"
        pool = [clean(f.read_text()) for f in sorted(pathlib.Path(team_dir).iterdir()) if f.is_file() and not f.name.endswith(".csv")]
        if not pool:
            raise ValueError(f"no team files in {team_dir}")

    class Builder(Teambuilder):
        def __init__(self):
            self.rng = random.Random(seed)

        def yield_team(self) -> str:
            paste = self.rng.choice(pool) if pool else team_to_showdown(sample_team(self.rng))
            return self.join_team(self.parse_showdown_team(paste))

    return Builder()


def make_player(checkpoint: str, *, battle_format: str = "gen1ou", temperature: float = 0.5, seed: int = 0,
                team=None, team_dir: str | None = None, **kwargs):
    from poke_env.player import Player

    agent = PEPShowdownAgent(checkpoint, temperature=temperature, seed=seed)

    class PEPPlayer(Player):
        def choose_move(self, battle):
            try:
                return agent.choose(battle, self)
            except Exception:
                self.logger.exception("PEP adapter failed; random move")
                return self.choose_random_move(battle)

    return PEPPlayer(battle_format=battle_format, team=team if team is not None else make_teambuilder(seed, team_dir), **kwargs)
