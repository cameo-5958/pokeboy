"""Decoding of raw pkai::Features bytes and a battle-grouped dataset for PEP.

The byte layout mirrors pkai/python/pkai.py (ctypes) exactly:

    Token    (60 B): type u8 | index u8 | present u8 | candidate u8 | cat u16[4] | f i8[48]
    Features (1626 B): tokens Token[27] | count u8 | (pad) | legal u16 | request_kind u8 | (pad)

`decode_features()` turns one Features blob into numpy arrays; `FeaturesDataset`
loads parquet rows (see train_pep.py for the column contract), groups them by
battle_id ordered by seq, and yields per-battle arrays for recurrent training.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Iterator, Sequence

import numpy as np

FEAT = 48
MAX_TOKENS = 27
N_ACTIONS = 16
EV_DIM = 64
FEATURE_SCHEMA = "pkai-features-v1"

TOKEN_DTYPE = np.dtype(
    [
        ("type", "u1"),
        ("index", "u1"),
        ("present", "u1"),
        ("candidate", "u1"),
        ("cat", "<u2", (4,)),
        ("f", "i1", (FEAT,)),
    ]
)
assert TOKEN_DTYPE.itemsize == 60

FEATURES_DTYPE = np.dtype(
    {
        "names": ["tokens", "count", "legal", "request_kind"],
        "formats": [(TOKEN_DTYPE, (MAX_TOKENS,)), "u1", "<u2", "u1"],
        "offsets": [0, 1620, 1622, 1624],
        "itemsize": 1626,
    }
)
FEATURES_BYTES = FEATURES_DTYPE.itemsize  # 1626

# Fixed token order (contract): 0 field, 1-6 own mon, 7-12 player mon,
# 13-16 own moves, 17-20 player moves, 21-26 items.
TOKEN_TYPES = np.array([0] + [1] * 6 + [2] * 6 + [3] * 4 + [4] * 4 + [5] * 6, dtype=np.uint8)
assert TOKEN_TYPES.shape == (MAX_TOKENS,)


def _split(rec: np.ndarray) -> dict[str, np.ndarray]:
    """Structured Features record(s) -> dict of plain arrays (leading dims kept)."""
    tok = rec["tokens"]
    # np.array(...) copies the strided struct views into contiguous arrays (0-d stays 0-d)
    return {
        "type": np.array(tok["type"]),
        "index": np.array(tok["index"]),
        "present": np.array(tok["present"]),
        "candidate": np.array(tok["candidate"]),
        "cat": np.array(tok["cat"]),
        "f": np.array(tok["f"]),
        "count": np.array(rec["count"]),
        "legal": np.array(rec["legal"]),
        "request_kind": np.array(rec["request_kind"]),
    }


def decode_features(buf: bytes | bytearray | memoryview | np.ndarray) -> dict[str, np.ndarray]:
    """Decode one raw pkai::Features blob.

    Returns type/index/present/candidate (27,) u8, cat (27,4) u16, f (27,48) i8,
    count u8, legal u16, request_kind u8 (the scalars as 0-d arrays).
    """
    a = np.frombuffer(buf, dtype=np.uint8)
    if a.size != FEATURES_BYTES:
        raise ValueError(f"Features blob must be {FEATURES_BYTES} bytes, got {a.size}")
    return _split(a.view(FEATURES_DTYPE)[0])


def decode_features_batch(blobs: Sequence[bytes] | np.ndarray) -> dict[str, np.ndarray]:
    """Decode N blobs at once (arrays get a leading N axis)."""
    if isinstance(blobs, np.ndarray) and blobs.dtype == np.uint8 and blobs.ndim == 2:
        raw = blobs
    else:
        raw = np.frombuffer(b"".join(bytes(b) for b in blobs), dtype=np.uint8).reshape(-1, FEATURES_BYTES)
    if raw.shape[1] != FEATURES_BYTES:
        raise ValueError(f"expected rows of {FEATURES_BYTES} bytes, got {raw.shape[1]}")
    rec = np.ascontiguousarray(raw).view(FEATURES_DTYPE).reshape(raw.shape[0])
    return _split(rec)


def encode_features(
    *,
    present: np.ndarray,
    candidate: np.ndarray,
    cat: np.ndarray,
    f: np.ndarray,
    legal: int,
    request_kind: int = 0,
    index: np.ndarray | None = None,
    type_: np.ndarray | None = None,
) -> bytes:
    """Inverse of decode_features (used by tests / synthetic data)."""
    rec = np.zeros(1, dtype=FEATURES_DTYPE)
    tok = rec["tokens"][0]
    tok["type"] = TOKEN_TYPES if type_ is None else np.asarray(type_, dtype=np.uint8)
    tok["index"] = np.arange(MAX_TOKENS, dtype=np.uint8) if index is None else np.asarray(index, dtype=np.uint8)
    tok["present"] = np.asarray(present, dtype=np.uint8)
    tok["candidate"] = np.asarray(candidate, dtype=np.uint8)
    tok["cat"] = np.asarray(cat, dtype=np.uint16)
    tok["f"] = np.asarray(f, dtype=np.int8)
    rec["count"] = MAX_TOKENS
    rec["legal"] = legal
    rec["request_kind"] = request_kind
    return rec.tobytes()


def _parquet_files(path: str | os.PathLike) -> list[str]:
    p = str(path)
    if os.path.isdir(p):
        files = sorted(glob.glob(os.path.join(p, "**", "*.parquet"), recursive=True))
    else:
        files = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
    if not files:
        raise FileNotFoundError(f"no parquet files under {path}")
    return files


def _binary_column(col, itemsize: int) -> np.ndarray:
    """Fixed-width binary column -> (N, itemsize) uint8; rows of other sizes raise."""
    import pyarrow as pa

    col = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    n = len(col)
    if n == 0:
        return np.zeros((0, itemsize), dtype=np.uint8)
    if pa.types.is_fixed_size_binary(col.type):
        if col.type.byte_width != itemsize:
            raise ValueError(f"fixed binary width {col.type.byte_width} != {itemsize}")
        buf = col.buffers()[1]
        return np.frombuffer(buf, dtype=np.uint8, count=n * itemsize, offset=col.offset * itemsize).reshape(n, itemsize)
    # variable-width binary: check all offsets are uniformly spaced, else fall back
    offsets = np.frombuffer(col.buffers()[1], dtype=np.int64 if pa.types.is_large_binary(col.type) else np.int32)
    offsets = offsets[col.offset : col.offset + n + 1]
    if np.all(np.diff(offsets) == itemsize) and col.null_count == 0:
        data = np.frombuffer(col.buffers()[2], dtype=np.uint8)
        return data[offsets[0] : offsets[-1]].reshape(n, itemsize)
    rows = col.to_pylist()
    out = np.zeros((n, itemsize), dtype=np.uint8)
    for i, r in enumerate(rows):
        if r is None:
            continue
        if len(r) != itemsize:
            raise ValueError(f"row {i}: {len(r)} bytes != {itemsize}")
        out[i] = np.frombuffer(r, dtype=np.uint8)
    return out


def _list_column(col, width: int) -> np.ndarray:
    """list<float32> column -> (N, width) float32 with NaN rows where null/malformed."""
    import pyarrow as pa

    col = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    n = len(col)
    out = np.full((n, width), np.nan, dtype=np.float32)
    if n == 0:
        return out
    if pa.types.is_fixed_size_list(col.type):
        vals = col.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(n, -1)
        if vals.shape[1] == width:
            out[:] = vals
        valid = ~np.asarray(col.is_null().to_numpy(zero_copy_only=False))
        out[~valid] = np.nan
        return out
    offsets = np.asarray(col.offsets.to_numpy(zero_copy_only=False), dtype=np.int64)
    vals = np.asarray(col.values.to_numpy(zero_copy_only=False), dtype=np.float32)
    lengths = np.diff(offsets)
    ok = lengths == width
    if col.null_count:
        ok &= ~np.asarray(col.is_null().to_numpy(zero_copy_only=False))
    idx = np.nonzero(ok)[0]
    if idx.size:
        gather = offsets[idx][:, None] + np.arange(width)[None, :]
        out[idx] = vals[gather]
    return out


def _optional_numpy(table, name: str, dtype, default):
    if name not in table.column_names:
        return np.full(table.num_rows, default, dtype=dtype)
    return table.column(name).to_numpy(zero_copy_only=False).astype(dtype)


@dataclass
class Battle:
    """One battle's decisions in seq order; every array has leading axis T."""

    battle_id: str
    type: np.ndarray  # (T,27) u8
    present: np.ndarray  # (T,27) u8
    candidate: np.ndarray  # (T,27) u8
    cat: np.ndarray  # (T,27,4) u16
    f: np.ndarray  # (T,27,48) i8
    legal: np.ndarray  # (T,) u16
    request_kind: np.ndarray  # (T,) u8
    event: np.ndarray  # (T,64) f32
    teacher_probs: np.ndarray  # (T,16) f32, NaN rows = missing
    teacher_value: np.ndarray  # (T,) f32, -1 = unknown
    action: np.ndarray  # (T,) i8
    won: np.ndarray  # (T,) bool
    round: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))

    def __len__(self) -> int:
        return int(self.legal.shape[0])


def load_rows(path: str | os.PathLike, columns: Sequence[str] | None = None):
    """Read all parquet files under `path` into one pyarrow Table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    tables = [pq.read_table(f, columns=list(columns) if columns else None) for f in _parquet_files(path)]
    return pa.concat_tables(tables, promote_options="default") if len(tables) > 1 else tables[0]


class FeaturesDataset:
    """Parquet rows grouped into `Battle`s, ordered by seq within battle_id.

    Rows are decoded eagerly into compact numpy arrays (~1.7 KB / decision).
    """

    def __init__(self, path: str | os.PathLike | None = None, *, table=None, max_battles: int | None = None):
        if table is None:
            if path is None:
                raise ValueError("path or table required")
            table = load_rows(path)
        self.battles: list[Battle] = self._group(table, max_battles)

    @staticmethod
    def _group(table, max_battles: int | None) -> list[Battle]:
        n = table.num_rows
        if n == 0:
            return []
        ids = np.asarray(table.column("battle_id").to_numpy(zero_copy_only=False)).astype(str)
        seq = table.column("seq").to_numpy(zero_copy_only=False).astype(np.int64)
        uniq, inv = np.unique(ids, return_inverse=True)
        order = np.lexsort((seq, inv))
        feats = decode_features_batch(_binary_column(table.column("features"), FEATURES_BYTES)[order])
        legal_col = (
            table.column("legal").to_numpy(zero_copy_only=False).astype(np.uint16)
            if "legal" in table.column_names
            else feats["legal"]
        )
        rk_col = (
            table.column("request_kind").to_numpy(zero_copy_only=False).astype(np.uint8)
            if "request_kind" in table.column_names
            else feats["request_kind"]
        )
        if "event" in table.column_names:
            ev_raw = _binary_column(table.column("event"), EV_DIM * 4)
            event = np.ascontiguousarray(ev_raw).view(np.float32).reshape(n, EV_DIM)
        else:
            event = np.zeros((n, EV_DIM), dtype=np.float32)
        if "teacher_probs" in table.column_names:
            tp = _list_column(table.column("teacher_probs"), N_ACTIONS)
        else:
            tp = np.full((n, N_ACTIONS), np.nan, dtype=np.float32)
        tv = _optional_numpy(table, "teacher_value", np.float32, -1.0)
        action = _optional_numpy(table, "action", np.int8, -1)
        won = _optional_numpy(table, "won", np.bool_, False)
        rnd = _optional_numpy(table, "round", np.int32, 0)

        legal_s, rk_s, ev_s, tp_s = legal_col[order], rk_col[order], event[order], tp[order]
        tv_s, act_s, won_s, rnd_s, inv_s = tv[order], action[order], won[order], rnd[order], inv[order]

        bounds = np.flatnonzero(np.diff(inv_s)) + 1
        starts = np.concatenate([[0], bounds])
        ends = np.concatenate([bounds, [n]])
        battles: list[Battle] = []
        for s, e in zip(starts, ends):
            if max_battles is not None and len(battles) >= max_battles:
                break
            sl = slice(s, e)
            battles.append(
                Battle(
                    battle_id=str(uniq[inv_s[s]]),
                    type=feats["type"][sl],
                    present=feats["present"][sl],
                    candidate=feats["candidate"][sl],
                    cat=feats["cat"][sl],
                    f=feats["f"][sl],
                    legal=legal_s[sl],
                    request_kind=rk_s[sl],
                    event=ev_s[sl],
                    teacher_probs=tp_s[sl],
                    teacher_value=tv_s[sl],
                    action=act_s[sl],
                    won=won_s[sl],
                    round=rnd_s[sl],
                )
            )
        return battles

    def __len__(self) -> int:
        return len(self.battles)

    def __getitem__(self, i: int) -> Battle:
        return self.battles[i]

    def __iter__(self) -> Iterator[Battle]:
        return iter(self.battles)

    @property
    def num_decisions(self) -> int:
        return sum(len(b) for b in self.battles)

    def split(self, holdout_frac: float, seed: int = 0) -> tuple["FeaturesDataset", "FeaturesDataset"]:
        """Deterministic battle-level train/holdout split."""
        rng = np.random.default_rng(seed)
        perm = rng.permutation(len(self.battles))
        k = int(round(len(perm) * holdout_frac))
        if len(perm) > 1:
            k = min(max(k, 1 if holdout_frac > 0 else 0), len(perm) - 1)
        hold = FeaturesDataset.__new__(FeaturesDataset)
        hold.battles = [self.battles[i] for i in perm[:k]]
        train = FeaturesDataset.__new__(FeaturesDataset)
        train.battles = [self.battles[i] for i in perm[k:]]
        return train, hold


def collate_battles(battles: Sequence[Battle]) -> dict[str, np.ndarray]:
    """Pad a list of battles to (B, T, ...) numpy arrays plus a `mask` (B,T) bool."""
    B = len(battles)
    T = max(len(b) for b in battles)
    out = {
        "type": np.tile(TOKEN_TYPES, (B, T, 1)),
        "present": np.zeros((B, T, MAX_TOKENS), np.uint8),
        "candidate": np.zeros((B, T, MAX_TOKENS), np.uint8),
        "cat": np.zeros((B, T, MAX_TOKENS, 4), np.uint16),
        "f": np.zeros((B, T, MAX_TOKENS, FEAT), np.int8),
        "legal": np.zeros((B, T), np.uint16),
        "request_kind": np.zeros((B, T), np.uint8),
        "event": np.zeros((B, T, EV_DIM), np.float32),
        "teacher_probs": np.full((B, T, N_ACTIONS), np.nan, np.float32),
        "teacher_value": np.full((B, T), -1.0, np.float32),
        "action": np.full((B, T), -1, np.int8),
        "won": np.zeros((B, T), np.bool_),
        "mask": np.zeros((B, T), np.bool_),
    }
    for i, b in enumerate(battles):
        t = len(b)
        out["type"][i, :t] = b.type
        out["present"][i, :t] = b.present
        out["candidate"][i, :t] = b.candidate
        out["cat"][i, :t] = b.cat
        out["f"][i, :t] = b.f
        out["legal"][i, :t] = b.legal
        out["request_kind"][i, :t] = b.request_kind
        out["event"][i, :t] = b.event
        out["teacher_probs"][i, :t] = b.teacher_probs
        out["teacher_value"][i, :t] = b.teacher_value
        out["action"][i, :t] = b.action
        out["won"][i, :t] = b.won
        out["mask"][i, :t] = True
    return out
