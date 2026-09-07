import json
from unittest.mock import MagicMock, patch

from data.sources.showdown_replays import scrape_format


def _resp(js):
    m = MagicMock()
    m.json.return_value = js
    m.status_code = 200
    m.raise_for_status = lambda: None
    return m


PAGE = [{"id": "gen1ou-1", "uploadtime": 1}]
REPLAY = {"id": "gen1ou-1", "log": "|start", "players": ["a", "b"]}


def test_scrape_pages_until_empty(tmp_path):
    with patch("requests.Session.get", side_effect=[_resp(PAGE), _resp(REPLAY)]):
        n = scrape_format("gen1ou", root=tmp_path, min_interval_s=0)
    assert n == 1
    saved = tmp_path / "raw" / "showdown" / "gen1ou" / "gen1ou-1.json"
    assert json.loads(saved.read_text())["log"] == "|start"


def test_scrape_resumes(tmp_path):
    with patch("requests.Session.get", side_effect=[_resp(PAGE), _resp(REPLAY)]):
        assert scrape_format("gen1ou", root=tmp_path, min_interval_s=0) == 1
    with patch("requests.Session.get", side_effect=[_resp(PAGE)]) as get:
        assert scrape_format("gen1ou", root=tmp_path, min_interval_s=0) == 0
        assert get.call_count == 1  # only the search page, no replay re-fetch
