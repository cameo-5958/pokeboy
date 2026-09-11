"""Flatten the PEP reference vectors into a raw binary bundle for the C++ model test.

    uv run python -m tools.dump_vectors <vectors_dir> <out_dir>

Writes `<out_dir>/vectors.bin` (every array's raw little-endian bytes, C order, each
64-byte aligned) and `<out_dir>/vectors.idx` (one line per array:
`name dtype ndim d0 d1 d2 d3 offset nbytes`).  Arrays are prefixed by their source
file (`inputs.`, `intermediates.`, `outputs.`, `sequence.`).  The float event vectors are
additionally quantised to int8 exactly as `IntPEP.quantize_event` does so the C++ side
never touches floating point.
"""
from __future__ import annotations

import os
import sys

import numpy as np

from models.pep_int import IntPEP

ALIGN = 64


def main(vectors_dir: str, out_dir: str) -> None:
    arrays: list[tuple[str, np.ndarray]] = []
    for group in ("inputs", "intermediates", "outputs", "sequence"):
        z = np.load(os.path.join(vectors_dir, f"{group}.npz"))
        for k in z.files:
            arrays.append((f"{group}.{k}", np.ascontiguousarray(z[k])))
        if group == "sequence":
            ev = z["event"]  # (B, T, 64) float32
            ev8 = np.stack([IntPEP.quantize_event(ev[b]) for b in range(ev.shape[0])])
            arrays.append(("sequence.ev8", np.ascontiguousarray(ev8)))
    os.makedirs(out_dir, exist_ok=True)
    off = 0
    lines = []
    with open(os.path.join(out_dir, "vectors.bin"), "wb") as fh:
        for name, arr in arrays:
            if arr.dtype.byteorder == ">":
                arr = arr.astype(arr.dtype.newbyteorder("<"))
            raw = arr.tobytes()
            pad = (-off) % ALIGN
            fh.write(b"\0" * pad)
            off += pad
            shape = list(arr.shape) + [0] * (4 - arr.ndim)
            lines.append(f"{name} {arr.dtype.name} {arr.ndim} {' '.join(str(s) for s in shape)} {off} {len(raw)}")
            fh.write(raw)
            off += len(raw)
    with open(os.path.join(out_dir, "vectors.idx"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{len(arrays)} arrays, {off} bytes -> {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
