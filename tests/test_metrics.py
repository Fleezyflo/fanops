from fanops.config import Config
from fanops.post.metrics import BlotatoMetricsClient

class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def test_list_posts_returns_rows(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"items": [{"postSubmissionId": "s1", "metrics": {"saves": 5}}]}))
    rows = BlotatoMetricsClient(cfg).list_posts("30d")
    assert rows[0]["postSubmissionId"] == "s1" and rows[0]["metrics"]["saves"] == 5

import pytest

def test_list_posts_bare_list_response(tmp_path, monkeypatch, mocker):
    # A top-level array response must be returned as-is, not crash on .get
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, [{"postSubmissionId": "s1"}, {"postSubmissionId": "s2"}]))
    rows = BlotatoMetricsClient(cfg).list_posts()
    assert [r["postSubmissionId"] for r in rows] == ["s1", "s2"]

def test_list_posts_non_2xx_raises(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(500, {"e": "down"}))
    with pytest.raises(RuntimeError, match="500"):
        BlotatoMetricsClient(cfg).list_posts()
