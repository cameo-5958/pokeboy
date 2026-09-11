"""Post-training quantisation of a PEP checkpoint and a fake-quant fp32 twin.

* `quantize(model, calib) -> QuantParams`: per-output-channel int8 weights (max-abs),
  per-tensor activation scales calibrated at the 99.99th percentile of |x| over real
  decisions (collected by running `FakeQuantPEP` in observe mode), every multiplier /
  shift / LUT / folded bias the integer path (models/pep_int.py) needs.
* `FakeQuantPEP(model, qp)`: drop-in `nn.Module` with PEP's forward signature that
  mimics the integer path in fp32 with straight-through rounding, so a QAT fine-tune
  can train the underlying PEP parameters against the quantised behaviour.  Weight and
  embedding scales are recomputed from the live parameters every forward; activation
  scales are fixed buffers taken from `qp.scales`.

Interpretations of §8.3 (see the report in tools/export_weights.py): mixed-scale GEMM
inputs (int8 features ⊕ embeddings, event ⊕ c_s, c_s ⊕ h) fold the per-piece input
scale into the weight columns before per-channel quantisation, so the kernel sees one
int8 vector; the token-type embedding is folded into the projection bias; Q/K and the
pointer scales are rounded up to the nearest value that makes the logit → 1/256-nat
conversion a pure shift.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from typing import Sequence

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from models.pep import EV_DIM, N_ACTIONS, N_TOKEN_TYPES, PEP, FEATURE_SCHEMA
from models.pep_int import (
    EV_SCALE,
    I8_MAX,
    I16_MAX,
    I32_MAX,
    I32_MIN,
    LOGIT_FRAC_BITS,
    TOKEN_EMBEDS,
    TOKEN_SLICES,
    TOKEN_TYPE_NAMES,
    QuantParams,
    make_luts,
    requant_params,
)

Q12 = 1.0 / 4096.0
Q14 = 1.0 / 16384.0
Q8N = 1.0 / (1 << LOGIT_FRAC_BITS)  # logit unit (nats)
INT16_HEADROOM = 1.25  # int16 tensors are scaled from the observed max with this margin

EMB_TABLES = {
    "emb.species": "species",
    "emb.move": "move",
    "emb.type": "type_",
    "emb.effect": "effect",
    "emb.matchup": "matchup",
    "emb.trainer": "trainer",
    "emb.item": "item",
    "emb.request": "request",
}
TOKEN_LINEARS = {
    "field": "in_field",
    "own_mon": "in_own_mon",
    "player_mon": "in_player_mon",
    "own_move": "in_own_move",
    "player_move": "in_player_move",
    "item": "in_item",
}


# --------------------------------------------------------------------------- STE helpers


def ste_round(x: torch.Tensor) -> torch.Tensor:
    return x + (torch.round(x) - x).detach()


def fq(x: torch.Tensor, scale: float | torch.Tensor, qmax: int, qmin: int | None = None) -> torch.Tensor:
    """Fake-quantise to a symmetric grid: round(x/scale) clamped to [qmin, qmax], back to fp32."""
    qmin = -qmax if qmin is None else qmin
    return torch.clamp(ste_round(x / scale), qmin, qmax) * scale


def weight_scales(w: torch.Tensor) -> torch.Tensor:
    """Per-output-channel max-abs int8 scales, shape (C, 1)."""
    s = w.detach().abs().amax(dim=1, keepdim=True) / I8_MAX
    return torch.where(s > 0, s, torch.ones_like(s))


# --------------------------------------------------------------------------- observer


class Observer:
    """Collects per-tensor |x| statistics (percentile and max) over calibration batches."""

    def __init__(self, pct: float = 99.99):
        self.pct = pct
        self.stats: dict[str, list[float]] = {}  # name -> [pct_max, abs_max]

    def add(self, name: str, x: torch.Tensor) -> None:
        a = x.detach().abs().flatten().float()
        if a.numel() == 0:
            return
        k = max(1, min(a.numel(), int(math.ceil(self.pct / 100.0 * a.numel()))))
        p = float(a.kthvalue(k).values)
        m = float(a.max())
        cur = self.stats.setdefault(name, [0.0, 0.0])
        cur[0] = max(cur[0], p)
        cur[1] = max(cur[1], m)

    def finalize(self) -> dict[str, tuple[float, float]]:
        return {k: (v[0], v[1]) for k, v in self.stats.items()}


# --------------------------------------------------------------------------- fake-quant module


class FakeQuantPEP(nn.Module):
    """fp32 twin of the integer path (same forward signature as PEP).

    observe=True: no quantisation, records activation statistics into `self.observer`.
    Otherwise `qp.scales` must contain every activation scale produced by `quantize`.
    """

    def __init__(self, model: PEP, qp: QuantParams | None = None, observe: bool = False, pct: float = 99.99):
        super().__init__()
        if qp is None and not observe:
            raise ValueError("qp required unless observe=True")
        self.model = model
        self.cfg = model.cfg
        self.observe = observe
        self.observer = Observer(pct) if observe else None
        self.scales: dict[str, float] = dict(qp.scales) if qp is not None else {}
        self.shifts: dict[str, int] = {}
        if qp is not None:
            for k, v in qp.tensors.items():
                if k.endswith("logit_shift"):
                    self.shifts[k] = int(v[0])
        self._ctx: tuple[torch.Tensor | None, torch.Tensor | None] = (None, None)

    # PEP surface the trainer relies on.
    def num_params(self, *a, **k):
        return self.model.num_params(*a, **k)

    def init_hidden(self, *a, **k):
        return self.model.init_hidden(*a, **k)

    # -- quant primitives -------------------------------------------------------

    def _act(self, x: torch.Tensor, name: str, bits: int, mask: torch.Tensor | None = None) -> torch.Tensor:
        if self.observe:
            sel = x if mask is None else x[mask]
            self.observer.add(name, sel)
            return x
        return fq(x, self.scales[name], I8_MAX if bits == 8 else I16_MAX)

    def _emb(self, table: str, ids: torch.Tensor) -> tuple[torch.Tensor, float]:
        emb: nn.Embedding = getattr(self.model.embed, EMB_TABLES[table])
        ids = ids.long().clamp(0, emb.num_embeddings - 1)
        if self.observe:
            return emb(ids), 1.0
        s = float(emb.weight.detach().abs().max() / I8_MAX) or 1.0
        w = fq(emb.weight, s, I8_MAX)
        return F.embedding(ids, w), s

    def _gemm(
        self,
        pieces: Sequence[tuple[torch.Tensor, float]],
        w: torch.Tensor,
        b: torch.Tensor | None,
        s_out: float | None,
        out_qmax: int,
        out_qmin: int | None = None,
    ) -> torch.Tensor:
        """Mixed-scale int8 GEMM twin: fold piece scales into the weight columns, quantise per channel."""
        if self.observe:
            y = F.linear(torch.cat([p for p, _ in pieces], dim=-1), w, b)
            return y
        s_ref = pieces[0][1]
        cols = []
        xs = []
        for x, s in pieces:
            cols.append(torch.full((x.shape[-1],), s / s_ref, dtype=w.dtype, device=w.device))
            xs.append(x * (s_ref / s))
        wf = w * torch.cat(cols)[None, :]
        sw = weight_scales(wf)
        wq = fq(wf, sw, I8_MAX)
        bq = None
        if b is not None:
            sb = (sw[:, 0] * s_ref).detach()
            bq = torch.clamp(ste_round(b / sb), I32_MIN, I32_MAX) * sb
        y = F.linear(torch.cat(xs, dim=-1), wq, bq)
        if s_out is None:
            return y
        return fq(y, s_out, out_qmax, out_qmin)

    def _softmax(self, scores: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """scores in nats (..., L); mask True = participates.  Quant mode mirrors softmax_int."""
        neg = torch.finfo(scores.dtype).min
        if self.observe:
            return torch.softmax(scores.masked_fill(~mask, neg), dim=-1)
        m = scores.masked_fill(~mask, neg).amax(dim=-1, keepdim=True)
        d = torch.clamp(ste_round((m - scores) / Q8N), 0, I16_MAX) * Q8N
        p = torch.exp(-d) * mask.to(scores.dtype)
        p = p / p.sum(dim=-1, keepdim=True).clamp_min(1e-12)
        return torch.clamp(ste_round(p * 256.0), 0, 255) / 256.0

    def _attention(self, q, k, v, key_mask, s_qk: float, dh: int, pv_name: str) -> torch.Tensor:
        N, Lq, d = q.shape
        L = k.shape[1]
        h = self.cfg.heads
        qh = q.view(N, Lq, h, dh).transpose(1, 2)
        kh = k.view(N, L, h, dh).transpose(1, 2)
        vh = v.view(N, L, h, dh).transpose(1, 2)
        scores = torch.matmul(qh, kh.transpose(-1, -2)) / math.sqrt(dh)
        probs = self._softmax(scores, key_mask[:, None, None, :])
        out = torch.matmul(probs, vh).transpose(1, 2).reshape(N, Lq, d)
        return self._act(out, pv_name, 8)

    def _rezero(self, x: torch.Tensor, branch: torch.Tensor, alpha: torch.Tensor, branch_name: str) -> torch.Tensor:
        b = self._act(branch, branch_name, 16)
        if self.observe:
            return self._act(x + alpha * b, "res", 16, self._ctx[0])
        s_res, s_b = self.scales["res"], self.scales[branch_name]
        a = ste_round(alpha * (s_b / s_res) * 16384.0).clamp(-32768, 32767) / 16384.0 * (s_res / s_b)
        return fq(x + fq(a * b, s_res, I16_MAX), s_res, I16_MAX)

    # -- forward ----------------------------------------------------------------

    def forward_seq(
        self,
        features: dict[str, torch.Tensor],
        ev: torch.Tensor | None = None,
        h0: torch.Tensor | None = None,
        mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Whole-sequence API with PEP.forward_seq semantics, stepping the fake-quant forward per decision."""
        B, T = features["present"].shape[:2]
        dev = features["present"].device
        h = self.model.init_hidden(B, dev) if h0 is None else h0.float()
        logits_t, value_t, h_t = [], [], []
        for t in range(T):
            step = {k: v[:, t] for k, v in features.items() if v.dim() >= 2 and v.shape[1] == T}
            ev_t = None if ev is None else ev[:, t]
            lg, val, h_new = self.forward(step, ev_t, h, None if mask is None else mask[:, t])
            h = h_new if mask is None else torch.where(mask[:, t, None], h_new, h)
            logits_t.append(lg); value_t.append(val); h_t.append(h)
        return torch.stack(logits_t, 1), torch.stack(value_t, 1), torch.stack(h_t, 1)

    def forward(
        self,
        features: dict[str, torch.Tensor],
        ev: torch.Tensor | None = None,
        h: torch.Tensor | None = None,
        row_mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        m = self.model
        cfg = self.cfg
        d, g = cfg.d, cfg.gru
        dh = d // cfg.heads
        present = features["present"].bool()
        N = present.shape[0]
        dev = present.device
        key_mask = present.clone()
        key_mask[:, 0] |= ~present.any(dim=1)
        if ev is None:
            ev = torch.zeros(N, EV_DIM, device=dev)
        if h is None:
            h = torch.zeros(N, g, device=dev)
        ev = ev.float()
        h = h.float()
        if row_mask is None:
            row_mask = torch.ones(N, dtype=torch.bool, device=dev)
        tok_mask = present & row_mask[:, None]
        self._ctx = (tok_mask, row_mask)
        cat = features["cat"].long()
        x_f = features["f"].float() / 127.0
        ttype = features["type"].long().clamp(0, N_TOKEN_TYPES - 1)

        # ---- token projections
        x = torch.zeros(N, 27, d, device=dev, dtype=x_f.dtype)
        for name in TOKEN_TYPE_NAMES:
            sl = TOKEN_SLICES[name]
            pieces: list[tuple[torch.Tensor, float]] = [(x_f[:, sl], 1.0 / 127.0)]
            for tab, col in TOKEN_EMBEDS[name]:
                pieces.append(self._emb(tab, cat[:, sl, col]))
            lin: nn.Linear = getattr(m.embed, TOKEN_LINEARS[name])
            tidx = TOKEN_TYPE_NAMES.index(name)
            bias = lin.bias + m.embed.token_type.weight[tidx]
            x[:, sl] = self._gemm(pieces, lin.weight, bias, None if self.observe else self.scales["res"], I16_MAX)
        x = self._act(x, "res", 16, tok_mask)

        # ---- encoder
        for l, layer in enumerate(m.layers):
            p = f"layers.{l}."
            a = self._act(x, p + "attn.in", 8, tok_mask)
            s_in = self.scales.get(p + "attn.in", 1.0)
            s_qk = self.scales.get(p + "attn.qk", 1.0)
            q = self._act(self._gemm([(a, s_in)], layer.attn.q.weight, None, None, I8_MAX), p + "attn.qk", 8, tok_mask)
            k = self._act(self._gemm([(a, s_in)], layer.attn.k.weight, None, None, I8_MAX), p + "attn.qk", 8, tok_mask)
            v = self._act(self._gemm([(a, s_in)], layer.attn.v.weight, None, None, I8_MAX), p + "attn.v", 8, tok_mask)
            pv = self._attention(q, k, v, key_mask, s_qk, dh, p + "attn.pv")
            o = self._gemm([(pv, self.scales.get(p + "attn.pv", 1.0))], layer.attn.o.weight, None, None, I16_MAX)
            x = self._rezero(x, o, layer.alpha_attn, p + "attn.branch")

            f_in = self._act(x, p + "ffn.in", 8, tok_mask)
            hid = self._gemm([(f_in, self.scales.get(p + "ffn.in", 1.0))], layer.ff1.weight, layer.ff1.bias, None, I8_MAX)
            hid = self._act(F.relu(hid), p + "ffn.hid", 8, tok_mask)
            o = self._gemm([(hid, self.scales.get(p + "ffn.hid", 1.0))], layer.ff2.weight, layer.ff2.bias, None, I16_MAX)
            x = self._rezero(x, o, layer.alpha_ffn, p + "ffn.branch")

        # ---- PMA pool
        pool = m.pool
        pin = self._act(x, "pool.in", 8, tok_mask)
        s_pin = self.scales.get("pool.in", 1.0)
        s_pqk = self.scales.get("pool.qk", 1.0)
        q_seed = F.linear(pool.seed, pool.q.weight)  # (d,) constant
        q_seed = self._act(q_seed, "pool.qk", 8)
        k = self._act(self._gemm([(pin, s_pin)], pool.k.weight, None, None, I8_MAX), "pool.qk", 8, tok_mask)
        v = self._act(self._gemm([(pin, s_pin)], pool.v.weight, None, None, I8_MAX), "pool.v", 8, tok_mask)
        pv = self._attention(q_seed.view(1, 1, d).expand(N, 1, d), k, v, key_mask, s_pqk, dh, "pool.pv")[:, 0]
        cs = self._act(self._gemm([(pv, self.scales.get("pool.pv", 1.0))], pool.o.weight, None, None, I8_MAX), "pool.cs", 8, row_mask)
        s_cs = self.scales.get("pool.cs", 1.0)

        # ---- GRU over [ev ⊕ c_s]
        gru = m.gru
        ev_q = ev if self.observe else fq(ev, EV_SCALE, I8_MAX)
        h_q = h if self.observe else fq(h, Q14, I16_MAX)
        h8 = self._act(h_q, "gru.h8", 8, row_mask)
        s_h8 = self.scales.get("gru.h8", 1.0)
        gi = self._gemm([(ev_q, EV_SCALE), (cs, s_cs)], gru.weight_ih, gru.bias_ih, None if self.observe else Q12, I16_MAX)
        gh = self._gemm([(h8, s_h8)], gru.weight_hh, gru.bias_hh, None if self.observe else Q12, I16_MAX)
        if self.observe:
            self.observer.add("gru.pre", torch.cat([gi, gh], dim=-1)[row_mask])
        gi_r, gi_z, gi_n = gi.split(g, dim=-1)
        gh_r, gh_z, gh_n = gh.split(g, dim=-1)
        if self.observe:
            r = torch.sigmoid(gi_r + gh_r)
            z = torch.sigmoid(gi_z + gh_z)
            n = torch.tanh(gi_n + r * gh_n)
            h_new = (1 - z) * n + z * h
        else:
            r = fq(torch.sigmoid(fq(gi_r + gh_r, Q12, I16_MAX)), Q14, I16_MAX)
            z = fq(torch.sigmoid(fq(gi_z + gh_z, Q12, I16_MAX)), Q14, I16_MAX)
            rn = fq(r * gh_n, Q12, I16_MAX)
            n = fq(torch.tanh(fq(gi_n + rn, Q12, I16_MAX)), Q14, I16_MAX)
            h_new = fq(fq((1 - z) * n, Q14, I16_MAX) + fq(z * h_q, Q14, I16_MAX), Q14, I16_MAX)

        # ---- context
        hc8 = self._act(h_new, "gru.h8", 8, row_mask)
        c = self._act(self._gemm([(cs, s_cs), (hc8, s_h8)], m.ctx.weight, m.ctx.bias, None, I8_MAX), "ctx.c", 8, row_mask)
        s_c = self.scales.get("ctx.c", 1.0)

        # ---- pointer head
        pq = self._act(self._gemm([(c, s_c)], m.ptr_q.weight, None, None, I8_MAX), "ptr.qk", 8, row_mask)
        tok = self._act(x, "ptr.in", 8, tok_mask)
        pk = self._act(self._gemm([(tok, self.scales.get("ptr.in", 1.0))], m.ptr_k.weight, None, None, I8_MAX), "ptr.qk", 8, tok_mask)
        score = torch.einsum("nd,nld->nl", pq, pk) / math.sqrt(d)
        b_type = m.b_type
        if not self.observe:
            score = fq(score, Q8N, I16_MAX)
            b_type = fq(b_type, Q8N, I16_MAX)
        score = score + b_type[ttype]
        if not self.observe:
            score = fq(score, Q8N, I16_MAX)
        cand = features["candidate"].long()
        valid = (cand > 0) & present
        target = torch.where(valid, cand - 1, torch.full_like(cand, N_ACTIONS))
        logits = torch.full((N, N_ACTIONS + 1), float("-inf"), device=dev, dtype=torch.float32)
        logits = logits.scatter(1, target, score.float())[:, :N_ACTIONS]
        logits = logits.masked_fill(~PEP.legal_mask(features["legal"]), float("-inf"))

        # ---- value (int32 accumulator; no output quantisation)
        value = self._gemm([(c, s_c)], m.value.weight, m.value.bias, None, I32_MAX).float().squeeze(-1)
        return logits, value, h_new


# --------------------------------------------------------------------------- calibration


def _iter_calibration(calib, batch_battles: int):
    """Yield (features dict of torch tensors (N,...), ev (N,64), row_mask (N,), carry_id) per time step."""
    from models.pep import features_to_tensors
    from models.pep_data import Battle, FeaturesDataset, collate_battles

    if isinstance(calib, dict):  # flat decisions (N, ...)
        feats = features_to_tensors(calib)
        ev = torch.as_tensor(calib["event"]) if "event" in calib else None
        yield feats, ev, None, None
        return
    battles = list(calib.battles) if isinstance(calib, FeaturesDataset) else list(calib)
    assert all(isinstance(b, Battle) for b in battles)
    for i in range(0, len(battles), batch_battles):
        arrays = collate_battles(battles[i : i + batch_battles])
        B, T = arrays["mask"].shape
        for t in range(T):
            step = {k: arrays[k][:, t] for k in ("type", "present", "candidate", "cat", "f", "legal")}
            yield features_to_tensors(step), torch.as_tensor(arrays["event"][:, t]), torch.as_tensor(arrays["mask"][:, t]), (i, t)


@torch.no_grad()
def collect_stats(model: PEP, calib, pct: float = 99.99, batch_battles: int = 64) -> dict[str, tuple[float, float]]:
    """Run the fp32 structure over calibration decisions (h carried through battles); return |x| stats."""
    model.eval()
    fq_model = FakeQuantPEP(model, observe=True, pct=pct)
    h = None
    last_batch = None
    for feats, ev, row_mask, carry in _iter_calibration(calib, batch_battles):
        if carry is None or carry[1] == 0 or carry[0] != last_batch:
            h = None
        last_batch = None if carry is None else carry[0]
        _, _, h_new = fq_model(feats, ev, h, row_mask)
        h = h_new if row_mask is None or h is None else torch.where(row_mask[:, None], h_new, h)
    return fq_model.observer.finalize()


def load_calibration(path, rows: int = 4096, seed: int = 0):
    """Battles from parquet files under `path` (files read in order until >= rows decisions)."""
    from models.pep_data import FeaturesDataset, _parquet_files, load_rows

    battles = []
    n = 0
    for f in _parquet_files(path):
        ds = FeaturesDataset(table=load_rows(f))
        for b in ds.battles:
            battles.append(b)
            n += len(b)
            if n >= rows:
                return battles
    return battles


# --------------------------------------------------------------------------- PTQ builder


def _np(t: torch.Tensor) -> np.ndarray:
    return t.detach().cpu().double().numpy()


class _Builder:
    def __init__(self, model: PEP, stats: dict[str, tuple[float, float]]):
        self.m = model
        self.stats = stats
        self.t: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self.s: "OrderedDict[str, float]" = OrderedDict()

    # -- stats -----------------------------------------------------------------

    def pct(self, name: str) -> float:
        return self.stats[name][0]

    def amax(self, name: str) -> float:
        return self.stats[name][1]

    def s8(self, name: str) -> float:
        """int8 activation scale from the percentile statistic (falls back to 1/127)."""
        v = self.pct(name)
        s = v / I8_MAX if v > 0 else 1.0 / I8_MAX
        self.s[name] = s
        return s

    def s16(self, name: str) -> float:
        v = self.amax(name) * INT16_HEADROOM
        s = v / I16_MAX if v > 0 else 1.0 / I16_MAX
        self.s[name] = s
        return s

    # -- tensors ---------------------------------------------------------------

    def rq(self, name: str, real: float) -> None:
        mult, shift = requant_params(real)
        self.t[name + ".mult"] = np.array([mult], np.int32)
        self.t[name + ".shift"] = np.array([shift], np.int8)

    def gemm(self, name: str, w: np.ndarray, b: np.ndarray | None, in_scales: Sequence[float], widths: Sequence[int], s_out: float | None) -> float:
        """Fold input piece scales into columns, quantise per channel, emit w/b/mult/shift.  Returns s_ref."""
        assert sum(widths) == w.shape[1], (name, widths, w.shape)
        s_ref = in_scales[0]
        col = np.concatenate([np.full(k, s / s_ref) for s, k in zip(in_scales, widths)])
        wf = w * col[None, :]
        sw = np.abs(wf).max(axis=1) / I8_MAX
        sw[sw == 0] = 1.0
        self.t[name + ".w"] = np.clip(np.rint(wf / sw[:, None]), -I8_MAX, I8_MAX).astype(np.int8)
        if b is not None:
            self.t[name + ".b"] = np.clip(np.rint(b / (sw * s_ref)), I32_MIN, I32_MAX).astype(np.int32)
        if s_out is not None:
            ms = [requant_params(float(x)) for x in sw * s_ref / s_out]
            self.t[name + ".mult"] = np.array([m for m, _ in ms], np.int32)
            self.t[name + ".shift"] = np.array([sh for _, sh in ms], np.int8)
        else:
            self.s[name + ".acc"] = float(sw[0] * s_ref)  # single-channel int32 output (value head)
        return s_ref

    def qk_scale(self, name: str, dh_or_d: int) -> tuple[float, int]:
        """Shared Q/K scale rounded up so s² * 2^8 / sqrt(dh) is a power of two -> pure shift."""
        v = self.pct(name)
        s_cal = v / I8_MAX if v > 0 else 1.0 / I8_MAX
        shift = math.floor(math.log2(math.sqrt(dh_or_d) / ((1 << LOGIT_FRAC_BITS) * s_cal * s_cal)))
        s = math.sqrt(2.0 ** (-shift) * math.sqrt(dh_or_d) / (1 << LOGIT_FRAC_BITS))
        assert s >= s_cal * (1 - 1e-12)
        self.s[name] = s
        return s, shift

    def branch(self, name: str, alpha: float) -> tuple[float, int]:
        s_res = self.s["res"]
        v = self.amax(name) * INT16_HEADROOM
        s_b = v / I16_MAX if v > 0 else s_res
        if abs(alpha) * s_b / s_res >= 1.999:
            s_b = 1.999 * s_res / abs(alpha)
        self.s[name] = s_b
        a_q14 = int(np.clip(round(alpha * s_b / s_res * 16384.0), -32768, 32767))
        return s_b, a_q14

    # -- build -----------------------------------------------------------------

    def build(self) -> None:
        m = self.m
        cfg = m.cfg
        d, g, dh = cfg.d, cfg.gru, cfg.d // cfg.heads
        t, s = self.t, self.s

        # embedding tables (per-table scale)
        for tname, attr in EMB_TABLES.items():
            w = _np(getattr(m.embed, attr).weight)
            se = float(np.abs(w).max() / I8_MAX) or 1.0
            s[tname] = se
            t[tname] = np.clip(np.rint(w / se), -I8_MAX, I8_MAX).astype(np.int8)
        s["ev"] = EV_SCALE

        # token projections -> int16 stream
        s_res = self.s16("res")
        for i, name in enumerate(TOKEN_TYPE_NAMES):
            lin = getattr(m.embed, TOKEN_LINEARS[name])
            w = _np(lin.weight)
            b = _np(lin.bias) + _np(m.embed.token_type.weight[i])
            in_scales = [1.0 / I8_MAX] + [s[tab] for tab, _ in TOKEN_EMBEDS[name]]
            widths = [48] + [t[tab].shape[1] for tab, _ in TOKEN_EMBEDS[name]]
            self.gemm(f"embed.{name}", w, b, in_scales, widths, s_res)

        # encoder layers
        for l, layer in enumerate(m.layers):
            p = f"layers.{l}."
            s_in = self.s8(p + "attn.in")
            self.rq(p + "attn.in", s_res / s_in)
            s_qk, shift = self.qk_scale(p + "attn.qk", dh)
            t[p + "attn.logit_shift"] = np.array([shift], np.int8)
            self.gemm(p + "attn.q", _np(layer.attn.q.weight), None, [s_in], [d], s_qk)
            self.gemm(p + "attn.k", _np(layer.attn.k.weight), None, [s_in], [d], s_qk)
            s_v = self.s8(p + "attn.v")
            self.gemm(p + "attn.v", _np(layer.attn.v.weight), None, [s_in], [d], s_v)
            s_pv = self.s8(p + "attn.pv")
            self.rq(p + "attn.pv", s_v / 256.0 / s_pv)
            s_b, a_q14 = self.branch(p + "attn.branch", float(layer.alpha_attn.detach().item()))
            self.gemm(p + "attn.o", _np(layer.attn.o.weight), None, [s_pv], [d], s_b)
            t[p + "alpha_attn_q14"] = np.array([a_q14], np.int16)

            s_fin = self.s8(p + "ffn.in")
            self.rq(p + "ffn.in", s_res / s_fin)
            s_hid = self.s8(p + "ffn.hid")
            self.gemm(p + "ffn.f1", _np(layer.ff1.weight), _np(layer.ff1.bias), [s_fin], [d], s_hid)
            s_b2, a2_q14 = self.branch(p + "ffn.branch", float(layer.alpha_ffn.detach().item()))
            self.gemm(p + "ffn.f2", _np(layer.ff2.weight), _np(layer.ff2.bias), [s_hid], [cfg.ffn], s_b2)
            t[p + "alpha_ffn_q14"] = np.array([a2_q14], np.int16)

        # PMA pool
        pool = m.pool
        s_pin = self.s8("pool.in")
        self.rq("pool.in", s_res / s_pin)
        s_pqk, pshift = self.qk_scale("pool.qk", dh)
        t["pool.logit_shift"] = np.array([pshift], np.int8)
        q_seed = _np(pool.q.weight) @ _np(pool.seed)
        t["pool.q8"] = np.clip(np.rint(q_seed / s_pqk), -I8_MAX, I8_MAX).astype(np.int8)
        self.gemm("pool.k", _np(pool.k.weight), None, [s_pin], [d], s_pqk)
        s_pv = self.s8("pool.v")
        self.gemm("pool.v", _np(pool.v.weight), None, [s_pin], [d], s_pv)
        s_ppv = self.s8("pool.pv")
        self.rq("pool.pv", s_pv / 256.0 / s_ppv)
        s_cs = self.s8("pool.cs")
        self.gemm("pool.o", _np(pool.o.weight), None, [s_ppv], [d], s_cs)

        # GRU
        v = self.pct("gru.h8") if "gru.h8" in self.stats else 0.0
        s_h8 = min(v, 1.0) / I8_MAX if v > 0 else 1.0 / I8_MAX
        s["gru.h8"] = s_h8
        self.rq("gru.h8", Q14 / s_h8)
        self.gemm("gru.ih", _np(m.gru.weight_ih), _np(m.gru.bias_ih), [EV_SCALE, s_cs], [EV_DIM, d], Q12)
        self.gemm("gru.hh", _np(m.gru.weight_hh), _np(m.gru.bias_hh), [s_h8], [g], Q12)

        # context
        s_c = self.s8("ctx.c")
        self.gemm("ctx", _np(m.ctx.weight), _np(m.ctx.bias), [s_cs, s_h8], [d, g], s_c)

        # pointer head
        s_tin = self.s8("ptr.in")
        self.rq("ptr.in", s_res / s_tin)
        s_ptr, qshift = self.qk_scale("ptr.qk", d)
        t["ptr.logit_shift"] = np.array([qshift], np.int8)
        self.gemm("ptr.q", _np(m.ptr_q.weight), None, [s_c], [d], s_ptr)
        self.gemm("ptr.k", _np(m.ptr_k.weight), None, [s_tin], [d], s_ptr)
        t["ptr.b_type_q8"] = np.clip(np.rint(_np(m.b_type) * (1 << LOGIT_FRAC_BITS)), -32768, 32767).astype(np.int16)

        # value head: int32 accumulator, scale exported
        self.gemm("value", _np(m.value.weight), _np(m.value.bias), [s_c], [d], None)

        # LUTs
        t.update(make_luts())


def build_quant_params(model: PEP, stats: dict[str, tuple[float, float]]) -> QuantParams:
    b = _Builder(model, stats)
    b.build()
    cfg = model.cfg
    config = {
        "d": cfg.d,
        "layers": cfg.layers,
        "heads": cfg.heads,
        "ffn": cfg.ffn,
        "gru": cfg.gru,
        "emb_species": cfg.emb_species,
        "emb_move": cfg.emb_move,
        "emb_matchup": cfg.emb_matchup,
        "emb_small": cfg.emb_small,
    }
    return QuantParams(config=config, feature_schema=FEATURE_SCHEMA, tensors=b.t, scales=b.s)


def quantize(model: PEP, calib, pct: float = 99.99, batch_battles: int = 64) -> QuantParams:
    """Post-training quantisation: calibrate activation scales on `calib`, then build QuantParams.

    calib: FeaturesDataset / list[Battle] (h carried through each battle) or a flat dict of
    decision arrays (N, ...) as produced by pep_data.decode_features_batch (+ optional "event").
    """
    stats = collect_stats(model, calib, pct=pct, batch_battles=batch_battles)
    qp = build_quant_params(model, stats)
    qp.stats = stats  # type: ignore[attr-defined]  (kept for reports; not exported)
    return qp
