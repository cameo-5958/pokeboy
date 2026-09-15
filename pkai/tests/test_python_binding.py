"""Python <-> C++ parity for observations and features.

Usage: python3 test_python_binding.py <build_dir> <rom_path>
The C++ real-ROM test dumps the fixture observation and its feature hash
into <build_dir>; this test rebuilds the features through the C ABI from the
Python mirror and requires the identical hash, then exercises a Python-built
observation end to end.
"""
import ctypes as C
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import pkai  # noqa: E402


def main(build_dir: str, rom_path: str) -> int:
    build = pathlib.Path(build_dir)
    lib = pkai.load(next(build.rglob("libpkai_c.*")))
    pkai.check_layout(lib)
    rom = pathlib.Path(rom_path).read_bytes()

    # 1. Round trip of the fixture dumped by pkai_rom_battle_tests.
    dump = build / "pkai_fixture_observation.bin"
    expected = int((build / "pkai_fixture_hash.txt").read_text().split()[0])
    raw = dump.read_bytes()
    assert len(raw) == C.sizeof(pkai.Observation), (len(raw), C.sizeof(pkai.Observation))
    obs = pkai.Observation.from_buffer_copy(raw)
    feats, mask = pkai.build_features(lib, rom, obs, 0)
    got = pkai.features_hash(lib, feats)
    assert got == expected, f"feature hash {got} != C++ {expected}"
    assert feats.count == 27 and mask == feats.legal and mask & 0xF

    # 2. A Python-authored observation: Charmander (internal $B0) L25 with Ember vs Squirtle ($B1) L25.
    o = pkai.Observation()
    o.own_count = 1; o.player_count = 1; o.trainer_class = 1; o.count = 3
    for s in (o.stages, o.player_stages):
        for i in range(6):
            s[i] = 7
    mon = o.own[0]
    mon.species = 0xB0; mon.level = 25; mon.hp = 60; mon.max_hp = 60
    mon.types[0] = 0x14; mon.types[1] = 0x14; mon.moves[0] = 52; mon.moves[1] = 10
    mon.stats[0] = 40; mon.stats[1] = 35; mon.stats[2] = 45; mon.stats[3] = 40; mon.dvs[0] = 0x98; mon.dvs[1] = 0x88
    o.active = mon
    p = o.player[0]
    p.known = True; p.species = 0xB1; p.level = 25; p.hp = 65; p.max_hp = 65
    p.types[0] = 0x15; p.types[1] = 0x15; p.moves[0] = 55
    feats2, mask2 = pkai.build_features(lib, rom, o, 0)
    rows = pkai.token_matrix(feats2)
    ember, scratch, empty = rows[13], rows[14], rows[15]
    assert mask2 & 0b11 == 0b11 and not mask2 & 0b1100, bin(mask2)   # two moves legal, no empty slots
    assert ember[0] == 1 and empty[0] == 0
    assert ember[8 + 3] < scratch[8 + 3], "Ember (Fire) must be less effective than Scratch on Squirtle"
    assert rows[17][0] == 1 and rows[17][4] == 55, "revealed Water Gun token present"
    assert rows[17][8 + 3] > rows[13][8 + 3], "Water Gun on Charmander is super effective"
    print("python binding parity OK; fixture hash", got, "mask", bin(mask2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
