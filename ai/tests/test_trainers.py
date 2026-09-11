"""Trainer party dataset (ROM-built) and its libpkmn conversion.

Run: cd ai && uv run pytest tests/test_trainers.py
The dataset is regenerated through tools/dump_trainers.py if missing.
"""

from __future__ import annotations

import importlib.util
import zlib
from pathlib import Path

import pytest

from sim import trainers
from sim.gen1data import MOVES, SPECIES
from sim.pack import compute_stats

AI_ROOT = Path(__file__).resolve().parents[1]
# Classes whose parties get a move from TeamMoves / the champion rival branch.
SPECIAL_MOVE_CLASSES = {"LORELEI", "BRUNO", "AGATHA", "LANCE", "RIVAL3"}
ROM = AI_ROOT.parent / "pred-patch" / "pokered-ai.gbc"


def _ensure_dataset() -> Path:
    if trainers.DEFAULT_PATH.exists():
        return trainers.DEFAULT_PATH
    spec = importlib.util.spec_from_file_location("dump_trainers", AI_ROOT / "tools" / "dump_trainers.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.dump()


@pytest.fixture(scope="module")
def data() -> trainers.TrainerData:
    return trainers.load(_ensure_dataset())


def test_party_count_and_classes(data: trainers.TrainerData) -> None:
    assert len(data.parties) == 391
    assert sorted(data.class_names) == list(range(1, 48))
    assert data.class_names[1] == "YOUNGSTER" and data.class_names[47] == "LANCE"
    # Empty labels in parties.asm: these classes own no party records.
    assert {c for c in data.class_names if not data.by_class(data.class_names[c])} == {13, 27}
    assert len({p.key for p in data.parties}) == 391
    if ROM.exists():
        assert data.rom_crc32 == zlib.crc32(ROM.read_bytes())


def test_brock(data: trainers.TrainerData) -> None:
    (brock,) = data.by_class("BROCK")
    assert brock.index == 1 and brock.lone_attack_no == 1
    assert [(m.species_name, m.level) for m in brock.mons] == [("Geodude", 12), ("Onix", 14)]
    geodude, onix = brock.mons
    assert onix.move_names == ["Tackle", "Screech", "Bide"] and "Bide" not in geodude.move_names
    assert onix.pp == (35, 40, 0)  # Bide slot: PP untouched by ReadTrainer
    specs = brock.to_specs()
    assert [s.species for s in specs] == ["Geodude", "Onix"]
    assert specs[1].moves[2] == "Bide" and specs[1].level == 14
    assert specs[0].dvs == (8, 9, 8, 8, 8) and specs[0].statexp == (0, 0, 0, 0, 0)


def test_every_mon_well_formed(data: trainers.TrainerData) -> None:
    for party in data.parties:
        assert 1 <= len(party.mons) <= 6, party.key
        for m in party.mons:
            assert 1 <= len(m.moves) <= 4, (party.key, m)
            assert all(mv in trainers.MOVES_BY_ID for mv in m.moves), (party.key, m)
            assert m.max_hp > 0 and m.hp == m.max_hp, (party.key, m)
            assert 1 <= m.dex <= 151 and 1 <= m.level <= 100
            assert m.dvs == (0x98, 0x88)
            assert m.status == 0


def test_rom_stats_match_compute_stats(data: trainers.TrainerData) -> None:
    """DV/stat-exp decoding: pack.compute_stats must reproduce the ROM's numbers."""
    checked = 0
    for party in data.parties:
        for mon, spec in zip(party.mons, party.to_specs()):
            stats = compute_stats(spec)
            rom = {"hp": mon.max_hp, "atk": mon.attack, "def": mon.defense, "spe": mon.speed, "spc": mon.special}
            assert stats == rom, (party.key, mon.species_name, stats, rom)
            # Trainer mons have no PP Ups: PP equals each move's base PP,
            # except that ReadTrainer writes special moves (LoneMoves,
            # TeamMoves, champion rival) into slot 3 without updating PP.
            for slot, name in enumerate(spec.moves):
                if slot == 2 and (party.lone_attack_no or party.class_name in SPECIAL_MOVE_CLASSES):
                    continue
                assert mon.pp[slot] == MOVES[name][1], (party.key, mon, slot)
            checked += 1
    assert checked == 994  # every trainer mon in Red


def test_special_moves(data: trainers.TrainerData) -> None:
    lorelei = data.by_class("LORELEI")[0]
    assert lorelei.mons[4].species_name == "Lapras" and "Blizzard" in lorelei.mons[4].move_names
    rival = {p.index: p for p in data.by_class("RIVAL3")}
    assert {p.mons[5].species_name for p in rival.values()} == {"Blastoise", "Venusaur", "Charizard"}
    for p in rival.values():
        assert p.mons[0].species_name == "Pidgeot" and "Sky Attack" in p.mons[0].move_names
    starter_move = {"Blastoise": "Blizzard", "Venusaur": "Mega Drain", "Charizard": "Fire Blast"}
    for p in rival.values():
        assert starter_move[p.mons[5].species_name] in p.mons[5].move_names
    giovanni = {p.index: p for p in data.by_class("GIOVANNI")}
    assert "Fissure" in giovanni[3].mons[4].move_names and giovanni[3].mons[4].species_name == "Rhydon"
    assert all("Fissure" not in m.move_names for m in giovanni[1].mons + giovanni[2].mons)


def test_species_and_types_agree_with_gen1data(data: trainers.TrainerData) -> None:
    # Base stats implied by the dex id must be the ones libpkmn uses; a wrong
    # dex mapping would already break compute_stats, this pins the type pair.
    rom_types = {}
    for party in data.parties:
        for m in party.mons:
            rom_types.setdefault(m.species_name, set()).add(m.types)
    assert all(len(v) == 1 for v in rom_types.values())
    assert len(rom_types) > 100
    for name in rom_types:
        assert name in SPECIES
