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

def test_2xx_without_submission_id_marks_failed(tmp_path, monkeypatch, mocker):
    # A 2xx whose body lacks postSubmissionId is untrackable -> failed, not submitted.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pn", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(200, {"noid": True}))
    led = BlotatoRestPoster(cfg).publish(led, "pn")
    assert led.posts["pn"].state is PostState.failed
    assert led.posts["pn"].submission_id is None
    assert "no postSubmissionId" in (led.posts["pn"].error_reason or "")

def test_5xx_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    seq = [_R(503, {"e": "down"}), _R(200, {"postSubmissionId": "s5"})]
    mocker.patch("fanops.post.blotato_rest.requests.post", side_effect=seq)
    mocker.patch("fanops.post.blotato_rest.time.sleep")
    led = BlotatoRestPoster(cfg).publish(led, "p5")
    assert led.posts["p5"].state is PostState.submitted and led.posts["p5"].submission_id == "s5"

def test_retry_exhaustion_marks_failed(tmp_path, monkeypatch, mocker):
    # All attempts 429 -> failed (not raise, not hang). Proves _MAX_RETRIES bounds the loop.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="px", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(429, {"e": "rate"}))
    mocker.patch("fanops.post.blotato_rest.time.sleep")
    led = BlotatoRestPoster(cfg).publish(led, "px")
    assert led.posts["px"].state is PostState.failed
    assert "429" in (led.posts["px"].error_reason or "")
    assert pm.call_count == 4                              # _MAX_RETRIES attempts, bounded
