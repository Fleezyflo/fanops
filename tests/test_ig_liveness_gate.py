# tests/test_ig_liveness_gate.py
# MOL-117 — the CONDITIONAL, platform-confirmed IG liveness rest-gate in reconcile.
#
# Before MOL-117 an IG post rested `published` on POSTIZ SELF-REPORT alone (status==published + a
# releaseURL Postiz itself populated). MOL-117 replaces that IG branch with a conditional gate that
# consumes MOL-113's confirm_post_live seam:
#   1. Account WITH its own ig_user_id (credentialed) -> FAIL-CLOSED platform identity gate: rests
#      published ONLY when the Graph resolves the captured media id AND its owner username == the post's
#      intended account handle. A definitive mismatch/absence -> parked needs_reconcile (NOT rested on
#      Postiz's word).
#   2. Account WITHOUT its own ig_user_id (uncredentialed) -> UNCHANGED Postiz-rest path (the #317
#      borrowed-credential regression guard: 6 posts were stranded when #317 strict-gated a borrowed id).
#   3. Transport failure during confirm (raising/timeout getter) -> FAIL-OPEN: NOT parked, retries next
#      tick. Only a DEFINITIVE identity verdict parks.
#
# Every test MOCKS the confirmation seam (inject confirm= / graph_get=) — no live Graph, no live verbs.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _ig_post(led, pid="p1", account="@markmakmouly"):
    # a parked IG post whose Postiz poll will report published + a releaseURL + a releaseId (the IG object id)
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1",
                      platform=Platform.instagram, caption="x",
                      state=PostState.needs_reconcile, submission_id="postiz_1"))


_URL = "https://www.instagram.com/reel/DaY8y2DCiuf/"
_RID = "17841456789012345"


def _poll_published(sid):
    return {"postSubmissionId": sid, "status": "published", "publicUrl": _URL, "releaseId": _RID}


# ── (a) credentialed + media resolves owner==account -> rests published ─────────────────────────────────

def test_credentialed_owner_matches_rests_published(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    # the mocked seam confirms the object AND reports the owner == the post's account handle
    def confirm(cfg_, post, *, get=None):
        assert post.media_id == _RID                       # the captured releaseId is the resolve INPUT
        return {"confirmed": True, "owner": "markmakmouly"}
    led = reconcile_posts(led, cfg, get_status=_poll_published, confirm=confirm)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].public_url == _URL
    assert led.posts["p1"].media_id == _RID


# ── (b) credentialed + does NOT resolve OR owner!=account -> parks needs_reconcile (fail-closed) ─────────

def test_credentialed_object_gone_parks_needs_reconcile(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    # DEFINITIVE absence: the Graph resolved nothing (confirmed False), no transport error
    def confirm(cfg_, post, *, get=None):
        return {"confirmed": False, "owner": None}
    led = reconcile_posts(led, cfg, get_status=_poll_published, confirm=confirm)
    assert led.posts["p1"].state is PostState.needs_reconcile      # NOT rested on Postiz's word
    assert "unverified" in (led.posts["p1"].error_reason or "").lower()


def test_credentialed_owner_mismatch_parks_needs_reconcile(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    # DEFINITIVE mismatch: object resolves but the owner is a DIFFERENT username (wrong account/hijack)
    def confirm(cfg_, post, *, get=None):
        return {"confirmed": True, "owner": "someone_else"}
    led = reconcile_posts(led, cfg, get_status=_poll_published, confirm=confirm)
    assert led.posts["p1"].state is PostState.needs_reconcile      # owner!=account -> fail-closed park


# ── (c) UNCREDENTIALED account -> Postiz-rest path UNCHANGED, NOT strict-gated (#317 regression guard) ───

def test_uncredentialed_account_rests_on_postiz_unchanged(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # active IG account with NO ig_user_id (borrowed/global credential) — the #317 blind spot
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active"}])
    _ig_post(led)
    called = {"n": 0}
    def confirm(cfg_, post, *, get=None):
        called["n"] += 1                                    # MUST NOT be called for an uncredentialed account
        return {"confirmed": False, "owner": None}
    led = reconcile_posts(led, cfg, get_status=_poll_published, confirm=confirm)
    assert led.posts["p1"].state is PostState.published      # rests on the Postiz-confirmed releaseURL
    assert called["n"] == 0                                  # NOT strict-gated (no borrowed-id enumeration)
    assert led.posts["p1"].media_id == _RID                  # still stamps the releaseId (MOL-112)


def test_no_accounts_file_rests_on_postiz_unchanged(tmp_path, monkeypatch):
    # A torn/absent accounts.json degrades credentialed_ig_handles -> [] -> the post is treated as
    # uncredentialed and rests on Postiz (NEVER stranded on a config read failure).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _ig_post(led)
    led = reconcile_posts(led, cfg, get_status=_poll_published,
                          confirm=lambda *a, **k: {"confirmed": False, "owner": None})
    assert led.posts["p1"].state is PostState.published


# ── (d) transport error during confirm -> NOT parked; retries next tick (fail-open) ─────────────────────

def test_credentialed_transport_error_is_fail_open_not_parked(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # creds present (token + ig_user_id) so the real confirm_post_live actually reaches the Graph getter —
    # the raising getter is what exercises the transport-vs-definitive distinction.
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    # a transport failure DURING confirm: the injected graph_get raises. The gate must NOT read the
    # resulting confirmed=False as a definitive absence — it fails OPEN and leaves the post re-resolvable.
    import requests
    def graph_get(url, params=None, timeout=None):
        raise requests.exceptions.RequestException("boom")
    led = reconcile_posts(led, cfg, get_status=_poll_published, graph_get=graph_get)
    # fail-open: the post is NOT parked as an identity failure on a network hiccup. It is left untouched
    # (still needs_reconcile, still re-pollable) so the next tick re-confirms — never stranded/failed.
    assert led.posts["p1"].state is PostState.needs_reconcile   # untouched, re-pollable next pass
    assert led.posts["p1"].state is not PostState.failed
    er = (led.posts["p1"].error_reason or "").lower()
    assert "unverified" not in er                               # NOT the definitive-identity-failure park


# ── (e) no IG post rests published on Postiz self-report alone for a CREDENTIALED account ────────────────

def test_credentialed_never_rests_on_postiz_self_report_alone(tmp_path, monkeypatch):
    # The grep-style invariant: for a CREDENTIALED account, a Postiz status==published + releaseURL is
    # NOT sufficient to rest published. Without a platform confirmation the post must park.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    # Postiz says published with a real releaseURL, but the platform confirms NOTHING (definitive absence).
    led = reconcile_posts(led, cfg, get_status=_poll_published,
                          confirm=lambda *a, **k: {"confirmed": False, "owner": None})
    assert led.posts["p1"].state is not PostState.published   # Postiz self-report alone did NOT rest it
    assert led.posts["p1"].state is PostState.needs_reconcile


# ── (f) missing releaseId on credentialed account -> fail-open (no probe id -> retry next tick) ─────────

def test_credentialed_missing_release_id_fail_open(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    called = {"n": 0}
    def confirm(cfg_, post, *, get=None):
        called["n"] += 1
        return {"confirmed": False, "owner": None}
    def _poll_no_rid(sid):
        return {"postSubmissionId": sid, "status": "published", "publicUrl": _URL}   # no releaseId
    led = reconcile_posts(led, cfg, get_status=_poll_no_rid, confirm=confirm)
    assert led.posts["p1"].state is PostState.needs_reconcile   # fail-open: left re-pollable
    assert called["n"] == 0                                       # confirm never called without a probe id
    assert "unverified" not in (led.posts["p1"].error_reason or "").lower()


def test_credentialed_missing_release_id_then_resolves_two_pass(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-mark-99"}])
    _ig_post(led)
    pass_n = {"n": 0}
    def poll(sid):
        pass_n["n"] += 1
        if pass_n["n"] == 1:
            return {"postSubmissionId": sid, "status": "published", "publicUrl": _URL}
        return {"postSubmissionId": sid, "status": "published", "publicUrl": _URL, "releaseId": _RID}
    def confirm(cfg_, post, *, get=None):
        assert post.media_id == _RID
        return {"confirmed": True, "owner": "markmakmouly"}
    led = reconcile_posts(led, cfg, get_status=poll, confirm=confirm)
    assert led.posts["p1"].state is PostState.needs_reconcile   # first pass: no releaseId -> fail-open
    led = reconcile_posts(led, cfg, get_status=poll, confirm=confirm)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].media_id == _RID
