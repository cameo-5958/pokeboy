"""Integer reference forward pass of PEP (numpy only) - the executable §8.3 specification.

Everything here is defined in terms of int8/int16/int32 arithmetic (int64 numpy
intermediates stand in for the 64-bit products of the scalar C++ path).  No torch.
The C++ scalar and NEON kernels are tested against this module bit for bit.

Fixed-point conventions (see notes/SPEC-on-device-battle-ai.md §8.3 and the report
in tools/export_weights.py for the places where the spec was interpreted):

* GEMM inputs int8 symmetric (zero point 0); weights int8 per-output-channel; bias int32
  added to the int32 accumulator; requantisation = saturating rounding doubling high
  multiply by a Q0.31 multiplier (VQRDMULH semantics, ties toward +inf) followed by a
  round-half-to-even arithmetic right shift and saturation.  A negative shift means a
  saturating left shift applied to the accumulator *before* the multiply.
* Residual stream int16 at one fixed scale (`scales["res"]`); every ReZero branch is
  requantised to int16 at its own scale, multiplied by alpha (Q1.14, int16), rounded
  (half to even) by >> 14 and added with saturation.
* Attention logits: int32 dot products of int8 Q and K (shared scale per layer).
  `max - x` is right-shifted by `logit_shift` (round half to even) into 1/256-nat units
  and saturated to [0, 32767]; exp LUT (257 x Q0.15) indexed by d >> 4 with linear
  interpolation on the low 4 bits (floor); sum int32; reciprocal from a 256-entry
  16-bit LUT plus one Newton step; probabilities uint8 Q0.8 (255 = 255/256).
  Masked entries are excluded from max and sum and get probability 0.
* GRU: preactivations requantised to int16 Q3.12; sigmoid/tanh 257-entry LUTs over
  Q3.12 (index = (v + 32768) >> 8, interpolation on the low 8 bits, floor); gate outputs
  Q1.14; products Q1.14 x Q1.14 -> >> 14 (round half to even) -> saturating add.
* Pointer logits: int32 -> 1/256-nat int16 by a pure shift, + per-type bias (int16,
  1/256 nat), masked = -32768, temperature 0.5 = saturating << 1.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np


FEAT = 48
N_TOKENS = 27
N_ACTIONS = 16
EV_DIM = 64
N_TOKEN_TYPES = 6

I8_MIN, I8_MAX = -127, 127  # symmetric int8 (−128 never produced)
I16_MIN, I16_MAX = -32768, 32767
I32_MIN, I32_MAX = -(2**31), 2**31 - 1
MASK16 = -32768  # masked logit
Q14_ONE = 1 << 14
LOGIT_FRAC_BITS = 8  # attention / pointer logits carry 1/256 nat per LSB
EXP_LUT_INDEX_BITS = 4  # exp LUT index = d16 >> 4  (one entry per 1/16 nat)
EXP_LUT_SIZE = 256
GATE_LUT_SIZE = 256
RECIP_LUT_SIZE = 256
EV_SCALE = 1.0 / 127.0  # event vectors are fractions / flags, quantised like input features

TOKEN_TYPE_NAMES = ("field", "own_mon", "player_mon", "own_move", "player_move", "item")
TOKEN_SLICES = {
    "field": slice(0, 1),
    "own_mon": slice(1, 7),
    "player_mon": slice(7, 13),
    "own_move": slice(13, 17),
    "player_move": slice(17, 21),
    "item": slice(21, 27),
}
# (embedding table, cat column) gathered after the 48 int8 features for each token type
TOKEN_EMBEDS = {
    "field": (("emb.trainer", 0), ("emb.request", 1)),
    "own_mon": (("emb.species", 0), ("emb.type", 1), ("emb.type", 2), ("emb.matchup", 3)),
    "player_mon": (("emb.species", 0), ("emb.type", 1), ("emb.type", 2), ("emb.matchup", 3)),
    "own_move": (("emb.move", 0), ("emb.effect", 1), ("emb.type", 2)),
    "player_move": (("emb.move", 0), ("emb.effect", 1), ("emb.type", 2)),
    "item": (("emb.item", 0),),
}


@dataclass
class QuantParams:
    """Everything the integer model needs; `tensors` is exactly what pkai.weights stores."""

    config: dict  # d, layers, heads, ffn, gru, emb_species, emb_move, emb_matchup, emb_small
    feature_schema: str
    tensors: "OrderedDict[str, np.ndarray]" = field(default_factory=OrderedDict)
    scales: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    rom_crc32: int = 0

    def nbytes(self) -> int:
        return sum(int(t.nbytes) for t in self.tensors.values())


def sat(x, lo: int, hi: int) -> np.ndarray:
    return np.clip(np.asarray(x, np.int64), lo, hi)


def rshift_round_even(x, s) -> np.ndarray:
    """Arithmetic right shift by s (>= 0, scalar or broadcastable array) rounding half to even."""
    x = np.asarray(x, np.int64)
    s = np.asarray(s, np.int64)
    if np.any(s < 0):
        raise ValueError("negative shift")
    q = x >> s
    r = x & ((np.int64(1) << s) - 1)
    half = np.where(s > 0, np.int64(1) << np.maximum(s - 1, 0), np.int64(0))
    inc = (r > half) | ((r == half) & (s > 0) & ((q & 1) == 1))
    return q + inc


def srdmh(x, m) -> np.ndarray:
    """Saturating rounding doubling high multiply (NEON VQRDMULH): sat32((2*x*m + 2^31) >> 32)."""
    x = np.asarray(x, np.int64)
    m = np.asarray(m, np.int64)
    return np.clip((x * m + (np.int64(1) << 30)) >> 31, I32_MIN, I32_MAX)


def requant(acc, mult, shift, lo: int, hi: int) -> np.ndarray:
    """int32 accumulator -> saturated [lo, hi] via Q0.31 multiplier and rounding shift.

    shift may be negative (saturating left shift of the accumulator first)."""
    acc = np.asarray(acc, np.int64)
    shift = np.asarray(shift, np.int64)
    left = np.maximum(-shift, 0)
    right = np.maximum(shift, 0)
    x = np.clip(acc << left, I32_MIN, I32_MAX)
    y = srdmh(x, mult)
    y = rshift_round_even(y, right)
    return np.clip(y, lo, hi)


def requant_params(real: float) -> tuple[int, int]:
    """real (> 0) -> (M, shift) with real ≈ M * 2^-31 * 2^-shift, M in [2^30, 2^31)."""
    if not (real > 0) or not math.isfinite(real):
        raise ValueError(f"bad requant ratio {real}")
    m0, e = math.frexp(real)  # real = m0 * 2^e, m0 in [0.5, 1)
    m = int(round(m0 * (1 << 31)))
    shift = -e
    if m == 1 << 31:
        m = 1 << 30
        shift -= 1
    return m, shift


def gemm(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    """int8 (..., K) x int8 (C, K) -> exact int64 (..., C) (float64 matmul is exact below 2^53)."""
    return (x.astype(np.float64) @ w.astype(np.float64).T).astype(np.int64)


def make_luts() -> "OrderedDict[str, np.ndarray]":
    """The four fixed tables shipped in pkai.weights (independent of the checkpoint)."""
    i = np.arange(EXP_LUT_SIZE + 1, dtype=np.float64)
    exp_q15 = np.minimum(np.round(32768.0 * np.exp(-i / 16.0)), 32767).astype(np.int16)
    # reciprocal of s_n in [2^14, 2^15): entry j covers s_n in [2^14 + 64 j, 2^14 + 64 (j+1))
    j = np.arange(RECIP_LUT_SIZE, dtype=np.float64)
    recip_q15 = np.round((1 << 29) / (16384.0 + 64.0 * j + 32.0)).astype(np.int16)
    v = (np.arange(GATE_LUT_SIZE + 1, dtype=np.float64) * 256.0 - 32768.0) / 4096.0  # Q3.12 grid
    sigmoid_q14 = np.round(16384.0 / (1.0 + np.exp(-v))).astype(np.int16)
    tanh_q14 = np.round(16384.0 * np.tanh(v)).astype(np.int16)
    return OrderedDict(
        [
            ("lut.exp_q15", exp_q15),
            ("lut.recip_q15", recip_q15),
            ("lut.sigmoid_q14", sigmoid_q14),
            ("lut.tanh_q14", tanh_q14),
        ]
    )


def gate_lut(v_q12, lut: np.ndarray) -> np.ndarray:
    """int16 Q3.12 -> Q1.14 through a 257-entry table, linear interpolation on the low 8 bits."""
    u = sat(v_q12, I16_MIN, I16_MAX) + 32768  # 0..65535
    i = u >> 8
    frac = u & 255
    lo = lut[i].astype(np.int64)
    hi = lut[i + 1].astype(np.int64)
    return lo + (((hi - lo) * frac) >> 8)


def exp_lut(d16, lut: np.ndarray) -> np.ndarray:
    """d16 in [0, 32767] (1/256 nat) -> Q0.15 exp(-d) with interpolation; 0 beyond the table."""
    d16 = np.asarray(d16, np.int64)
    i = np.minimum(d16 >> EXP_LUT_INDEX_BITS, EXP_LUT_SIZE)
    frac = d16 & ((1 << EXP_LUT_INDEX_BITS) - 1)
    lo = lut[i].astype(np.int64)
    hi = lut[np.minimum(i + 1, EXP_LUT_SIZE)].astype(np.int64)
    return lo + (((hi - lo) * frac) >> EXP_LUT_INDEX_BITS)


def bit_length(x: np.ndarray) -> np.ndarray:
    """Number of significant bits of positive int64 values (0 for 0)."""
    _, e = np.frexp(np.asarray(x, np.float64))
    return np.where(np.asarray(x) > 0, e, 0).astype(np.int64)


def softmax_int(x, mask, shift: int, lut_exp: np.ndarray, lut_recip: np.ndarray, trace: dict | None = None, prefix: str = ""):
    """Integer softmax along the last axis.

    x: int64 (..., L) int32-valued logits; mask: bool (..., L), True = participates;
    shift: converts (max - x) to 1/256-nat units (>= 0 rounds half to even; < 0 left shifts).
    Returns uint8 probabilities (..., L) in Q0.8; masked entries are exactly 0.
    """
    x = np.asarray(x, np.int64)
    mask = np.asarray(mask, bool)
    xm = np.where(mask, x, I32_MIN)
    m = xm.max(axis=-1, keepdims=True)
    d = np.where(mask, m - x, 0)
    if shift >= 0:
        d16 = rshift_round_even(d, shift)
    else:
        d16 = d << (-shift)
    d16 = np.clip(d16, 0, I16_MAX)
    p = np.where(mask, exp_lut(d16, lut_exp), 0)  # Q0.15
    s = p.sum(axis=-1, keepdims=True)  # int32 on device (<= 27 * 32767)
    any_ = s > 0
    s_safe = np.where(any_, s, 1 << 14)
    n = 15 - bit_length(s_safe)  # s << n in [2^14, 2^15)
    s_n = np.where(n >= 0, s_safe << np.maximum(n, 0), s_safe >> np.maximum(-n, 0))
    idx = (s_n >> 6) - 256
    r0 = lut_recip[idx].astype(np.int64)
    err = (np.int64(1) << 29) - s_n * r0
    r1 = r0 + ((r0 * err) >> 29)
    probs = rshift_round_even(p * r1, 21 - n)
    probs = np.where(mask & any_, np.clip(probs, 0, 255), 0).astype(np.uint8)
    if trace is not None:
        trace[prefix + "d16"] = d16.astype(np.int16)
        trace[prefix + "p_q15"] = p.astype(np.int16)
        trace[prefix + "sum"] = s[..., 0].astype(np.int32)
        trace[prefix + "recip_q15"] = r1[..., 0].astype(np.int32)
        trace[prefix + "probs_u8"] = probs
    return probs


@dataclass
class IntOutput:
    logits_q8: np.ndarray  # (N,16) int16, 1/256 nat, temperature 1, masked = -32768
    logits_t: np.ndarray  # (N,16) int16, after temperature 0.5 (<< 1), masked = -32768
    probs: np.ndarray  # (N,16) uint8 Q0.8, softmax of logits_t
    value_acc: np.ndarray  # (N,) int32
    value: np.ndarray  # (N,) float32 dequantised value logit
    h: np.ndarray  # (N, gru) int16 Q1.14
    trace: "OrderedDict[str, np.ndarray]"


class IntPEP:
    """numpy integer forward of a quantised PEP.  Call `forward` per decision."""

    def __init__(self, qp: QuantParams):
        self.qp = qp
        self.t = qp.tensors
        cfg = qp.config
        self.d = int(cfg["d"])
        self.layers = int(cfg["layers"])
        self.heads = int(cfg["heads"])
        self.dh = self.d // self.heads
        self.ffn = int(cfg["ffn"])
        self.gru = int(cfg["gru"])
        self.lut_exp = self.t["lut.exp_q15"]
        self.lut_recip = self.t["lut.recip_q15"]
        self.lut_sig = self.t["lut.sigmoid_q14"]
        self.lut_tanh = self.t["lut.tanh_q14"]

    # -- helpers -------------------------------------------------------------

    def init_hidden(self, n: int) -> np.ndarray:
        return np.zeros((n, self.gru), np.int16)

    @staticmethod
    def quantize_event(ev: np.ndarray) -> np.ndarray:
        """float event vector (N,64) -> int8 at EV_SCALE (round half to even, saturate)."""
        return np.clip(np.rint(np.asarray(ev, np.float64) / EV_SCALE), I8_MIN, I8_MAX).astype(np.int8)

    def _rq(self, acc, name: str, lo: int, hi: int) -> np.ndarray:
        return requant(acc, self.t[name + ".mult"], self.t[name + ".shift"], lo, hi)

    def _linear(self, x8: np.ndarray, name: str, lo: int, hi: int) -> np.ndarray:
        acc = gemm(x8, self.t[name + ".w"])
        if name + ".b" in self.t:
            acc = acc + self.t[name + ".b"].astype(np.int64)
        return self._rq(acc, name, lo, hi)

    def _linear_acc(self, x8: np.ndarray, name: str) -> np.ndarray:
        acc = gemm(x8, self.t[name + ".w"])
        if name + ".b" in self.t:
            acc = acc + self.t[name + ".b"].astype(np.int64)
        return np.clip(acc, I32_MIN, I32_MAX)

    def _gather(self, table: str, ids: np.ndarray) -> np.ndarray:
        tab = self.t[table]
        return tab[np.clip(ids, 0, tab.shape[0] - 1)]

    def _rezero_add(self, x16: np.ndarray, branch16: np.ndarray, alpha_name: str) -> np.ndarray:
        alpha = int(self.t[alpha_name][0])
        prod = rshift_round_even(branch16.astype(np.int64) * alpha, 14)
        return sat(x16.astype(np.int64) + prod, I16_MIN, I16_MAX)

    def _attention(self, q8, k8, v8, key_mask, shift: int, pv_name: str, trace, prefix: str):
        """q8 (N,Lq,d) k8/v8 (N,L,d) int8 -> P·V requantised int8 (N,Lq,d)."""
        N, Lq, _ = q8.shape
        L = k8.shape[1]
        h, dh = self.heads, self.dh
        qh = q8.reshape(N, Lq, h, dh).transpose(0, 2, 1, 3).astype(np.float64)
        kh = k8.reshape(N, L, h, dh).transpose(0, 2, 1, 3).astype(np.float64)
        scores = (qh @ kh.transpose(0, 1, 3, 2)).astype(np.int64)  # (N,h,Lq,L) int32 (K = dh)
        if trace is not None:
            trace[prefix + "scores"] = scores.astype(np.int32)
        probs = softmax_int(scores, key_mask[:, None, None, :], shift, self.lut_exp, self.lut_recip, trace, prefix)
        vh = v8.reshape(N, L, h, dh).transpose(0, 2, 1, 3).astype(np.float64)
        pv = (probs.astype(np.float64) @ vh).astype(np.int64)  # uint8 x int8 -> int32 (K = L)
        pv = pv.transpose(0, 2, 1, 3).reshape(N, Lq, h * dh)
        out = self._rq(pv, pv_name, I8_MIN, I8_MAX)
        if trace is not None:
            trace[prefix + "pv_acc"] = pv.astype(np.int32)
            trace[prefix + "pv8"] = out.astype(np.int8)
        return out

    # -- forward ---------------------------------------------------------------

    def forward(
        self,
        feats: dict[str, np.ndarray],
        ev8: np.ndarray | None = None,
        h: np.ndarray | None = None,
        record: bool = False,
    ) -> IntOutput:
        t = self.t
        trace: OrderedDict | None = OrderedDict() if record else None
        present = np.asarray(feats["present"]).astype(bool)
        N = present.shape[0]
        key_mask = present.copy()
        key_mask[:, 0] |= ~present.any(axis=1)
        cat = np.asarray(feats["cat"]).astype(np.int64)
        f = np.asarray(feats["f"]).astype(np.int8)
        if ev8 is None:
            ev8 = np.zeros((N, EV_DIM), np.int8)
        if h is None:
            h = self.init_hidden(N)
        ev8 = np.asarray(ev8, np.int8)
        h = np.asarray(h, np.int16)

        # ---- token projections -> int16 residual stream
        x16 = np.zeros((N, N_TOKENS, self.d), np.int64)
        for name in TOKEN_TYPE_NAMES:
            sl = TOKEN_SLICES[name]
            parts = [f[:, sl]] + [self._gather(tab, cat[:, sl, col]) for tab, col in TOKEN_EMBEDS[name]]
            xin = np.concatenate(parts, axis=-1).astype(np.int8)
            x16[:, sl] = self._linear(xin, f"embed.{name}", I16_MIN, I16_MAX)
            if trace is not None:
                trace[f"embed.{name}.in8"] = xin
        if trace is not None:
            trace["embed.out16"] = x16.astype(np.int16)

        # ---- encoder layers
        for l in range(self.layers):
            p = f"layers.{l}."
            a8 = self._rq(x16, p + "attn.in", I8_MIN, I8_MAX).astype(np.int8)
            q8 = self._linear(a8, p + "attn.q", I8_MIN, I8_MAX).astype(np.int8)
            k8 = self._linear(a8, p + "attn.k", I8_MIN, I8_MAX).astype(np.int8)
            v8 = self._linear(a8, p + "attn.v", I8_MIN, I8_MAX).astype(np.int8)
            if trace is not None:
                trace[p + "attn.in8"] = a8
                trace[p + "attn.q8"] = q8
                trace[p + "attn.k8"] = k8
                trace[p + "attn.v8"] = v8
            pv8 = self._attention(q8, k8, v8, key_mask, int(t[p + "attn.logit_shift"][0]), p + "attn.pv", trace, p + "attn.")
            o16 = self._linear(pv8, p + "attn.o", I16_MIN, I16_MAX)
            x16 = self._rezero_add(x16, o16, p + "alpha_attn_q14")
            if trace is not None:
                trace[p + "attn.o16"] = o16.astype(np.int16)
                trace[p + "attn.res16"] = x16.astype(np.int16)

            f8 = self._rq(x16, p + "ffn.in", I8_MIN, I8_MAX).astype(np.int8)
            acc = self._linear_acc(f8, p + "ffn.f1")
            acc = np.maximum(acc, 0)  # ReLU on the accumulator (equivalent to clamping after requant)
            h8 = self._rq(acc, p + "ffn.f1", 0, I8_MAX).astype(np.int8)
            o16 = self._linear(h8, p + "ffn.f2", I16_MIN, I16_MAX)
            x16 = self._rezero_add(x16, o16, p + "alpha_ffn_q14")
            if trace is not None:
                trace[p + "ffn.in8"] = f8
                trace[p + "ffn.hid8"] = h8
                trace[p + "ffn.o16"] = o16.astype(np.int16)
                trace[p + "ffn.res16"] = x16.astype(np.int16)

        # ---- PMA pooling -> c_s int8
        pk8 = self._rq(x16, "pool.in", I8_MIN, I8_MAX).astype(np.int8)
        k8 = self._linear(pk8, "pool.k", I8_MIN, I8_MAX).astype(np.int8)
        v8 = self._linear(pk8, "pool.v", I8_MIN, I8_MAX).astype(np.int8)
        q8 = np.broadcast_to(t["pool.q8"].reshape(1, 1, self.d), (N, 1, self.d))
        if trace is not None:
            trace["pool.in8"] = pk8
            trace["pool.k8"] = k8
            trace["pool.v8"] = v8
        pv8 = self._attention(q8, k8, v8, key_mask, int(t["pool.logit_shift"][0]), "pool.pv", trace, "pool.")
        cs8 = self._linear(pv8[:, 0], "pool.o", I8_MIN, I8_MAX).astype(np.int8)  # (N,d)
        if trace is not None:
            trace["pool.cs8"] = cs8

        # ---- GRU over [ev ⊕ c_s]
        gx8 = np.concatenate([ev8, cs8], axis=-1).astype(np.int8)
        h8 = self._rq(h, "gru.h8", I8_MIN, I8_MAX).astype(np.int8)
        gi = self._linear(gx8, "gru.ih", I16_MIN, I16_MAX)  # (N,3g) Q3.12
        gh = self._linear(h8, "gru.hh", I16_MIN, I16_MAX)
        g = self.gru
        r = gate_lut(sat(gi[:, :g] + gh[:, :g], I16_MIN, I16_MAX), self.lut_sig)
        z = gate_lut(sat(gi[:, g : 2 * g] + gh[:, g : 2 * g], I16_MIN, I16_MAX), self.lut_sig)
        rn = rshift_round_even(r * gh[:, 2 * g :], 14)  # Q1.14 x Q3.12 -> Q3.12
        n = gate_lut(sat(gi[:, 2 * g :] + rn, I16_MIN, I16_MAX), self.lut_tanh)
        h_new = sat(rshift_round_even((Q14_ONE - z) * n, 14) + rshift_round_even(z * h.astype(np.int64), 14), I16_MIN, I16_MAX)
        if trace is not None:
            trace["gru.x8"] = gx8
            trace["gru.h8"] = h8
            trace["gru.gi_q12"] = gi.astype(np.int16)
            trace["gru.gh_q12"] = gh.astype(np.int16)
            trace["gru.r_q14"] = r.astype(np.int16)
            trace["gru.z_q14"] = z.astype(np.int16)
            trace["gru.n_q14"] = n.astype(np.int16)
            trace["gru.h_q14"] = h_new.astype(np.int16)

        # ---- context c = Linear([c_s ⊕ h])
        hc8 = self._rq(h_new, "gru.h8", I8_MIN, I8_MAX).astype(np.int8)
        cx8 = np.concatenate([cs8, hc8], axis=-1).astype(np.int8)
        c8 = self._linear(cx8, "ctx", I8_MIN, I8_MAX).astype(np.int8)
        if trace is not None:
            trace["ctx.in8"] = cx8
            trace["ctx.c8"] = c8

        # ---- pointer head
        pq8 = self._linear(c8, "ptr.q", I8_MIN, I8_MAX).astype(np.int8)  # (N,d)
        tk8 = self._rq(x16, "ptr.in", I8_MIN, I8_MAX).astype(np.int8)
        pk8 = self._linear(tk8, "ptr.k", I8_MIN, I8_MAX).astype(np.int8)  # (N,27,d)
        score = (pk8.astype(np.float64) @ pq8.astype(np.float64)[..., None])[..., 0].astype(np.int64)  # (N,27)
        pshift = int(t["ptr.logit_shift"][0])
        if pshift >= 0:
            s16 = rshift_round_even(score, pshift)
        else:
            s16 = score << (-pshift)
        ttype = np.clip(np.asarray(feats["type"]).astype(np.int64), 0, N_TOKEN_TYPES - 1)
        s16 = sat(sat(s16, I16_MIN, I16_MAX) + t["ptr.b_type_q8"][ttype].astype(np.int64), I16_MIN, I16_MAX)
        cand = np.asarray(feats["candidate"]).astype(np.int64)
        valid = (cand > 0) & present
        legal_bits = np.asarray(feats["legal"]).astype(np.int64).reshape(N, 1)
        legal = ((legal_bits >> np.arange(N_ACTIONS)) & 1).astype(bool)  # (N,16)
        logits = np.full((N, N_ACTIONS), MASK16, np.int64)
        rows, toks = np.nonzero(valid)
        logits[rows, cand[rows, toks] - 1] = s16[rows, toks]
        logits = np.where(legal, logits, MASK16)
        logits_t = np.where(logits == MASK16, MASK16, sat(logits << 1, I16_MIN, I16_MAX))
        pmask = logits != MASK16
        probs = softmax_int(logits_t, pmask, 0, self.lut_exp, self.lut_recip, trace, "ptr.")
        if trace is not None:
            trace["ptr.q8"] = pq8
            trace["ptr.tok8"] = tk8
            trace["ptr.k8"] = pk8
            trace["ptr.score32"] = score.astype(np.int32)
            trace["ptr.token_logit16"] = s16.astype(np.int16)
            trace["ptr.logits_q8"] = logits.astype(np.int16)
            trace["ptr.logits_t"] = logits_t.astype(np.int16)

        # ---- value head (stays int32)
        vacc = self._linear_acc(c8, "value")[:, 0]
        value = (vacc.astype(np.float64) * self.qp.scales["value.acc"]).astype(np.float32)
        if trace is not None:
            trace["value.acc32"] = vacc.astype(np.int32)

        return IntOutput(
            logits_q8=logits.astype(np.int16),
            logits_t=logits_t.astype(np.int16),
            probs=probs,
            value_acc=vacc.astype(np.int32),
            value=value,
            h=h_new.astype(np.int16),
            trace=trace if trace is not None else OrderedDict(),
        )

    def run_battle(self, feats: dict[str, np.ndarray], ev: np.ndarray | None = None, h0: np.ndarray | None = None):
        """Step through T decisions of one battle (arrays (T,...)); returns list of IntOutput."""
        T = feats["present"].shape[0]
        h = self.init_hidden(1) if h0 is None else np.asarray(h0, np.int16).reshape(1, -1)
        outs = []
        for i in range(T):
            step = {k: np.asarray(v)[i : i + 1] for k, v in feats.items()}
            e8 = None if ev is None else self.quantize_event(np.asarray(ev)[i : i + 1])
            out = self.forward(step, e8, h)
            h = out.h
            outs.append(out)
        return outs
