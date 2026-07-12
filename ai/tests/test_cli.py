import json
import subprocess
import sys


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "sim", *args], capture_output=True, text=True, timeout=300
    )


def test_random_vs_random_smoke():
    p = run("battle", "--p1", "random", "--p2", "random", "--seed", "42", "--battles", "1000")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.splitlines()[-1])
    assert out["battles"] == 1000
    assert out["p1_wins"] + out["p2_wins"] + out["ties"] == 1000


def test_smoke_is_deterministic():
    a = run("battle", "--p1", "random", "--p2", "random", "--seed", "42", "--battles", "100")
    b = run("battle", "--p1", "random", "--p2", "random", "--seed", "42", "--battles", "100")
    assert a.stdout.splitlines()[-1] == b.stdout.splitlines()[-1]


def test_maxdamage_vs_random():
    p = run("battle", "--p1", "maxdamage", "--p2", "random", "--seed", "7", "--battles", "50")
    out = json.loads(p.stdout.splitlines()[-1])
    assert out["p1_wins"] > out["p2_wins"]
