"""ctypes mirror of the pkai C ABI (pkai/api.h).

The structures below mirror pkai::Observation / pkai::Features byte for
byte; `check_layout()` verifies every field offset against the library
before anything is trusted.
"""
from __future__ import annotations

import ctypes as C
import os
import pathlib

FEAT = 48
MAX_TOKENS = 27


class OwnMon(C.Structure):
    _fields_ = [("species", C.c_uint8), ("level", C.c_uint8), ("status", C.c_uint8), ("moves", C.c_uint8 * 4),
                ("types", C.c_uint8 * 2), ("dvs", C.c_uint8 * 2), ("hp", C.c_uint16), ("max_hp", C.c_uint16),
                ("stats", C.c_uint16 * 4)]


class PublicMon(C.Structure):
    _fields_ = [("known", C.c_bool), ("species", C.c_uint8), ("level", C.c_uint8), ("status", C.c_uint8),
                ("types", C.c_uint8 * 2), ("moves", C.c_uint8 * 4), ("revealed_moves", C.c_uint8 * 21),
                ("hp", C.c_uint16), ("max_hp", C.c_uint16), ("observed_round", C.c_uint32)]


class Observation(C.Structure):
    _fields_ = [("own", OwnMon * 6), ("player", PublicMon * 6), ("active", OwnMon), ("own_count", C.c_uint8),
                ("player_count", C.c_uint8), ("own_slot", C.c_uint8), ("player_slot", C.c_uint8),
                ("trainer_class", C.c_uint8), ("count", C.c_uint8), ("disabled", C.c_uint8),
                ("stages", C.c_uint8 * 6), ("battle_status", C.c_uint8 * 3), ("substitute", C.c_uint8),
                ("player_stages", C.c_uint8 * 6), ("player_visible_status", C.c_uint8 * 3),
                ("confusion_counter", C.c_uint8), ("toxic_counter", C.c_uint8), ("round", C.c_uint32)]


class Token(C.Structure):
    _fields_ = [("type", C.c_uint8), ("index", C.c_uint8), ("present", C.c_uint8), ("candidate", C.c_uint8),
                ("cat", C.c_uint16 * 4), ("f", C.c_int8 * FEAT)]


class Features(C.Structure):
    _fields_ = [("tokens", Token * MAX_TOKENS), ("count", C.c_uint8), ("legal", C.c_uint16), ("request_kind", C.c_uint8)]


_STRUCTS = {"OwnMon": OwnMon, "PublicMon": PublicMon, "Observation": Observation, "Token": Token, "Features": Features}


def load(path: str | os.PathLike | None = None) -> C.CDLL:
    """Load libpkai_c; default search: $PKAI_LIB, then build dirs next to the repo."""
    candidates = []
    if path:
        candidates.append(pathlib.Path(path))
    if os.environ.get("PKAI_LIB"):
        candidates.append(pathlib.Path(os.environ["PKAI_LIB"]))
    root = pathlib.Path(__file__).resolve().parents[2]
    for d in sorted(root.glob("build*")):
        candidates += list(d.rglob("libpkai_c.*"))
    for c in candidates:
        if c.exists():
            lib = C.CDLL(str(c))
            lib.pkai_observation_size.restype = C.c_size_t
            lib.pkai_features_size.restype = C.c_size_t
            lib.pkai_layout.restype = C.c_size_t
            lib.pkai_layout.argtypes = [C.c_char_p, C.c_size_t]
            lib.pkai_build_features.restype = C.c_int
            lib.pkai_build_features.argtypes = [C.c_char_p, C.c_size_t, C.c_void_p, C.c_uint8, C.c_void_p]
            lib.pkai_features_hash.restype = C.c_uint32
            lib.pkai_features_hash.argtypes = [C.c_void_p]
            lib.pkai_species_from_dex.restype = C.c_uint8
            lib.pkai_species_from_dex.argtypes = [C.c_char_p, C.c_size_t, C.c_uint8]
            lib.pkai_class_item.restype = C.c_int
            lib.pkai_class_item.argtypes = [C.c_char_p, C.c_size_t, C.c_uint8, C.POINTER(C.c_uint8), C.POINTER(C.c_uint8), C.POINTER(C.c_uint8)]
            lib.pkai_class_item_count.restype = C.c_int
            lib.pkai_class_item_count.argtypes = [C.c_char_p, C.c_size_t, C.c_uint8]
            return lib
    raise FileNotFoundError("libpkai_c not found; build gameboy/ with CMake or set PKAI_LIB")


def check_layout(lib: C.CDLL) -> None:
    """Raise if any field offset or size differs between C++ and this mirror."""
    n = lib.pkai_layout(None, 0)
    buf = C.create_string_buffer(n + 1)
    lib.pkai_layout(buf, n + 1)
    for line in buf.value.decode().splitlines():
        name, off, size = line.split()
        off, size = int(off), int(size)
        if "." in name:
            struct, field = name.split(".")
            got = getattr(_STRUCTS[struct], field)
            if got.offset != off or got.size != size:
                raise AssertionError(f"{name}: C++ offset/size {off}/{size}, python {got.offset}/{got.size}")
        elif C.sizeof(_STRUCTS[name]) != size:
            raise AssertionError(f"sizeof({name}): C++ {size}, python {C.sizeof(_STRUCTS[name])}")
    assert lib.pkai_observation_size() == C.sizeof(Observation)
    assert lib.pkai_features_size() == C.sizeof(Features)


def build_features(lib: C.CDLL, rom: bytes, obs: Observation, request_kind: int = 0) -> tuple[Features, int]:
    """Return (features, legal_mask) for an observation using the ROM's tables."""
    out = Features()
    mask = lib.pkai_build_features(rom, len(rom), C.byref(obs), request_kind, C.byref(out))
    if mask < 0:
        raise ValueError("pkai_build_features rejected its arguments")
    return out, mask


def features_hash(lib: C.CDLL, feats: Features) -> int:
    return lib.pkai_features_hash(C.byref(feats))


def species_from_dex(lib: C.CDLL, rom: bytes, dex: int) -> int:
    """ROM-internal species id for a Pokédex number (0 if unknown)."""
    return lib.pkai_species_from_dex(rom, len(rom), dex)


def class_item(lib: C.CDLL, rom: bytes, trainer_class: int) -> tuple[int, int, int]:
    """(item id, HP divisor, status required) the native AI offers for a trainer class; item 0 = none."""
    item, div, st = C.c_uint8(), C.c_uint8(), C.c_uint8()
    if lib.pkai_class_item(rom, len(rom), trainer_class, C.byref(item), C.byref(div), C.byref(st)) != 0:
        raise ValueError(f"bad trainer class {trainer_class}")
    return item.value, div.value, st.value


def class_item_count(lib: C.CDLL, rom: bytes, trainer_class: int) -> int:
    """Native per-send-out item use count for a trainer class."""
    n = lib.pkai_class_item_count(rom, len(rom), trainer_class)
    if n < 0:
        raise ValueError(f"bad trainer class {trainer_class}")
    return n


def token_matrix(feats: Features) -> list[list[int]]:
    """Feature vectors as plain lists: [present, type, index, candidate, cat0..3, f0..f47] per token."""
    rows = []
    for i in range(feats.count):
        t = feats.tokens[i]
        rows.append([t.present, t.type, t.index, t.candidate, *t.cat, *t.f])
    return rows
