"""`pkai.weights` container (§9.4): little-endian header + table of contents + raw tensors.

Layout (all little-endian, offsets in bytes):

    header (128 B, struct HEADER_FMT):
        magic "PKAI" | version u32 = 1 | model char[16] (NUL padded, "pep")
        d, layers, heads, ffn, gru u32          (model config)
        feature_schema char[32] (NUL padded) | feature_schema_crc32 u32
        rom_crc32 u32 (placeholder 0)           | n_tensors u32
        toc_offset u32 | data_offset u32 | file_size u32
        emb_species, emb_move, emb_matchup, emb_small u32
        reserved to 128 B
    toc (n_tensors x 80 B, struct TOC_FMT):
        name char[48] (NUL padded) | dtype u8 | ndim u8 | pad u16 | shape u32[4] | offset u32 | nbytes u32 | reserved u32
    data: every tensor's raw bytes (C order), each 64-byte aligned, in TOC order.

Float scales live in two ordinary tensors (`scales.names` uint8 NUL-joined, `scales.values`
float64) so `load_weights` rebuilds the full QuantParams; the integer kernels never read them.
"""
from __future__ import annotations

import os
import struct
import zlib
from collections import OrderedDict

import numpy as np

from models.pep_int import QuantParams

# Fixed model identifier written into the 16-byte header name field. Readers accept any
# value there (files written before this field was fixed carry a run-configuration name).
MODEL_ID = "pep"

MAGIC = b"PKAI"
VERSION = 1
HEADER_SIZE = 128
HEADER_FMT = "<4sI16s5I32sIIIIII4I"
TOC_FMT = "<48sBBH4IIII"
TOC_SIZE = struct.calcsize(TOC_FMT)  # 80
ALIGN = 64
NAME_MAX = 47

DTYPES = [np.int8, np.uint8, np.int16, np.uint16, np.int32, np.uint32, np.float32, np.float64]
DTYPE_CODE = {np.dtype(d): i for i, d in enumerate(DTYPES)}
assert struct.calcsize(HEADER_FMT) <= HEADER_SIZE and TOC_SIZE == 80

CONFIG_KEYS = ("d", "layers", "heads", "ffn", "gru")
EMB_KEYS = ("emb_species", "emb_move", "emb_matchup", "emb_small")


def _align(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def _pack_scales(scales: "OrderedDict[str, float]") -> tuple[np.ndarray, np.ndarray]:
    names = "\0".join(scales.keys()).encode("utf-8")
    return np.frombuffer(names, dtype=np.uint8).copy(), np.array(list(scales.values()), np.float64)


def _unpack_scales(names: np.ndarray, values: np.ndarray) -> "OrderedDict[str, float]":
    keys = names.tobytes().decode("utf-8").split("\0") if names.size else []
    return OrderedDict(zip(keys, [float(v) for v in values]))


def write_weights(qp: QuantParams, path: str | os.PathLike) -> list[dict]:
    """Serialise QuantParams; returns the TOC as a list of dicts (name, dtype, shape, offset, nbytes)."""
    tensors: "OrderedDict[str, np.ndarray]" = OrderedDict(qp.tensors)
    names_arr, values_arr = _pack_scales(qp.scales)
    tensors["scales.names"] = names_arr
    tensors["scales.values"] = values_arr
    for name, arr in tensors.items():
        if len(name.encode()) > NAME_MAX:
            raise ValueError(f"tensor name too long: {name}")
        if arr.ndim > 4:
            raise ValueError(f"{name}: ndim {arr.ndim} > 4")
        if arr.dtype not in DTYPE_CODE:
            raise ValueError(f"{name}: unsupported dtype {arr.dtype}")

    n = len(tensors)
    toc_offset = HEADER_SIZE
    data_offset = _align(toc_offset + n * TOC_SIZE)
    entries = []
    off = data_offset
    for name, arr in tensors.items():
        nbytes = int(arr.nbytes)
        entries.append({"name": name, "dtype": str(arr.dtype), "shape": tuple(arr.shape), "offset": off, "nbytes": nbytes})
        off = _align(off + nbytes)
    file_size = off

    cfg = qp.config
    schema = qp.feature_schema.encode("utf-8")
    header = struct.pack(
        HEADER_FMT,
        MAGIC,
        VERSION,
        MODEL_ID.encode("utf-8")[:15],
        *[int(cfg[k]) for k in CONFIG_KEYS],
        schema[:31],
        zlib.crc32(schema) & 0xFFFFFFFF,
        int(qp.rom_crc32) & 0xFFFFFFFF,
        n,
        toc_offset,
        data_offset,
        file_size,
        *[int(cfg.get(k, 0)) for k in EMB_KEYS],
    )
    header = header.ljust(HEADER_SIZE, b"\0")

    buf = bytearray(file_size)
    buf[:HEADER_SIZE] = header
    for i, (e, (name, arr)) in enumerate(zip(entries, tensors.items())):
        shape = list(arr.shape) + [0] * (4 - arr.ndim)
        rec = struct.pack(
            TOC_FMT, name.encode("utf-8"), DTYPE_CODE[arr.dtype], arr.ndim, 0, *shape, e["offset"], e["nbytes"], 0
        )
        s = toc_offset + i * TOC_SIZE
        buf[s : s + TOC_SIZE] = rec
        raw = np.ascontiguousarray(arr).astype(arr.dtype.newbyteorder("<") if arr.dtype.byteorder == ">" else arr.dtype).tobytes()
        buf[e["offset"] : e["offset"] + e["nbytes"]] = raw
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(buf)
    return entries


def read_header(data: bytes | memoryview) -> dict:
    if bytes(data[:4]) != MAGIC:
        raise ValueError("not a pkai.weights file (bad magic)")
    fields = struct.unpack_from(HEADER_FMT, data, 0)
    (magic, version, model_id, d, layers, heads, ffn, gru, schema, schema_crc, rom_crc, n, toc_off, data_off, size, es, em, emu, esm) = fields
    if version != VERSION:
        raise ValueError(f"unsupported pkai.weights version {version}")
    schema_s = schema.split(b"\0", 1)[0].decode("utf-8")
    if zlib.crc32(schema_s.encode()) & 0xFFFFFFFF != schema_crc:
        raise ValueError("feature_schema crc mismatch")
    return {
        "magic": magic.decode(),
        "version": version,
        "model": model_id.split(b"\0", 1)[0].decode("utf-8", "replace"),
        "config": {"d": d, "layers": layers, "heads": heads, "ffn": ffn, "gru": gru,
                   "emb_species": es, "emb_move": em, "emb_matchup": emu, "emb_small": esm},
        "feature_schema": schema_s,
        "feature_schema_crc32": schema_crc,
        "rom_crc32": rom_crc,
        "n_tensors": n,
        "toc_offset": toc_off,
        "data_offset": data_off,
        "file_size": size,
    }


def read_toc(data: bytes | memoryview, header: dict | None = None) -> list[dict]:
    header = header or read_header(data)
    entries = []
    for i in range(header["n_tensors"]):
        name, code, ndim, _pad, s0, s1, s2, s3, off, nbytes, _r = struct.unpack_from(TOC_FMT, data, header["toc_offset"] + i * TOC_SIZE)
        entries.append(
            {
                "name": name.split(b"\0", 1)[0].decode("utf-8"),
                "dtype": str(np.dtype(DTYPES[code])),
                "shape": tuple([s0, s1, s2, s3][:ndim]),
                "offset": off,
                "nbytes": nbytes,
            }
        )
    return entries


def load_weights(path: str | os.PathLike) -> QuantParams:
    """Read pkai.weights back into a QuantParams (numpy only)."""
    with open(path, "rb") as fh:
        data = fh.read()
    header = read_header(data)
    if header["file_size"] != len(data):
        raise ValueError(f"file size {len(data)} != header {header['file_size']}")
    tensors: "OrderedDict[str, np.ndarray]" = OrderedDict()
    for e in read_toc(data, header):
        arr = np.frombuffer(data, dtype=np.dtype(e["dtype"]).newbyteorder("<"), count=int(np.prod(e["shape"], dtype=np.int64)), offset=e["offset"])
        tensors[e["name"]] = arr.reshape(e["shape"]).astype(np.dtype(e["dtype"]), copy=True)
    scales = _unpack_scales(tensors.pop("scales.names"), tensors.pop("scales.values"))
    return QuantParams(
        config=dict(header["config"]),
        feature_schema=header["feature_schema"],
        tensors=tensors,
        scales=scales,
        rom_crc32=header["rom_crc32"],
    )


def describe(path: str | os.PathLike) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    h = read_header(data)
    lines = [f"{h['magic']} v{h['version']} model={h['model']} cfg={h['config']} schema={h['feature_schema']} "
             f"rom_crc32={h['rom_crc32']:#010x} tensors={h['n_tensors']} size={h['file_size']}"]
    for e in read_toc(data, h):
        lines.append(f"{e['name']:40s} {e['dtype']:8s} {str(e['shape']):22s} @{e['offset']:<9d} {e['nbytes']} B")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(describe(sys.argv[1]))
