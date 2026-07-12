import json
import subprocess
import sys


def test_throughput_gate():
    p = subprocess.run(
        [sys.executable, "-m", "sim", "bench", "--seconds", "5"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p.returncode == 0, p.stderr
    dps = json.loads(p.stdout.splitlines()[-1])["decisions_per_s"]
    assert dps >= 20_000, f"only {dps:.0f} decisions/s"
