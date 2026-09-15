"""Access to the device feature builder (libpkai_c) and the patched ROM from Python.

The training stack never re-implements observation → features: it hands a
pkai.Observation to the same C++ code that runs on the handheld.
"""
from __future__ import annotations

import functools
import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "pkai" / "python"))
import pkai  # noqa: E402

ROM_PATH = pathlib.Path(os.environ.get("PKAI_ROM", _ROOT / "pred-patch" / "pokered-ai.gbc"))

# libpkmn type index (sim.gen1data.TYPES order) -> ROM type constant.
ROM_TYPE = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x07, 0x08, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A]

# Item ids (constants/item_constants.asm).
FULL_RESTORE, POTION, SUPER_POTION, HYPER_POTION = 0x10, 0x14, 0x13, 0x12
FULL_HEAL, GUARD_SPEC, X_ATTACK, X_DEFEND, X_SPEED = 0x34, 0x37, 0x41, 0x42, 0x43
HEAL_AMOUNT = {POTION: 20, SUPER_POTION: 50, HYPER_POTION: 200, FULL_RESTORE: None}
# 16-way action index -> item class (10 heal, 11 full heal, 12 guard spec, 13 x attack, 14 x defend, 15 x speed)
ITEM_SLOT = {FULL_RESTORE: 10, POTION: 10, SUPER_POTION: 10, HYPER_POTION: 10,
             FULL_HEAL: 11, GUARD_SPEC: 12, X_ATTACK: 13, X_DEFEND: 14, X_SPEED: 15}


class Bridge:
    """One loaded library + ROM, with cached ROM lookups."""

    def __init__(self, rom_path: pathlib.Path = ROM_PATH, lib_path: str | None = None):
        if not rom_path.exists():
            raise FileNotFoundError(f"{rom_path} missing - run pred-patch/build_ai.sh")
        self.rom = rom_path.read_bytes()
        self.lib = pkai.load(lib_path)
        pkai.check_layout(self.lib)

    @functools.lru_cache(maxsize=None)
    def species_from_dex(self, dex: int) -> int:
        v = pkai.species_from_dex(self.lib, self.rom, dex)
        if not v:
            raise ValueError(f"no internal species id for dex {dex}")
        return v

    @functools.lru_cache(maxsize=None)
    def class_item(self, trainer_class: int) -> tuple[int, int, int]:
        return pkai.class_item(self.lib, self.rom, trainer_class)

    @functools.lru_cache(maxsize=None)
    def class_item_count(self, trainer_class: int) -> int:
        return pkai.class_item_count(self.lib, self.rom, trainer_class)

    def features(self, obs: pkai.Observation, request_kind: int) -> tuple[pkai.Features, int]:
        return pkai.build_features(self.lib, self.rom, obs, request_kind)

    def features_hash(self, feats: pkai.Features) -> int:
        return pkai.features_hash(self.lib, feats)


_bridge: Bridge | None = None


def bridge() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
    return _bridge
