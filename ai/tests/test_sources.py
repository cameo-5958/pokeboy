from unittest.mock import patch

from data.sources.hf_corpora import SOURCES, pull_hf
from data.sources.smogon_stats import month_files


def test_sources_cover_required_corpora():
    assert set(SOURCES) >= {"metamon", "pokechamp", "pokeagent"}
    ids = [s.repo_id for group in SOURCES.values() for s in group]
    assert "jakegrigsby/metamon-parsed-replays" in ids
    assert "milkkarten/pokechamp" in ids


def test_pull_skips_manifested(tmp_path):
    with patch("data.sources.hf_corpora.snapshot_download") as sd:
        sd.return_value = str(tmp_path / "x")
        pull_hf("pokechamp", root=tmp_path)
        pull_hf("pokechamp", root=tmp_path)  # second call: manifest hit
        assert sd.call_count == 1


def test_smogon_month_files_shape():
    files = month_files("2026-06")
    assert "2026-06/gen1ou-1500.txt" in files
    assert "2026-06/moveset/gen1ou-0.txt" in files
    assert "2026-06/chaos/gen1uu-1500.json" in files
