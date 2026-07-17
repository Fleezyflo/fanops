# tests/test_zernio_idempotency.py — report 11: x-request-id + existingPost + the 24h 409, landed together
# because they are inseparable (the header alone misparses a replay as "no id" and files a SUCCESS as
# ambiguous). All offline: every test stubs requests.post, time.sleep and time.monotonic. No live call.
#
# The three root causes this pins (report 11 §1):
#   R-1  two re-POST branches (ConnectTimeout, 429) sent a byte-identical body with NO idempotency key
#   R-2  _extract_zernio_id had no `existingPost` branch -> a replay parsed as "no id" -> needs_reconcile
#   R-3  a 409 fell through to `failed` — and `failed` is RE-QUEUEABLE, i.e. a double-post
# and the two operator-caught design defects: D6 (post.id alone is NOT one create operation — crosspost
# pops a failed record and remints it under the same id) and D7 (the shared Poster protocol must not change).
import inspect
import json
import uuid
from pathlib import Path
import pytest
import requests
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, is_real_submission_id
from fanops.post import Poster
from fanops.post import run as run_mod
from fanops.post import zernio
from fanops.post.zernio import (ZernioPoster, _request_id, _require_request_identity, _parse_create_body,
                                _extract_409_candidate, _retry_after_s, _ZERNIO_REQ_NS, _REQ_NAME_V,
                                _RETRY_DEADLINE_S, _IDEMPOTENCY_WINDOW_S, _MAX_RETRIES)
from fanops.post.zernio_outcome import Created, IdempotentReplay, ReconciliationRequired, TerminalFailure
from fanops.studio.views_common import is_transient_failure_reason

_BIRTH = "2026-07-16T13:31:00Z"          # incarnation 1 — the burned-record population
_REBIRTH = "2026-07-17T11:20:00Z"        # incarnation 2 — same post.id, popped + reminted by crosspost


class _R:
    """Stub response. body=<Exception> makes .json() raise (the non-JSON / unreadable-2xx case)."""
    def __init__(s, code, body=None, text="", headers=None):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text; s.headers = headers or {}
    def json(s):
        if isinstance(s._b, Exception): raise s._b
        return s._b


class _Rec:
    """Records every POST /posts. len(rec.calls) IS the send count — the only honest way to assert
    'zero network calls'. Responses are consumed in order; the last one repeats. An Exception is raised."""
    def __init__(s, *responses):
        s.responses = list(responses) or [_R(201, {"_id": "z1"})]; s.calls = []
    def __call__(s, url, headers=None, json=None, timeout=None):
        s.calls.append({"url": url, "headers": dict(headers or {}), "payload": json})
        r = s.responses[min(len(s.calls) - 1, len(s.responses) - 1)]
        if isinstance(r, Exception): raise r
        return r
    @property
    def rids(s): return [c["headers"].get("x-request-id") for c in s.calls]


class _Clock:
    """Deterministic monotonic clock. sleep() advances it, so the deadline is exercised without wall-clock."""
    def __init__(s, start=1000.0): s.t = start
    def __call__(s): return s.t
    def advance(s, d): s.t += d


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # No real sleeping, no jitter: a deadline test must be deterministic, and the suite must not burn seconds.
    monkeypatch.setattr(zernio.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(zernio.random, "uniform", lambda *_a, **_k: 0.0)


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    return Config(root=tmp_path)


def _post(pid="post_x", acct_id="acc_abc", platform=Platform.tiktok, created_at=_BIRTH):
    return Post(id=pid, parent_id="c1", account="tk", account_id=acct_id, platform=platform,
                caption="fire", state=PostState.submitting, created_at=created_at,
                media_urls=["https://media.zernio.com/x.mp4"], scheduled_time="2099-01-01T00:00:00Z",
                public_url="dryrun://c1")


def _led(cfg, post):
    led = Ledger.load(cfg); led.add_post(post); return led


def _publish(cfg, post, rec, monkeypatch):
    monkeypatch.setattr(zernio.requests, "post", rec)
    led = _led(cfg, post)
    return ZernioPoster(cfg).publish(led, post.id).posts[post.id]


# ============================ REQUEST IDENTITY (§8) — tests 1-16 ============================

def test_01_request_id_header_is_present_on_every_send(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); rec = _Rec(_R(201, {"_id": "z1"}))
    _publish(cfg, _post(), rec, monkeypatch)
    assert rec.calls, "no request was issued"
    assert "x-request-id" in rec.calls[0]["headers"]

def test_02_request_id_is_a_valid_uuid(tmp_path, monkeypatch):
    # The spec types the header {type: string, format: uuid} — a non-UUID may be rejected or ignored,
    # and an IGNORED idempotency key fails OPEN into the double-post it exists to prevent.
    cfg = _cfg(tmp_path, monkeypatch); rec = _Rec(_R(201, {"_id": "z1"}))
    _publish(cfg, _post(), rec, monkeypatch)
    rid = rec.rids[0]
    assert str(uuid.UUID(rid)) == rid

def test_03_request_id_identical_across_the_connecttimeout_retry(tmp_path, monkeypatch):
    # R-1 NEGATIVE CONTROL: this branch used to re-POST a byte-identical body with no key.
    cfg = _cfg(tmp_path, monkeypatch)
    rec = _Rec(requests.exceptions.ConnectTimeout("blip"), _R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert len(rec.calls) == 2
    assert rec.rids[0] == rec.rids[1], "a retry with a DIFFERENT key is a second create"
    assert p.state is PostState.submitted

def test_04_request_id_identical_across_the_429_retry(tmp_path, monkeypatch):
    # R-1 NEGATIVE CONTROL: a 429 fires AFTER the request reached Zernio — the create may already be live.
    cfg = _cfg(tmp_path, monkeypatch)
    rec = _Rec(_R(429, headers={"Retry-After": "1"}), _R(201, {"_id": "z1"}))
    _publish(cfg, _post(), rec, monkeypatch)
    assert len(rec.calls) == 2
    assert rec.rids[0] == rec.rids[1]

def test_05_request_id_is_recomputed_identically_after_a_process_restart(tmp_path, monkeypatch):
    # The id is DERIVED, never stored: a crash mid-network must recompute the same value next pass, or the
    # retry would create a second post. Nothing but the record's own fields may feed it.
    cfg = _cfg(tmp_path, monkeypatch); rec1, rec2 = _Rec(_R(201, {"_id": "z1"})), _Rec(_R(201, {"_id": "z1"}))
    _publish(cfg, _post(), rec1, monkeypatch)
    _publish(cfg, _post(), rec2, monkeypatch)       # a fresh Post object == a fresh process reload
    assert rec1.rids[0] == rec2.rids[0]

def test_06_same_incarnation_every_retry_same_uuid():
    # Operator requirement 1.
    p = _post()
    assert _request_id(p) == _request_id(p) == _request_id(_post())

def test_07_remint_under_the_same_post_id_yields_a_different_uuid():
    # Operator requirement 2 — the D6 REGRESSION. crosspost.py pops a failed/rejected record
    # (`led.posts.pop(pid)`) and re-adds `Post(id=pid, ..., created_at=<fresh wall-clock>)`: the SAME post.id
    # now denotes a DIFFERENT create operation. uuid5(ns, post.id) alone would hand the NEW incarnation the
    # OLD one's identity, and Zernio would replay the dead post instead of creating the new one. The four
    # burned records are `failed` — exactly this population.
    old, new = _post(created_at=_BIRTH), _post(created_at=_REBIRTH)
    assert old.id == new.id
    assert _request_id(old) != _request_id(new)

def test_08_two_resolved_account_ids_yield_different_uuids():
    # Operator requirement 3 — run.py refreshes post.account_id at publish (a Go-Live integration remap),
    # and account_id is NOT hashed into post.id (the handle is; the Zernio integration id is not).
    assert _request_id(_post(acct_id="acc_A")) != _request_id(_post(acct_id="acc_B"))

def test_09_two_platforms_yield_different_uuids():
    # Operator requirement 4.
    assert _request_id(_post(platform=Platform.tiktok)) != _request_id(_post(platform=Platform.instagram))

def test_10_daemon_transient_requeue_keeps_the_same_uuid(tmp_path, monkeypatch):
    # A requeue is a retry of the SAME incarnation, so it MUST reuse the id (else the retry double-posts).
    # _requeue_transient_failed_for_daemon must therefore never touch created_at.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); before = _request_id(p)
    p.state, p.error_reason = PostState.failed, "publish transient error (retries exhausted): read timed out"
    led = _led(cfg, p); led.save()
    run_mod._requeue_transient_failed_for_daemon(cfg)
    after = Ledger.load(cfg).posts[p.id]
    assert after.created_at == _BIRTH
    assert _request_id(after) == before

def test_11_request_id_unaffected_by_submission_id(tmp_path, monkeypatch):
    # The id must depend on the record's IDENTITY only. If a network-written field fed it, the retry after a
    # partial write would derive a different key.
    a = _post(); b = _post(); b.submission_id = "fanops_deadbeef"
    assert _request_id(a) == _request_id(b)

def test_11b_canonical_name_is_json_not_a_delimiter_join():
    # The merged formula used "|".join(...) — WRONG, and corrected before any deployment or live create.
    # A raw delimiter join is not injective: the delimiter can occur INSIDE a component.
    p = _post()
    name = zernio._request_name(p)
    assert name == json.dumps(["1", p.id, p.created_at, p.platform.value, p.account_id],
                              ensure_ascii=False, separators=(",", ":"))
    assert name.startswith('["1"') and '","' in name        # JSON array, fixed separators, no whitespace
    assert json.loads(name) == ["1", p.id, p.created_at, p.platform.value, p.account_id]   # round-trips

def test_11c_delimiter_bearing_values_cannot_alias():
    # The defect the merged formula carried. A pipe join is injective only while NO component contains the
    # delimiter; when the pipe SHIFTS across a boundary between two free fields, two DIFFERENT identities
    # flatten to ONE string — one x-request-id for two posts, so Zernio replays the WRONG one.
    #
    # Honest severity: with TODAY's values the merged join happened to be safe — ver is fixed, post.id is
    # `post_<hex>`, created_at is ISO-8601 and platform is an enum, so the only unconstrained component
    # (account_id, operator-supplied) is LAST and cannot shift anything. It was injective BY ACCIDENT, not by
    # construction: any change to the field order, to child_id's format, or to what created_at holds would
    # have re-opened it silently. This pair demonstrates the property on the real encoder.
    a, b = _post(pid="a", created_at="b|2026"), _post(pid="a|b", created_at="2026")
    assert "|".join(("1", a.id, a.created_at, a.platform.value, a.account_id)) == \
           "|".join(("1", b.id, b.created_at, b.platform.value, b.account_id)), "this pair must alias under a pipe join"
    assert _request_id(a) != _request_id(b), "canonical encoding must be injective BY CONSTRUCTION"

def test_11d_json_significant_characters_cannot_alias():
    # A quote/backslash/bracket inside a value must be ESCAPED, not read as structure.
    pairs = [
        (_post(acct_id='a","b'), _post(acct_id='a', pid='post_x","b')),
        (_post(acct_id='a\\"b'), _post(acct_id='a\\', pid='post_x"b')),
        (_post(acct_id='["x"]'), _post(acct_id='[\\"x\\"]')),
        (_post(acct_id='a,b'), _post(acct_id='a', pid='post_x,b')),
    ]
    for x, y in pairs:
        assert _request_id(x) != _request_id(y), f"aliased: {x.account_id!r} vs {y.account_id!r}"
        assert json.loads(zernio._request_name(x))[4] == x.account_id      # value survives verbatim

def test_11e_unicode_values_are_stable_and_distinct():
    a, b = _post(acct_id="acct_café"), _post(acct_id="acct_cafe")
    assert _request_id(a) != _request_id(b)
    assert _request_id(a) == _request_id(_post(acct_id="acct_café"))        # deterministic across calls
    assert str(uuid.UUID(_request_id(a))) == _request_id(a)
    # ensure_ascii=False keeps it literal; uuid5 encodes UTF-8 — pin the bytes so the encoding cannot drift.
    assert "café" in zernio._request_name(a)
    assert zernio._request_name(a).encode("utf-8") == zernio._request_name(_post(acct_id="acct_café")).encode("utf-8")

def test_11f_canonical_name_is_byte_identical_on_repeat():
    p = _post()
    names = {zernio._request_name(p).encode("utf-8") for _ in range(5)}
    assert len(names) == 1, "canonical encoding must be byte-stable"

def test_12_namespace_and_formula_version_are_pinned():
    # DELIBERATE change-detector (report 11 §13.3): these are PERMANENT. Changing either makes a retry derive
    # a different key than the send it is retrying — silently re-opening R-1 for every in-flight post.
    assert _ZERNIO_REQ_NS == uuid.UUID("09105245-a8e0-4d28-ba02-c85ebab84cb3")
    assert _REQ_NAME_V == "1"

def test_13_created_at_is_not_mutated_by_a_publish_and_never_travels_at_finalize(tmp_path, monkeypatch):
    # created_at is the per-incarnation discriminator: if publish or finalize could rewrite it, one
    # incarnation's retries would derive different ids.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(201, {"_id": "z1"})), monkeypatch)
    assert p.created_at == _BIRTH
    assert "created_at" not in run_mod._NET_POST_FIELDS

def test_14_missing_created_at_refuses_before_any_network(tmp_path, monkeypatch):
    # Operator requirement 5. Post.created_at is Optional[str]: every row carries one in practice, but the
    # TYPE permits None and this design does not rest on an unenforced observation. ZERO sends (I-10).
    cfg = _cfg(tmp_path, monkeypatch); rec = _Rec(_R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(created_at=None), rec, monkeypatch)
    assert rec.calls == [], "a post with no derivable request identity reached the network"
    assert p.state is PostState.failed
    assert "missing_request_identity" in p.error_reason and "created_at" in p.error_reason
    assert p.submission_id is None

def test_15_missing_account_id_refuses_before_any_network(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); rec = _Rec(_R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(acct_id=""), rec, monkeypatch)
    assert rec.calls == []
    assert p.state is PostState.failed and "account_id" in p.error_reason

def test_15b_missing_request_identity_is_not_classified_transient():
    # The reason string feeds is_transient_failure_reason, which SUBSTRING-scans English ("timeout",
    # "network error", "(5xx)"). If this reason matched, the daemon would re-queue a row that cannot succeed
    # until its data is repaired — an endless loop. Pin the classification, not the wording's good intentions.
    r = _require_request_identity(_post(created_at=None))
    reason = f"zernio {r.reason}: {r.evidence}"
    assert is_transient_failure_reason(reason) is False

def test_16_request_id_and_payload_account_id_always_agree(tmp_path, monkeypatch):
    # The id names the account it was derived for; the payload names the account Zernio will post to. If they
    # could diverge, the key would be idempotent for the wrong channel.
    cfg = _cfg(tmp_path, monkeypatch); rec = _Rec(_R(201, {"_id": "z1"}))
    p = _post(acct_id="acc_live")
    _publish(cfg, p, rec, monkeypatch)
    assert rec.calls[0]["payload"]["platforms"][0]["accountId"] == "acc_live"
    assert rec.rids[0] == _request_id(p)


# ============================ PARSING — tests 17-25 ============================

def test_17_201_with_id_is_created():
    assert _parse_create_body({"_id": "z1"}) == Created("z1")

def test_18_200_with_existing_post_is_an_idempotent_replay():
    # R-2 NEGATIVE CONTROL: before this, `existingPost` had no branch and a SUCCESSFUL publish parsed as
    # "no id" -> needs_reconcile. That is the bug the header alone would have made worse.
    assert _parse_create_body({"existingPost": {"_id": "z9"}}) == IdempotentReplay("z9")

def test_19_existing_post_id_aliases_are_accepted():
    assert _parse_create_body({"existingPost": {"id": "z9"}}) == IdempotentReplay("z9")
    assert _parse_create_body({"existingPost": {"postId": "z9"}}) == IdempotentReplay("z9")

def test_20_existing_post_without_an_id_is_ambiguous_not_terminal():
    r = _parse_create_body({"existingPost": {}})
    assert isinstance(r, ReconciliationRequired) and r.reason == "replay_no_id"
    assert r.candidate_post_id is None

def test_21_existing_post_that_is_not_a_dict_is_tolerated():
    # `existingPost` is prose-only in the spec (never schematised). An unreadable shape must never raise, and
    # must NOT be downgraded to Created off a sibling id: the key IS Zernio saying "this is a replay".
    assert isinstance(_parse_create_body({"existingPost": 42}), ReconciliationRequired)
    assert isinstance(_parse_create_body({"_id": "z1", "existingPost": 42}), ReconciliationRequired)
    assert _parse_create_body({"existingPost": "z9"}) == IdempotentReplay("z9")

def test_22_200_with_a_bare_id_and_no_existing_post_is_created():
    # 200 is not even in the spec's responses map for POST /posts. A bare id with no replay marker is a create.
    assert _parse_create_body({"_id": "z1"}) == Created("z1")

def test_23_empty_body_is_success_no_id():
    r = _parse_create_body({})
    assert isinstance(r, ReconciliationRequired) and r.reason == "success_no_id"

def test_24_unreadable_2xx_body_never_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(200, ValueError("not json"))), monkeypatch)
    assert p.state is PostState.needs_reconcile          # never `failed`: Zernio accepted SOMETHING
    assert "success_unreadable_body" in p.error_reason

def test_25_conflicting_ids_park_with_no_candidate():
    # Two different ids means the response contract is not what we modelled. Adopt neither, and carry no
    # candidate — we could not even say which of the two it would be.
    r = _parse_create_body({"_id": "zA", "existingPost": {"_id": "zB"}})
    assert isinstance(r, ReconciliationRequired) and r.reason == "conflicting_ids"
    assert r.candidate_post_id is None

def test_25b_matching_direct_and_existing_ids_are_one_replay():
    assert _parse_create_body({"_id": "zA", "existingPost": {"_id": "zA"}}) == IdempotentReplay("zA")


# ============================ THE 409 — tests 26-32 ============================

def test_26_409_carries_the_candidate_and_never_a_submission_id(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, {"details": {"existingPostId": "z_other"}})), monkeypatch)
    assert p.state is PostState.needs_reconcile
    assert p.reconcile_candidate_id == "z_other"
    assert p.submission_id is None                       # §5: the candidate is NEVER an identity
    assert not is_real_submission_id(p.submission_id)    # nothing downstream can mistake it for a poll key

def test_27_409_with_empty_details_has_no_candidate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, {"details": {}})), monkeypatch)
    assert p.state is PostState.needs_reconcile and p.reconcile_candidate_id is None

def test_28_409_without_details_does_not_crash(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for body in ({}, {"details": "nope"}, ValueError("not json")):
        p = _publish(cfg, _post(), _Rec(_R(409, body)), monkeypatch)
        assert p.state is PostState.needs_reconcile and p.reconcile_candidate_id is None

def test_28b_409_with_an_unreadable_body_says_so_rather_than_swallowing_it(tmp_path, monkeypatch):
    # "Zernio named no post" and "Zernio may have named one we could not read" are DIFFERENT facts — only the
    # second means the operator is missing a pointer that exists. Routed through errors.fail_open, with the
    # breadcrumb landing on the HOUSE run.log channel (where the operator looks), not only stderr.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, ValueError("not json"))), monkeypatch)
    assert p.state is PostState.needs_reconcile and p.reconcile_candidate_id is None
    assert "unreadable" in p.error_reason and "a candidate may exist" in p.error_reason
    body = cfg.log_path.read_text()
    assert "zernio_409_body_unparsed" in body and "zernio.409.parse" in body
    # and the new wording must not read as transient ("unreachable" IS in the classifier's substring list)
    assert is_transient_failure_reason(p.error_reason) is False

def test_28c_a_readable_409_with_no_candidate_is_distinct_from_an_unreadable_one(tmp_path, monkeypatch):
    # The distinction the sentinel preserves: both yield candidate=None, but only ONE means "a pointer may
    # exist that we failed to read". fail_open swallows, so cand=None alone cannot tell them apart.
    cfg = _cfg(tmp_path, monkeypatch)
    readable = _publish(cfg, _post(pid="p_r"), _Rec(_R(409, {"details": {}})), monkeypatch)
    unread = _publish(cfg, _post(pid="p_u"), _Rec(_R(409, ValueError("not json"))), monkeypatch)
    assert readable.reconcile_candidate_id is unread.reconcile_candidate_id is None
    assert "unreadable" not in readable.error_reason
    assert "unreadable" in unread.error_reason

def test_28d_unreadable_2xx_breadcrumbs_through_fail_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(200, ValueError("not json"))), monkeypatch)
    assert p.state is PostState.needs_reconcile          # never `failed`: Zernio accepted SOMETHING
    body = cfg.log_path.read_text()
    assert "zernio_2xx_body_unparsed" in body and "zernio.create.parse" in body
    assert "sk_test" not in body and "sk_test" not in (p.error_reason or "")

def test_29_409_never_yields_failed(tmp_path, monkeypatch):
    # R-3 NEGATIVE CONTROL — the live defect. `failed` is RE-QUEUEABLE, so filing a duplicate-content 409 as
    # failed is a licence to post it again. A 409 proves only that Zernio (a SCHEDULER) holds a matching
    # record: not platform publication, not ownership by this post, not completion.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, {"details": {"existingPostId": "z_other"}})), monkeypatch)
    assert p.state is not PostState.failed
    assert p.state is PostState.needs_reconcile

def test_30_409_never_raises(tmp_path, monkeypatch):
    # It must never reach _is_transient_publish_error, which classifies a RuntimeError by MESSAGE SUBSTRING:
    # a RuntimeError("...(409)...") parses 400<=409<500 -> not transient -> `failed` -> re-queueable.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, {"details": {"existingPostId": "z_other"}})), monkeypatch)
    assert p.state is PostState.needs_reconcile          # returned a value, did not raise

def test_31_409_never_writes_submission_id_even_when_one_preexists(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.submission_id = "fanops_token"
    out = _publish(cfg, p, _Rec(_R(409, {"details": {"existingPostId": "z_other"}})), monkeypatch)
    assert out.submission_id == "fanops_token"           # untouched
    assert out.reconcile_candidate_id == "z_other"

def test_32_409_survives_publish_one_end_to_end(tmp_path, monkeypatch):
    # The candidate is written on the THROWAWAY network ledger — without _NET_POST_FIELDS it is silently
    # discarded at finalize and the operator loses the only pointer the 409 handed back.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.state = PostState.queued
    led = _led(cfg, p); led.save()
    monkeypatch.setattr(zernio.requests, "post", _Rec(_R(409, {"details": {"existingPostId": "z_other"}})))
    monkeypatch.setattr(run_mod, "_ensure_media", lambda *a, **k: None)
    state = run_mod._publish_one(cfg, p.id, "zernio", account_id="acc_abc")
    assert state == PostState.needs_reconcile.value
    after = Ledger.load(cfg).posts[p.id]
    assert after.state is PostState.needs_reconcile
    assert after.reconcile_candidate_id == "z_other"
    assert "candidate=z_other" in after.error_reason      # the downgrade mirror


# ============================ DEADLINE (§7) — tests 33-38 ============================

def _clocked(monkeypatch, start=1000.0):
    clock = _Clock(start)
    monkeypatch.setattr(zernio.time, "monotonic", clock)
    monkeypatch.setattr(zernio.time, "sleep", lambda d: clock.advance(d))
    return clock

def test_33_429_retry_after_inside_the_deadline_is_retried_with_the_same_id(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); _clocked(monkeypatch)
    rec = _Rec(_R(429, headers={"Retry-After": "5"}), _R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert len(rec.calls) == 2 and rec.rids[0] == rec.rids[1]
    assert p.state is PostState.submitted

def test_34_429_retry_after_beyond_the_deadline_never_sends_again(tmp_path, monkeypatch):
    # Past the ~5-min window the header is no longer honoured, so a "retry" IS a fresh create. Refuse to
    # send, and park — the first request reached Zernio, so the create may already be live.
    cfg = _cfg(tmp_path, monkeypatch); _clocked(monkeypatch)
    rec = _Rec(_R(429, headers={"Retry-After": "600"}), _R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert len(rec.calls) == 1, "sent again outside the idempotency window"
    assert p.state is PostState.needs_reconcile
    assert "rate_limited_may_be_live" in p.error_reason

def test_35_connecttimeout_past_the_deadline_with_nothing_sent_is_terminal(tmp_path, monkeypatch):
    # A connection never established sent nothing, so `failed` (re-queueable) is CORRECT and safe here — the
    # one boundary where terminal is provable.
    cfg = _cfg(tmp_path, monkeypatch); clock = _clocked(monkeypatch)
    class _SlowTimeout(_Rec):
        def __call__(s, *a, **k):
            clock.advance(120.0)                          # each attempt burns wall-clock before failing
            return super().__call__(*a, **k)
    rec = _SlowTimeout(requests.exceptions.ConnectTimeout("blip"))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert p.state is PostState.failed
    assert "connect_timeout" in p.error_reason and "nothing was sent" in p.error_reason

def test_36_the_budget_is_driven_by_monotonic_not_the_wall_clock(monkeypatch):
    # An NTP correction / DST step must neither extend nor collapse the budget. Pin the formula exactly to
    # time.monotonic(): every boundary below is predicted by monotonic ALONE, so no wall-clock reading can
    # be participating. (Patching time.time globally to prove the negative would also patch it for pytest's
    # own internals — a spurious-failure trap, and the negative is already implied by these exact boundaries.)
    clock = _Clock(0.0)
    monkeypatch.setattr(zernio.time, "monotonic", clock)
    assert zernio._fits_deadline(0.0, _RETRY_DEADLINE_S - 0.1) is True
    assert zernio._fits_deadline(0.0, _RETRY_DEADLINE_S) is False       # boundary is exclusive
    clock.advance(100.0)
    assert zernio._fits_deadline(0.0, _RETRY_DEADLINE_S - 100.1) is True
    assert zernio._fits_deadline(0.0, _RETRY_DEADLINE_S - 100.0) is False
    clock.advance(_RETRY_DEADLINE_S)
    assert zernio._fits_deadline(0.0, 0.0) is False                     # spent: no wait is small enough

def test_37_total_elapsed_never_exceeds_the_retry_deadline(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); clock = _clocked(monkeypatch, start=0.0)
    rec = _Rec(_R(429, headers={"Retry-After": "100"}))    # every attempt 429s
    _publish(cfg, _post(), rec, monkeypatch)
    assert clock.t < _RETRY_DEADLINE_S

def test_38_the_deadline_is_strictly_inside_the_idempotency_window():
    # If the budget reached the window, the last retry would land exactly where the key stops being honoured.
    assert _RETRY_DEADLINE_S < _IDEMPOTENCY_WINDOW_S

def test_38b_retry_after_header_parsing(tmp_path, monkeypatch):
    assert _retry_after_s(_R(429, headers={"Retry-After": "12"})) == 12.0
    assert _retry_after_s(_R(429, headers={"Retry-After": "-5"})) == 0.0
    assert _retry_after_s(_R(429)) is None
    assert _retry_after_s(_R(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None  # HTTP-date -> backoff


# ============================ CANDIDATE FIELD (§5) — tests 39-48 ============================

def test_39_candidate_round_trips_through_the_ledger(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.reconcile_candidate_id = "z_other"
    led = _led(cfg, p); led.save()
    assert Ledger.load(cfg).posts[p.id].reconcile_candidate_id == "z_other"

def test_40_candidate_is_in_the_network_finalize_field_set():
    # Without this the poster's write on the throwaway network ledger is DISCARDED at finalize.
    assert "reconcile_candidate_id" in run_mod._NET_POST_FIELDS

def test_41_an_old_row_without_the_key_loads_as_none(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    p = Post(id="p_old", parent_id="c1", account="tk", account_id="a1", platform=Platform.tiktok,
             caption="x", state=PostState.needs_reconcile)
    assert p.reconcile_candidate_id is None

def test_42_schema_version_is_unchanged():
    # Additive Optional[str]=None: no migration, no bump (precedent: media_id / product_type).
    from fanops.ledger import SCHEMA_VERSION
    assert SCHEMA_VERSION == 11

def test_43_extra_ignore_forward_compat_is_unbroken():
    # extra="ignore" is deliberate, pinned forward-compat: an OLDER binary drops this new key rather than
    # crashing. That is the documented downgrade cost — and why the candidate is mirrored into error_reason.
    p = Post(id="p1", parent_id="c1", account="tk", account_id="a1", platform=Platform.tiktok,
             caption="x", state=PostState.needs_reconcile, some_future_key="v")
    assert not hasattr(p, "some_future_key")

def test_44_reconcile_never_polls_the_candidate(tmp_path, monkeypatch):
    # NEGATIVE CONTROL for the §5 verdict. _RECONCILABLE deliberately INCLUDES needs_reconcile, so a
    # candidate used as a poll key would be polled every pass.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "fanops_token"; p.reconcile_candidate_id = "z_other"
    led = _led(cfg, p)
    asked = []
    def _poll(sid):
        asked.append(sid); return {"status": "pending"}
    rec_mod.reconcile_posts(led, cfg, get_status=_poll)
    assert "z_other" not in asked, "reconcile polled the unproven candidate"

def test_45_reconcile_never_promotes_from_the_candidate(tmp_path, monkeypatch):
    # The misattribution this design exists to prevent: poll the candidate, find it live (of course it is —
    # that is WHY Zernio rejected us as a duplicate) and stamp OUR row `published` with ANOTHER post's
    # permalink. Here the candidate polls `published`; the post's own id does not.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "fanops_token"; p.reconcile_candidate_id = "z_other"
    led = _led(cfg, p)
    def _poll(sid):
        if sid == "z_other":
            return {"status": "published", "publicUrl": "https://tiktok.com/@x/video/999"}
        return {"status": "pending"}
    out = rec_mod.reconcile_posts(led, cfg, get_status=_poll)
    after = out.posts[p.id]
    assert after.state is not PostState.published
    assert after.public_url != "https://tiktok.com/@x/video/999"

def test_45b_a_failed_poll_never_downgrades_a_candidate_bearing_row(tmp_path, monkeypatch):
    # CodeRabbit MAJOR (PR #696), valid: a `failed` poll of THIS row's OWN submission_id does not disprove
    # the 409 candidate — they name DIFFERENT objects, and we never polled the candidate. Downgrading to
    # `failed` makes the row RE-QUEUEABLE and licences a re-POST while a possibly-live duplicate stands.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "z_mine"; p.reconcile_candidate_id = "z_other"
    out = rec_mod.reconcile_posts(_led(cfg, p), cfg,
                                  get_status=lambda sid: {"status": "failed", "errorMessage": "rejected upstream"})
    after = out.posts[p.id]
    assert after.state is PostState.needs_reconcile, "a candidate-bearing row must never become re-queueable"
    assert after.reconcile_candidate_id == "z_other"      # preserved
    assert after.submission_id == "z_mine"                # never overwritten by the candidate
    assert "candidate=z_other" in after.error_reason and "UNVERIFIED" in after.error_reason

def test_45c_a_held_candidate_row_is_not_selected_by_the_transient_requeue(tmp_path, monkeypatch):
    # The whole point of holding needs_reconcile: _requeue_transient_failed_for_daemon reads
    # posts_in_state(failed) ONLY, so a held row can never be auto-re-POSTed.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "z_mine"; p.reconcile_candidate_id = "z_other"
    out = rec_mod.reconcile_posts(_led(cfg, p), cfg,
                                  get_status=lambda sid: {"status": "failed", "errorMessage": "read timed out"})
    out.save()
    assert run_mod._requeue_transient_failed_for_daemon(cfg) == 0
    after = Ledger.load(cfg).posts[p.id]
    assert after.state is PostState.needs_reconcile
    # NB the errorMessage says "read timed out" — a phrase is_transient_failure_reason MATCHES. Holding the
    # row out of `failed` is what makes that harmless; a downgrade would have re-queued it on that wording.
    assert is_transient_failure_reason(after.error_reason) is True

def test_45d_a_failed_poll_without_a_candidate_still_fails_ordinarily(tmp_path, monkeypatch):
    # The B-case: no candidate => unchanged pre-existing behaviour. Negative control for 45b/45c.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "z_mine"                            # no reconcile_candidate_id
    out = rec_mod.reconcile_posts(_led(cfg, p), cfg,
                                  get_status=lambda sid: {"status": "failed", "errorMessage": "rejected upstream"})
    after = out.posts[p.id]
    assert after.state is PostState.failed
    assert "poster reports failed" in after.error_reason
    assert after.reconcile_candidate_id is None

def test_46_candidate_is_mirrored_into_error_reason(tmp_path, monkeypatch):
    # extra="ignore" means an older binary drops the field; the mirror is then the only surviving copy.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(409, {"details": {"existingPostId": "z_other"}})), monkeypatch)
    assert "candidate=z_other" in p.error_reason

def test_47_reconcile_clears_the_candidate_on_an_explicit_identity_decision(tmp_path, monkeypatch):
    # Resolved on THIS row's own submission_id + the platform liveness gate — evidence that never touched the
    # candidate. Spent evidence must not outlive the ambiguity it described.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops import reconcile as rec_mod
    monkeypatch.setattr(rec_mod, "_tiktok_url_confirmed", lambda *a, **k: True)
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "z_real"; p.reconcile_candidate_id = "z_other"
    led = _led(cfg, p)
    out = rec_mod.reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": "https://tiktok.com/@x/video/1", "postSubmissionId": "z_real"})
    after = out.posts[p.id]
    assert after.state is PostState.published
    assert after.reconcile_candidate_id is None

def test_48_ui_renders_the_candidate_distinctly_from_the_submission_id(tmp_path, monkeypatch):
    # "submission_id" means "the backend id OF this post"; a candidate means "a record the backend holds that
    # MIGHT be this post". The UI must not let the two read alike.
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops.studio.views_results import inflight_watch
    p = _post(); p.state = PostState.needs_reconcile
    p.submission_id = "fanops_token"; p.reconcile_candidate_id = "z_other"
    row = inflight_watch(_led(cfg, p), cfg)[0]
    assert row.reconcile_candidate_id == "z_other"
    assert row.submission_id == "fanops_token"
    tpl = Path(__file__).resolve().parents[1] / "src/fanops/studio/templates/_reconcile_strip.html"
    body = tpl.read_text()
    assert "reconcile_candidate_id" in body and "unverified" in body.lower()
    css = Path(__file__).resolve().parents[1] / "src/fanops/studio/static/studio.css"
    assert ".reconcile-candidate" in css.read_text()


# ============================ PRESERVED — tests 49-61 ============================

def test_49_401_raises_zernio_auth_error_and_halts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(zernio.requests, "post", _Rec(_R(401, {})))
    p = _post(); led = _led(cfg, p)
    with pytest.raises(ZernioAuthError):
        ZernioPoster(cfg).publish(led, p.id)

def test_50_5xx_parks_needs_reconcile(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(503, {})), monkeypatch)
    assert p.state is PostState.needs_reconcile and "http_5xx" in p.error_reason

def test_51_other_4xx_fails_with_the_body_withheld(tmp_path, monkeypatch):
    # The body stays withheld (as before this fix): this reason is scanned by is_transient_failure_reason,
    # and a response body echoing "timeout"/"(503)" would flip a terminal 4xx into a re-queue loop.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _publish(cfg, _post(), _Rec(_R(400, {"error": "read timed out upstream (503)"})), monkeypatch)
    assert p.state is PostState.failed
    assert "read timed out" not in p.error_reason
    assert is_transient_failure_reason(p.error_reason) is False

def test_52_connecttimeout_is_retried(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rec = _Rec(requests.exceptions.ConnectTimeout("blip"), _R(201, {"_id": "z1"}))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert len(rec.calls) == 2 and p.state is PostState.submitted

def test_53_other_request_exception_parks_needs_reconcile(tmp_path, monkeypatch):
    # The body may have landed (the response, not the request, was lost) — never `failed`.
    cfg = _cfg(tmp_path, monkeypatch)
    rec = _Rec(requests.exceptions.ConnectionError("reset"))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert p.state is PostState.needs_reconcile
    assert len(rec.calls) == 1, "re-POSTed after a possible landing"

def test_53b_transport_error_text_is_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rec = _Rec(requests.exceptions.ConnectionError("boom with key sk_test in it"))
    p = _publish(cfg, _post(), rec, monkeypatch)
    assert "sk_test" not in (p.error_reason or "")

def test_54_claim_still_refuses_a_post_carrying_a_real_submission_id(tmp_path, monkeypatch):
    # The never-re-POST invariant is NOT replaced by idempotency: a ~5-min window cannot span the 600s
    # daemon interval, so the queued-only claim still carries CROSS-PASS safety.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.state = PostState.queued; p.submission_id = "z_real"
    led = _led(cfg, p); led.save()
    rec = _Rec(_R(201, {"_id": "z2"}))
    monkeypatch.setattr(zernio.requests, "post", rec)
    assert run_mod._publish_one(cfg, p.id, "zernio", account_id="acc_abc") is None
    assert rec.calls == []
    assert Ledger.load(cfg).posts[p.id].state is PostState.queued

def test_55_needs_reconcile_is_never_downgraded_to_failed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for body, code in (({"details": {"existingPostId": "z"}}, 409), ({}, 503)):
        p = _publish(cfg, _post(), _Rec(_R(code, body)), monkeypatch)
        assert p.state is PostState.needs_reconcile

def test_56_daemon_requeue_ignores_needs_reconcile(tmp_path, monkeypatch):
    # `failed` is re-queueable; needs_reconcile must never be. A 409 landing in failed would be a licence to
    # re-post a duplicate — R-3's actual consequence.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.state = PostState.needs_reconcile
    p.error_reason = "zernio duplicate_content_409: candidate=z_other ..."
    led = _led(cfg, p); led.save()
    run_mod._requeue_transient_failed_for_daemon(cfg)
    assert Ledger.load(cfg).posts[p.id].state is PostState.needs_reconcile

def test_57_the_four_burned_failed_records_need_no_migration(tmp_path, monkeypatch):
    # A `failed` row written before this change loads with reconcile_candidate_id=None and is untouched by it.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post(); p.state = PostState.failed
    p.error_reason = "zernio upload failed (405)"
    led = _led(cfg, p); led.save()
    after = Ledger.load(cfg).posts[p.id]
    assert after.state is PostState.failed and after.reconcile_candidate_id is None
    assert after.error_reason == "zernio upload failed (405)"

def test_58_no_api_key_reaches_the_error_reason(tmp_path, monkeypatch):
    # A 4xx/5xx debug or WAF page can reflect the presented key. Withholding the body is what stops it here.
    cfg = _cfg(tmp_path, monkeypatch)
    for r in (_R(400, {"debug": "presented key sk_test"}), _R(503, {"debug": "sk_test"}),
              _R(409, {"message": "dup; key sk_test"})):
        p = _publish(cfg, _post(), _Rec(r), monkeypatch)
        assert "sk_test" not in (p.error_reason or "")

def test_59_the_poster_protocol_signature_is_unchanged():
    # D7 NEGATIVE CONTROL. The typed result is PRIVATE to the Zernio backend and must never cross the shared
    # protocol — an unrelated backend does not pay for a Zernio contract quirk.
    sig = inspect.signature(Poster.publish)
    assert list(sig.parameters) == ["self", "led", "post_id"]
    assert inspect.signature(ZernioPoster.publish).return_annotation == sig.return_annotation

def test_60_postiz_does_not_know_about_the_zernio_outcome_types():
    # D7 NEGATIVE CONTROL: structural proof that postiz.py was not dragged into this change.
    src = Path(inspect.getfile(__import__("fanops.post.postiz", fromlist=["x"]))).read_text()
    assert "zernio_outcome" not in src and "ReconciliationRequired" not in src
    from fanops.post.postiz import PostizPoster
    assert list(inspect.signature(PostizPoster.publish).parameters) == ["self", "led", "post_id"]

def test_61_dryrun_does_not_know_about_the_zernio_outcome_types():
    src = Path(inspect.getfile(__import__("fanops.post.dryrun", fromlist=["x"]))).read_text()
    assert "zernio_outcome" not in src and "ReconciliationRequired" not in src
    from fanops.post.dryrun import DryRunPoster
    assert list(inspect.signature(DryRunPoster.publish).parameters) == ["self", "led", "post_id"]


# ============================ OUTCOME MAPPING (§4/§9) ============================

def test_created_and_replay_take_the_identical_ledger_state(tmp_path, monkeypatch):
    # Publication semantics are identical — the replay differs ONLY by the audit event. If they diverged, a
    # recovered publish would be treated as a lesser outcome than the create it recovered.
    cfg = _cfg(tmp_path, monkeypatch)
    a = _publish(cfg, _post(pid="pa"), _Rec(_R(201, {"_id": "z1"})), monkeypatch)
    b = _publish(cfg, _post(pid="pb"), _Rec(_R(200, {"existingPost": {"_id": "z1"}})), monkeypatch)
    assert (a.state, a.submission_id) == (b.state, b.submission_id) == (PostState.submitted, "z1")
    assert a.reconcile_candidate_id is b.reconcile_candidate_id is None

def test_idempotent_replay_is_audited(tmp_path, monkeypatch):
    # A replay means a send DID land and we recovered it instead of double-posting — the whole point of the
    # header, so it must be visible in the log.
    cfg = _cfg(tmp_path, monkeypatch)
    p = _post()
    _publish(cfg, p, _Rec(_R(200, {"existingPost": {"_id": "z1"}})), monkeypatch)
    body = cfg.log_path.read_text()
    assert "idempotent_replay" in body
    assert _request_id(p) in body

def test_outcome_types_are_immutable_values():
    for t in (Created("a"), IdempotentReplay("a"), ReconciliationRequired("r", "e"), TerminalFailure("r", "e")):
        with pytest.raises(Exception):
            t.reason = "mutated"                          # frozen: a result is evidence, not a scratchpad

def test_reconciliation_required_defaults_to_no_candidate():
    assert ReconciliationRequired("r", "e").candidate_post_id is None

def test_extract_409_candidate_shapes():
    assert _extract_409_candidate({"details": {"existingPostId": "z"}}) == "z"
    for bad in ({}, {"details": None}, {"details": {"existingPostId": ""}}, {"details": {"existingPostId": 7}}, "nope", None):
        assert _extract_409_candidate(bad) is None

def test_max_retries_is_bounded():
    assert 1 < _MAX_RETRIES <= 6
