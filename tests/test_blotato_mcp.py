import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.errors import BlotatoAuthError
from fanops.post.blotato_mcp import BlotatoMcpPoster

def test_flat_args(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="@a", account_id="98432",
                      platform=Platform.instagram, caption="the one", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=PostState.queued))
    calls = []
    poster = BlotatoMcpPoster(cfg, tool_caller=lambda n, a: calls.append((n, a)) or {"postSubmissionId": "s9"})
    led = poster.publish(led, "p1")
    n, a = calls[0]
    assert n == "blotato_create_post" and a["accountId"] == "98432"
    assert a["mediaUrls"] == ["https://h/v.mp4"] and "post" not in a
    assert led.posts["p1"].submission_id == "s9"

def test_raises_without_caller(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p2", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    with pytest.raises(RuntimeError):
        BlotatoMcpPoster(cfg, tool_caller=None).publish(led, "p2")

def test_mcp_auth_failure_raises_blotato_auth_error(tmp_path):
    # AUDIT B3: an auth failure from the MCP tool (401/403/unauthorized/forbidden/invalid token/
    # api key in the message) maps to the TYPED BlotatoAuthError so run.py can halt by type — a bad
    # key must halt loudly, never silently burn posts.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p4", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    def caller(n, a):
        raise RuntimeError("401 Unauthorized: invalid token")
    with pytest.raises(BlotatoAuthError):
        BlotatoMcpPoster(cfg, tool_caller=caller).publish(led, "p4")

def test_mcp_typed_auth_error_propagates_even_with_nonmatching_message(tmp_path):
    # ADVERSARIAL (D3 re-confirm): the substring set (401/403/unauthorized/forbidden/invalid token/
    # api key) cannot anticipate every auth phrasing. A caller that already raises the TYPED
    # BlotatoAuthError is the authoritative signal — it MUST propagate so run.py halts by type
    # (F52/H8), even when its MESSAGE matches no substring. Before the fix, the broad `except
    # Exception` swallowed it and re-parked as needs_reconcile (burn-the-queue regression). The
    # production MCP wiring is documented to raise BlotatoAuthError on auth failures, so this is the
    # realistic path, not a corner case. Note "credentials rejected" contains NONE of the substrings.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p7", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    def caller(n, a):
        raise BlotatoAuthError("credentials rejected")   # typed, but message matches no substring
    with pytest.raises(BlotatoAuthError):
        BlotatoMcpPoster(cfg, tool_caller=caller).publish(led, "p7")

def test_mcp_non_auth_failure_marks_post_needs_reconcile_not_raise(tmp_path):
    # PRIME DIRECTIVE: a NON-auth MCP error is AMBIGUOUS (the tool MAY have posted, like a REST 5xx)
    # -> PARK the post as needs_reconcile and RETURN (do NOT raise, do NOT mark failed). Raising
    # would abort the run; failed would make it re-queueable -> double-post to a real fan account.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p5", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    def caller(n, a):
        raise RuntimeError("500 internal error")
    led = BlotatoMcpPoster(cfg, tool_caller=caller).publish(led, "p5")
    assert led.posts["p5"].state is PostState.needs_reconcile   # parked, NOT raised, NOT failed
    assert "may be live" in (led.posts["p5"].error_reason or "")

def test_mcp_accepts_alias_submission_id(tmp_path):
    # AUDIT B3: the MCP poster reuses D2's _extract_submission_id, so an alias (submissionId / nested
    # data.id) from the tool result is recognized -> submitted.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p6", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    poster = BlotatoMcpPoster(cfg, tool_caller=lambda n, a: {"data": {"submissionId": "mcp_alias"}})
    led = poster.publish(led, "p6")
    assert led.posts["p6"].state is PostState.submitted
    assert led.posts["p6"].submission_id == "mcp_alias"

def test_mcp_no_submission_id_marks_needs_reconcile(tmp_path):
    # AUDIT B3 (rewrite of the old test_mcp_no_submission_id_marks_failed): an MCP 2xx with no
    # recognizable submission id is MAY-BE-LIVE (the tool returned) — PARK as needs_reconcile,
    # NEVER failed (failed => re-queueable => double-post to a real fan account).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p3", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.queued))
    poster = BlotatoMcpPoster(cfg, tool_caller=lambda n, a: {"unexpected": "no id"})
    led = poster.publish(led, "p3")
    assert led.posts["p3"].state is PostState.needs_reconcile
    assert "no recognizable submission id" in (led.posts["p3"].error_reason or "")
