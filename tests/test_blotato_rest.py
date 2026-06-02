import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.blotato_rest import BlotatoRestPoster, _extract_submission_id

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
    # AUDIT H8: a 401 raises the TYPED BlotatoAuthError (so run.py can halt by type, not by a
    # fragile "401" substring match). Still loud — a bad key must halt, not silently fail.
    from fanops.errors import BlotatoAuthError
    monkeypatch.setenv("BLOTATO_API_KEY", "badkey")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p3", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(401, {"e": "unauthorized"}))
    with pytest.raises(BlotatoAuthError):
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

def test_extract_submission_id_accepts_known_aliases():
    # AUDIT B2: Blotato's 2xx body shape varies (postSubmissionId | submissionId | id, sometimes
    # nested under "data"). _extract_submission_id accepts the known aliases and recurses into a
    # nested dict, ignoring non-str/empty values and non-dict bodies (-> None).
    assert _extract_submission_id({"postSubmissionId": "a1"}) == "a1"
    assert _extract_submission_id({"submissionId": "b2"}) == "b2"
    assert _extract_submission_id({"id": "c3"}) == "c3"
    assert _extract_submission_id({"data": {"submissionId": "d4"}}) == "d4"   # nested recursion
    assert _extract_submission_id({"data": {"id": "e5"}}) == "e5"
    # precedence: postSubmissionId wins over a bare id at the same level
    assert _extract_submission_id({"postSubmissionId": "win", "id": "lose"}) == "win"
    # rejects non-str / empty / non-dict -> None (never a truthy non-id)
    assert _extract_submission_id({"id": 123}) is None
    assert _extract_submission_id({"id": ""}) is None
    assert _extract_submission_id({"noid": True}) is None
    assert _extract_submission_id({}) is None
    assert _extract_submission_id(None) is None
    assert _extract_submission_id("not a dict") is None

def test_2xx_without_recognizable_id_is_needs_reconcile_not_failed(tmp_path, monkeypatch, mocker):
    # AUDIT B2 (rewrite of the old test_2xx_without_submission_id_marks_failed): a 2xx with no
    # recognizable submission id is MAY-BE-LIVE (the platform returned success) — it must be PARKED
    # as needs_reconcile, NEVER failed (failed => re-queueable => double-post to a real fan account).
    # The client token stamped at birth (D1) is PRESERVED so reconcile can still poll the post.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pn", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued, submission_id="fanops_tok"))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(200, {}))
    led = BlotatoRestPoster(cfg).publish(led, "pn")
    assert led.posts["pn"].state is PostState.needs_reconcile   # parked, NEVER failed
    assert led.posts["pn"].submission_id == "fanops_tok"        # client token preserved -> pollable
    assert "no recognizable submission id" in (led.posts["pn"].error_reason or "")

def test_2xx_with_alias_id_marks_submitted(tmp_path, monkeypatch, mocker):
    # AUDIT B2: the 2xx success path uses _extract_submission_id, so an alias (submissionId / nested
    # data.id) is recognized as a real id -> submitted, overwriting the client token.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pa", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued, submission_id="fanops_tok"))
    mocker.patch("fanops.post.blotato_rest.requests.post",
                 return_value=_R(201, {"data": {"submissionId": "real_alias"}}))
    led = BlotatoRestPoster(cfg).publish(led, "pa")
    assert led.posts["pa"].state is PostState.submitted
    assert led.posts["pa"].submission_id == "real_alias"   # real id beats the client token

def test_5xx_is_ambiguous_marks_needs_reconcile_no_repost(tmp_path, monkeypatch, mocker):
    # AUDIT C1: a 5xx arrives AFTER the request body was transmitted, so Blotato may have
    # already created the post (Blotato's own docs: a publish timeout causes a duplicate post).
    # Blotato has NO idempotency key, so a blind re-POST risks a SECOND live post on the artist's
    # real account. The safe move is to STOP and mark the post needs_reconcile (a human/poll step
    # checks GET /v2/posts/:id before any resubmit) — NOT retry. Exactly one POST must be sent.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(503, {"e": "down"}))
    slept = mocker.patch("fanops.post.blotato_rest.time.sleep")
    led = BlotatoRestPoster(cfg).publish(led, "p5")
    assert led.posts["p5"].state is PostState.needs_reconcile
    assert led.posts["p5"].submission_id is None
    assert "503" in (led.posts["p5"].error_reason or "")
    assert pm.call_count == 1, "must NOT re-POST an ambiguous 5xx (double-publish risk)"
    assert slept.call_count == 0, "no backoff/retry on an ambiguous failure"

def test_network_timeout_marks_needs_reconcile_no_repost(tmp_path, monkeypatch, mocker):
    # A connection error / read timeout mid-POST is also ambiguous — the request may have landed.
    # Previously this escaped publish() entirely (uncaught), aborting the run; now it's caught and
    # the post is parked for reconcile rather than blindly resubmitted.
    import requests
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pt", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      side_effect=requests.exceptions.ConnectionError("conn reset"))
    led = BlotatoRestPoster(cfg).publish(led, "pt")
    assert led.posts["pt"].state is PostState.needs_reconcile
    assert pm.call_count == 1, "a single ambiguous network failure must not fan into retries"
    assert "conn reset" in (led.posts["pt"].error_reason or "")


def test_5xx_with_submission_id_in_body_captures_it_for_reconcile(tmp_path, monkeypatch, mocker):
    # AUDIT H4: if an ambiguous 5xx body still carries a postSubmissionId, CAPTURE it on the post
    # so the reconcile step (GET /v2/posts/:id) can later resolve this post automatically. Without
    # the id, reconcile can't poll it and a human must — so grabbing it when present is what makes
    # auto-reconcile possible for this post. Still parks as needs_reconcile (no re-POST).
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5b", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      return_value=_R(503, {"postSubmissionId": "sub_amb", "error": "upstream"}))
    led = BlotatoRestPoster(cfg).publish(led, "p5b")
    assert led.posts["p5b"].state is PostState.needs_reconcile
    assert led.posts["p5b"].submission_id == "sub_amb"     # captured -> reconcile can poll it
    assert pm.call_count == 1

def test_5xx_body_id_captured_via_alias_or_nested(tmp_path, monkeypatch, mocker):
    # CODE-REVIEW (Minor #1): the 5xx-body id capture must use the SAME alias-aware extraction as
    # the 2xx path (_extract_submission_id), not just the literal "postSubmissionId". If Blotato's
    # ERROR body carries the real id under submissionId / id / nested data.*, capturing it makes the
    # post auto-reconcilable; missing it leaves the post on the un-pollable fanops_ client token
    # (human-only) until a later pass. Still prime-directive-safe either way (needs_reconcile, never
    # failed) — this just removes the last divergence between the two id-capture sites.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5d", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued, submission_id="fanops_tok"))
    mocker.patch("fanops.post.blotato_rest.requests.post",
                 return_value=_R(503, {"data": {"submissionId": "sub_nested"}, "error": "upstream"}))
    led = BlotatoRestPoster(cfg).publish(led, "p5d")
    assert led.posts["p5d"].state is PostState.needs_reconcile
    assert led.posts["p5d"].submission_id == "sub_nested"  # alias+nested id captured, overwrites token

def test_5xx_body_id_overwrites_preexisting_client_token(tmp_path, monkeypatch, mocker):
    # AUDIT H4 + H1: posts now carry a CLIENT token (fanops_...) at birth (D1). When an ambiguous
    # 5xx body still carries a REAL Blotato postSubmissionId, it MUST overwrite the client token —
    # the real id is the authoritative key for GET /v2/posts/:id. The old guard (`not
    # post.submission_id`) blocked this capture once a token preexisted; D1 drops that clause.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5c", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued, submission_id="fanops_clienttoken"))
    pm = mocker.patch("fanops.post.blotato_rest.requests.post",
                      return_value=_R(503, {"postSubmissionId": "sub_real", "error": "upstream"}))
    led = BlotatoRestPoster(cfg).publish(led, "p5c")
    assert led.posts["p5c"].state is PostState.needs_reconcile
    assert led.posts["p5c"].submission_id == "sub_real"    # real id BEATS the client token
    assert pm.call_count == 1

def test_429_backoff_is_jittered(tmp_path, monkeypatch, mocker):
    # Jitter the 429 backoff so many surfaces rate-limited at once don't retry in lockstep
    # (thundering herd). Each sleep is delay + random.uniform(0, delay), so with uniform pinned to
    # 0.3 the first sleep is 1.0 + 0.3 = 1.3 (NOT the bare 1.0), and every sleep stays > 0.
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pj", parent_id="c", account="@a", account_id="1", platform=Platform.twitter,
                      caption="x", state=PostState.queued))
    mocker.patch("fanops.post.blotato_rest.requests.post", return_value=_R(429, {"e": "rate"}))
    sleeps = []
    mocker.patch("fanops.post.blotato_rest.time.sleep", side_effect=lambda s: sleeps.append(s))
    mocker.patch("fanops.post.blotato_rest.random.uniform", return_value=0.3)
    led = BlotatoRestPoster(cfg).publish(led, "pj")
    assert led.posts["pj"].state is PostState.failed       # exhausted 429s -> failed (re-queueable)
    assert sleeps, "expected at least one backoff sleep"
    assert all(s > 0 for s in sleeps)                      # never a zero/negative wait
    assert sleeps[0] != 1.0                                # jittered off the bare base (1.0 -> 1.3)
    assert sleeps[0] == 1.3                                # delay(1.0) + uniform(0,delay)=0.3

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
