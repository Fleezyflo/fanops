import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.blotato_rest import BlotatoRestPoster

class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def test_success_sets_submission_id(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "secret123")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98432",
                      platform=Platform.twitter, caption="hi",
                      scheduled_time="2026-06-01T18:00:00Z", state=PostState.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      return_value=_R(200, {"postSubmissionId": "s_1"}))
    led = BlotatoRestPoster(cfg).publish(led, "p1")
    assert pm.call_args.args[0] == "https://backend.blotato.com/v2/posts"
    assert pm.call_args.kwargs["headers"]["blotato-api-key"] == "secret123"
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "s_1"

def test_4xx_marks_failed_not_analyzed(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="c", account="@a", account_id="1", platform=Platform.tiktok,
                      caption="x", media_urls=["https://h/v.mp4"], state=PostState.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(422, {"e": "bad"}))
    led = BlotatoRestPoster(cfg).publish(led, "p2")
    assert led.posts["p2"].state is PostState.failed       # FIX F22: failed, not analyzed
    assert "422" in (led.posts["p2"].error_reason or "")

def test_401_raises_loudly(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "badkey")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p3", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(401, {"e": "unauthorized"}))
    with pytest.raises(RuntimeError):
        BlotatoRestPoster(cfg).publish(led, "p3")          # bad key must halt, not silently fail

def test_429_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p4", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    seq = [_R(429, {"e": "rate"}), _R(200, {"postSubmissionId": "s9"})]
    mocker.patch("fanops.post.blotato_rest.requests.post", side_effect=seq)
    mocker.patch("fanops.post.blotato_rest.time.sleep")    # no real backoff in tests
    led = BlotatoRestPoster(cfg).publish(led, "p4")
    assert led.posts["p4"].submission_id == "s9" and led.posts["p4"].state is PostState.submitted
