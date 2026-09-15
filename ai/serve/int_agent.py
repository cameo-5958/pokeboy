"""Play the trainer seat with the exported integer model through the C++ library (libpkai_c).

Same interface as serve.pep_agent.PEPAgent, but every decision is computed by
`pkai_model_run` on a `pkai.weights` file, so evaluation exercises exactly the
on-device arithmetic (int8 GEMMs, LUT softmax, int16 GRU state, uint8 Q0.8
probabilities already at temperature 0.5 with illegal actions at 0).
"""
from __future__ import annotations

import ctypes as C
import os

import numpy as np

from models.pep_int import EV_DIM, N_ACTIONS, IntPEP
from sim.pkai_bridge import pkai
from sim.trainer_env import TrainerEnv


def _bind(lib: C.CDLL) -> C.CDLL:
    """Declare the pkai_model_* signatures (idempotent)."""
    lib.pkai_model_open.restype = C.c_void_p
    lib.pkai_model_open.argtypes = [C.c_char_p]
    lib.pkai_model_close.restype = None
    lib.pkai_model_close.argtypes = [C.c_void_p]
    lib.pkai_model_error.restype = C.c_char_p
    lib.pkai_model_error.argtypes = []
    lib.pkai_model_gru_size.restype = C.c_int
    lib.pkai_model_gru_size.argtypes = [C.c_void_p]
    lib.pkai_model_run.restype = C.c_int
    lib.pkai_model_run.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
    lib.pkai_backend_name.restype = C.c_char_p
    lib.pkai_backend_name.argtypes = []
    return lib


class IntModel:
    """Handle on one opened pkai.weights file inside libpkai_c."""

    def __init__(self, weights: str | os.PathLike, lib_path: str | None = None):
        self.lib = _bind(pkai.load(lib_path))
        self.handle = self.lib.pkai_model_open(os.fsencode(str(weights)))
        if not self.handle:
            raise RuntimeError(f"pkai_model_open({weights}): {self.lib.pkai_model_error().decode()}")
        self.gru = int(self.lib.pkai_model_gru_size(self.handle))
        self.backend = self.lib.pkai_backend_name().decode()

    def close(self) -> None:
        if getattr(self, "handle", None):
            self.lib.pkai_model_close(self.handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def run(self, features: bytes | pkai.Features, ev8: np.ndarray | None, hidden: np.ndarray | None):
        """One decision. `hidden` (int16[gru]) is updated in place when given.

        Returns (logits_q8 int16[16], probs uint8[16], value_acc int32).
        """
        if isinstance(features, pkai.Features):
            fbuf = features
        else:
            fbuf = (C.c_uint8 * C.sizeof(pkai.Features)).from_buffer_copy(bytes(features))
        ev_p = None
        if ev8 is not None:
            ev8 = np.ascontiguousarray(np.asarray(ev8, np.int8).reshape(EV_DIM))
            ev_p = ev8.ctypes.data_as(C.c_void_p)
        h_p = None
        if hidden is not None:
            assert hidden.dtype == np.int16 and hidden.size == self.gru and hidden.flags.c_contiguous
            h_p = hidden.ctypes.data_as(C.c_void_p)
        logits = np.zeros(N_ACTIONS, np.int16)
        probs = np.zeros(N_ACTIONS, np.uint8)
        vacc = np.zeros(1, np.int32)
        rc = self.lib.pkai_model_run(self.handle, C.byref(fbuf), ev_p, h_p,
                                     logits.ctypes.data_as(C.c_void_p), probs.ctypes.data_as(C.c_void_p),
                                     vacc.ctypes.data_as(C.c_void_p))
        if rc < 0:
            raise RuntimeError("pkai_model_run rejected its arguments")
        return logits, probs, int(vacc[0])


def sample_u8(probs: np.ndarray, rng: np.random.Generator, legal_mask: int) -> int:
    """Inverse-CDF sample from uint8 Q0.8 probabilities; first legal action if they are all zero."""
    cdf = np.cumsum(probs.astype(np.int64))
    total = int(cdf[-1])
    if total <= 0:
        legal = [a for a in range(N_ACTIONS) if legal_mask >> a & 1]
        return legal[0] if legal else 0
    r = int(rng.integers(0, total))  # uniform in [0, total)
    return int(np.searchsorted(cdf, r, side="right"))


class IntAgent:
    """PEPAgent-compatible policy that runs the on-device integer model."""

    def __init__(self, weights: str | os.PathLike, seed: int = 0, lib_path: str | None = None):
        self.model = IntModel(weights, lib_path)
        self.rng = np.random.default_rng(seed)
        self.h = np.zeros(self.model.gru, np.int16)
        self._battle_key = None

    def reset(self) -> None:
        self.h[:] = 0

    def choose(self, env: TrainerEnv) -> int:
        key = env.b.battle_id
        if key != self._battle_key:
            self._battle_key = key
            self.h[:] = 0
        feats, mask, _kind = env.features()
        ev8 = IntPEP.quantize_event(np.asarray(env.last_event, np.float32)[None])[0]
        _logits, probs, _v = self.model.run(feats, ev8, self.h)
        return sample_u8(probs, self.rng, mask)
