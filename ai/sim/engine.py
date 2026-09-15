"""ctypes binding for libpkmn (showdown build). See vendor/include/pkmn.h."""

from __future__ import annotations

import ctypes
import random
from pathlib import Path

_LIB_PATH = Path(__file__).resolve().parents[1] / "vendor" / "lib" / "libpkmn-showdown.so"

BATTLE_SIZE = 384

# pkmn_choice_kind
PASS, MOVE, SWITCH = 0, 1, 2
# pkmn_result_kind
RESULT_NONE, RESULT_WIN, RESULT_LOSE, RESULT_TIE, RESULT_ERROR = range(5)

_RESULT_NAMES = {RESULT_WIN: "p1", RESULT_LOSE: "p2", RESULT_TIE: "tie"}


def _load() -> ctypes.CDLL:
    if not _LIB_PATH.exists():
        raise FileNotFoundError(f"{_LIB_PATH} missing - run ai/sim/build_engine.sh")
    lib = ctypes.CDLL(str(_LIB_PATH))
    lib.pkmn_choice_init.restype = ctypes.c_uint8
    lib.pkmn_choice_init.argtypes = [ctypes.c_int, ctypes.c_uint8]
    lib.pkmn_choice_type.restype = ctypes.c_int
    lib.pkmn_choice_type.argtypes = [ctypes.c_uint8]
    lib.pkmn_choice_data.restype = ctypes.c_uint8
    lib.pkmn_choice_data.argtypes = [ctypes.c_uint8]
    lib.pkmn_result_type.restype = ctypes.c_int
    lib.pkmn_result_type.argtypes = [ctypes.c_uint8]
    lib.pkmn_result_p1.restype = ctypes.c_int
    lib.pkmn_result_p1.argtypes = [ctypes.c_uint8]
    lib.pkmn_result_p2.restype = ctypes.c_int
    lib.pkmn_result_p2.argtypes = [ctypes.c_uint8]
    lib.pkmn_gen1_battle_update.restype = ctypes.c_uint8
    lib.pkmn_gen1_battle_update.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_void_p,
    ]
    lib.pkmn_gen1_battle_choices.restype = ctypes.c_uint8
    lib.pkmn_gen1_battle_choices.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    return lib


_lib: ctypes.CDLL | None = None


def lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _load()
    return _lib


def max_choices() -> int:
    return ctypes.c_size_t.in_dll(lib(), "PKMN_GEN1_MAX_CHOICES").value


def choice_init(kind: int, data: int) -> int:
    return lib().pkmn_choice_init(kind, data)


def choice_type(choice: int) -> int:
    return lib().pkmn_choice_type(choice)


def choice_data(choice: int) -> int:
    return lib().pkmn_choice_data(choice)


class RawBattle:
    """Thin stateful wrapper over a 384-byte gen1 battle buffer."""

    def __init__(self, packed: bytes, seed: int = 0):
        if len(packed) != BATTLE_SIZE:
            raise ValueError(f"battle buffer must be {BATTLE_SIZE} bytes")
        del seed  # RNG seed lives inside the packed buffer (bytes 376-384)
        self._buf = ctypes.create_string_buffer(packed, BATTLE_SIZE)
        self._n_choices = max_choices()
        self._choices = (ctypes.c_uint8 * self._n_choices)()
        self.last_result: int = 0

    @property
    def bytes(self) -> bytes:
        return self._buf.raw

    def update(self, c1: int, c2: int) -> int:
        self.last_result = lib().pkmn_gen1_battle_update(self._buf, c1, c2, None)
        return self.last_result

    def result_type(self) -> int:
        return lib().pkmn_result_type(self.last_result)

    def requests(self) -> tuple[int, int]:
        """Choice kinds requested from each player for the next update."""
        return (
            lib().pkmn_result_p1(self.last_result),
            lib().pkmn_result_p2(self.last_result),
        )

    def choices(self, player: int, request: int) -> list[int]:
        n = lib().pkmn_gen1_battle_choices(
            self._buf, player, request, self._choices, self._n_choices
        )
        return list(self._choices[:n])

    def winner(self) -> str | None:
        return _RESULT_NAMES.get(self.result_type())

    def play_out_random(
        self, max_turns: int = 1000, policy_seed: int = 0
    ) -> tuple[str, int]:
        rng = random.Random(policy_seed)
        c1 = c2 = 0
        turns = 0
        for _ in range(max_turns):
            if self.result_type() != RESULT_NONE:
                break
            self.update(c1, c2)
            turns += 1
            if self.result_type() != RESULT_NONE:
                break
            r1, r2 = self.requests()
            c1 = rng.choice(self.choices(0, r1))
            c2 = rng.choice(self.choices(1, r2))
        rt = self.result_type()
        if rt == RESULT_ERROR:
            raise RuntimeError("engine reported error result")
        return (_RESULT_NAMES.get(rt, "unfinished"), turns)
