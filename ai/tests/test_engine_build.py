import ctypes
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "vendor" / "lib" / "libpkmn-showdown.so"
FLAGS = ROOT / "sim" / "mechanics_flags.json"


def test_shared_lib_exists_and_loads():
    assert LIB.exists(), "run ai/sim/build_engine.sh first"
    lib = ctypes.CDLL(str(LIB))
    assert hasattr(lib, "pkmn_gen1_battle_update")


def test_mechanics_flags():
    flags = json.loads(FLAGS.read_text())
    assert flags["mode"] == "showdown"
    assert len(flags["engine_commit"]) == 40
    assert flags["zig_version"]
