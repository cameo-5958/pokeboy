"""Enemy trainer parties as the ROM builds them.

The dataset (default ``ai/datasets/trainers.json``) is produced by
``tools/dump_trainers.py``, which builds and runs ``pkai_dump_trainers``: the
patched ROM's own ``ReadTrainer`` executes inside the emulator for every
trainer class/party and ``wEnemyMons`` is read back verbatim. Species are
internal ids plus dex ids, moves are move ids, stats are the ROM's computed
values (fixed trainer DVs $9888, zero stat exp).

``TrainerParty.to_specs()`` converts a party into ``sim.pack.PokemonSpec``
objects for libpkmn, decoding DVs the way the cartridge does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sim.gen1data import MOVES, SPECIES
from sim.pack import PokemonSpec

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "datasets" / "trainers.json"

# Reverse lookups: dex id -> species name, move id -> move name.
SPECIES_BY_DEX: dict[int, str] = {v[0]: k for k, v in SPECIES.items()}
MOVES_BY_ID: dict[int, str] = {v[0]: k for k, v in MOVES.items()}


def decode_dvs(dv_bytes: tuple[int, int] | list[int]) -> tuple[int, int, int, int, int]:
    """(hp, atk, def, spe, spc) from the two packed DV bytes.

    Byte 0 = attack << 4 | defense, byte 1 = speed << 4 | special. The HP DV
    is assembled from the low bit of each of the four (atk, def, spe, spc ->
    bits 3..0), exactly as the ROM's CalcStat does; trainer DVs ``$9888``
    therefore give (8, 9, 8, 8, 8).
    """
    b0, b1 = int(dv_bytes[0]) & 0xFF, int(dv_bytes[1]) & 0xFF
    atk, dfn, spe, spc = b0 >> 4, b0 & 0xF, b1 >> 4, b1 & 0xF
    hp = ((atk & 1) << 3) | ((dfn & 1) << 2) | ((spe & 1) << 1) | (spc & 1)
    return (hp, atk, dfn, spe, spc)


@dataclass(frozen=True)
class TrainerMon:
    species: int  # internal (ROM) species id
    dex: int  # national dex number
    level: int
    moves: tuple[int, ...]  # move ids, 1-4 entries, zeros stripped
    pp: tuple[int, ...]  # PP per move slot, aligned with ``moves``
    dvs: tuple[int, int]  # packed DV bytes as stored in the party struct
    hp: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    special: int
    types: tuple[int, int]  # ROM type ids
    status: int = 0

    @property
    def species_name(self) -> str:
        try:
            return SPECIES_BY_DEX[self.dex]
        except KeyError:
            raise ValueError(f"no libpkmn species for dex {self.dex} (internal ${self.species:02x})") from None

    @property
    def move_names(self) -> list[str]:
        out = []
        for m in self.moves:
            if m not in MOVES_BY_ID:
                raise ValueError(f"no libpkmn move for id {m}")
            out.append(MOVES_BY_ID[m])
        return out

    def to_spec(self) -> PokemonSpec:
        return PokemonSpec(
            species=self.species_name,
            moves=self.move_names,
            level=self.level,
            dvs=decode_dvs(self.dvs),
            statexp=(0, 0, 0, 0, 0),
        )


@dataclass(frozen=True)
class TrainerParty:
    class_id: int
    class_name: str
    index: int  # 1-based wTrainerNo
    mons: list[TrainerMon]
    lone_attack_no: int = 0  # wLoneAttackNo the ROM saw (gym leader number)
    rival_starter: int = 0  # wRivalStarter the ROM saw (champion rival only)

    @property
    def key(self) -> tuple[int, int]:
        return (self.class_id, self.index)

    def to_specs(self) -> list[PokemonSpec]:
        return [m.to_spec() for m in self.mons]


@dataclass(frozen=True)
class TrainerData:
    rom_crc32: int
    class_names: dict[int, str]  # every class id, including the two with no parties
    parties: list[TrainerParty]

    def by_key(self, class_id: int, index: int) -> TrainerParty:
        for p in self.parties:
            if p.class_id == class_id and p.index == index:
                return p
        raise KeyError((class_id, index))

    def by_class(self, class_name: str) -> list[TrainerParty]:
        return [p for p in self.parties if p.class_name == class_name]



def _mon(d: dict) -> TrainerMon:
    moves = [int(m) for m in d["moves"]]
    pp = [int(p) for p in d["pp"]]
    n = max((i + 1 for i, m in enumerate(moves) if m), default=0)
    return TrainerMon(
        species=int(d["species"]),
        dex=int(d["dex"]),
        level=int(d["level"]),
        moves=tuple(moves[:n]),
        pp=tuple(pp[:n]),
        dvs=(int(d["dvs"][0]), int(d["dvs"][1])),
        hp=int(d["hp"]),
        max_hp=int(d["max_hp"]),
        attack=int(d["attack"]),
        defense=int(d["defense"]),
        speed=int(d["speed"]),
        special=int(d["special"]),
        types=(int(d["types"][0]), int(d["types"][1])),
        status=int(d.get("status", 0)),
    )


def load(path: Path | str = DEFAULT_PATH) -> TrainerData:
    raw = json.loads(Path(path).read_text())
    parties: list[TrainerParty] = []
    class_names: dict[int, str] = {}
    for c in raw["classes"]:
        class_names[int(c["class"])] = str(c["name"])
        for p in c["parties"]:
            parties.append(
                TrainerParty(
                    class_id=int(c["class"]),
                    class_name=str(c["name"]),
                    index=int(p["index"]),
                    mons=[_mon(m) for m in p["mons"]],
                    lone_attack_no=int(p.get("lone_attack_no", 0)),
                    rival_starter=int(p.get("rival_starter", 0)),
                )
            )
    return TrainerData(rom_crc32=int(raw["rom_crc32"]), class_names=class_names, parties=parties)
