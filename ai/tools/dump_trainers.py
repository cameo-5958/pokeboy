"""Build and run the C++ trainer dumper to (re)generate datasets/trainers.json.

Make-style: the dump is skipped when the output already records the current
ROM's CRC and is newer than the dumper binary, unless --force is given (the
ROM's mtime is useless: build_ai.sh re-copies it on every cmake build).

    uv run python tools/dump_trainers.py [--force] [--out PATH]

Configures <repo>/build-ai from <repo>/gameboy if it is not configured yet,
builds the pkai_dump_trainers target, and runs it. Override the build
directory with POKEBOY_BUILD_DIR.
"""

from __future__ import annotations

import argparse
import json
import os
import zlib
import subprocess
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parents[1]
REPO = AI_ROOT.parent
GAMEBOY = REPO / "gameboy"
ROM = REPO / "pred-patch" / "pokered-ai.gbc"
DEFAULT_OUT = AI_ROOT / "datasets" / "trainers.json"


def build_dir() -> Path:
    return Path(os.environ.get("POKEBOY_BUILD_DIR", REPO / "build-ai"))


def build_tool(build: Path) -> Path:
    if not (build / "CMakeCache.txt").exists():
        subprocess.run(["cmake", "-S", str(GAMEBOY), "-B", str(build)], check=True)
    subprocess.run(
        ["cmake", "--build", str(build), "--target", "pkai_dump_trainers", "-j8"],
        check=True,
    )
    exe = build / "pkai_dump_trainers"
    if not exe.exists():
        raise FileNotFoundError(exe)
    return exe


def up_to_date(out: Path, exe: Path | None) -> bool:
    if not out.exists():
        return False
    if not ROM.exists():
        return False
    try:
        recorded = int(json.loads(out.read_text())["rom_crc32"])
    except (ValueError, KeyError, OSError):
        return False
    if recorded != zlib.crc32(ROM.read_bytes()):
        return False
    if exe is not None and exe.exists() and exe.stat().st_mtime > out.stat().st_mtime:
        return False
    return True


def dump(out: Path = DEFAULT_OUT, force: bool = False) -> Path:
    if not force and up_to_date(out, build_dir() / "pkai_dump_trainers"):
        return out
    exe = build_tool(build_dir())
    if not force and up_to_date(out, exe):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(exe), str(out)], check=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="regenerate even if up to date")
    args = ap.parse_args(argv)
    path = dump(args.out, args.force)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
