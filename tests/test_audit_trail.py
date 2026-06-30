"""R3 RED — operator audit trail for state-changing actions.

PRD Evidence: this session, 5 posts went from `awaiting_approval` to `published` in 25
seconds and produced ghost rows (state=published, public_url=''). The only durable
record was the `published_at` timestamp on each post and 5 sidecar JSONs in 05_scheduled/.
No trace of WHICH Studio endpoint fired, WHICH operator batch the posts shared, OR
WHEN the bulk-revert (also done by hand this session) ran. The ledger records the
OUTCOME state but not the operator action that triggered the transition.

R3 fix: append-only `00_control/studio_audit.log` (one JSON line per state-changing
action) + a `bulk_send_to_review(post_ids, *, reason)` operator API for the
wipe-and-revert flow + per-batch grouping/filter on the Posted tub.

These tests pin: D7 (bulk_send_to_review API), D17 (audit log of state-changing
actions), D18 (Posted-tub batch grouping/filter)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Post, Clip, Source, Moment, Platform, PostState, ClipState,
                           MomentState, Fmt)
from fanops.timeutil import iso_z


_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = iso_z(_NOW)


# ---------- Task 1: audit log writer (D17 foundation) ----------

def test_write_audit_appends_jsonl(tmp_path):
    """R3/D17: write_audit appends ONE valid JSON line per call to
    00_control/studio_audit.log. The schema MUST include `ts`, `action`, `post_ids`,
    `reason` — the minimum needed to reconstruct who/what/when from the log."""
    from fanops.audit import write_audit
    cfg = Config(root=tmp_path)
    write_audit(cfg, "approve", ["p1", "p2"], reason="studio_approve_batch")
    write_audit(cfg, "publish_now", ["p3"], reason="studio_publish_now")

    audit_path = cfg.control / "studio_audit.log"
    assert audit_path.exists(), f"audit log not created at {audit_path}"
    lines = audit_path.read_text().splitlines()
    assert len(lines) == 2, f"expected 2 audit lines, got {len(lines)}: {lines}"
    e0 = json.loads(lines[0])
    assert e0["action"] == "approve"
    assert e0["post_ids"] == ["p1", "p2"]
    assert e0["reason"] == "studio_approve_batch"
    assert "ts" in e0 and e0["ts"].endswith("Z"), f"bad ts: {e0.get('ts')!r}"


def test_write_audit_never_raises_on_io_error(tmp_path, monkeypatch):
    """R3/D17 contract: audit is OBSERVABILITY, never a blocker. If the disk fills
    or the dir disappears, write_audit fails silently — the operator action MUST
    NOT raise just because the audit write failed."""
    from fanops.audit import write_audit
    cfg = Config(root=tmp_path)
    # Make audit_path resolve to a path that can't be opened (a directory, not a file).
    (cfg.control).mkdir(parents=True, exist_ok=True)
    (cfg.control / "studio_audit.log").mkdir()    # collide: directory where the file should go

    # Must NOT raise — audit failure is silent, never breaks the caller.
    write_audit(cfg, "approve", ["p1"], reason="test")


def test_write_audit_preserves_extra_kw(tmp_path):
    """R3/D17: extra kw (e.g. suggested_iso, count, handle) are persisted as fields
    on the JSON entry — the audit must carry per-action context, not just the action
    name."""
    from fanops.audit import write_audit
    cfg = Config(root=tmp_path)
    write_audit(cfg, "reschedule_bucket", ["p1", "p2", "p3"],
                reason="studio_reschedule_all", handle=None, rescheduled=3)

    e = json.loads((cfg.control / "studio_audit.log").read_text().splitlines()[0])
    assert e["handle"] is None
    assert e["rescheduled"] == 3


# ---------- Task 2: thread audit through state-changing actions (D17) ----------

def _seed_queued_post(cfg: Config, post_id: str = "p1", *,
                     state: PostState = PostState.queued,
                     scheduled_iso: str = _NOW_ISO,
                     public_url: str = "dryrun://p1") -> str:
    """Seed one (source, moment, clip, post) chain so the action APIs have something
    real to mutate. The post defaults to queued + scheduled now."""
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x.mp4", duration=10.0))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="c1", parent_id="m1", path="/c1.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "x", "hashtags": []}}
    led.add_clip(clip)
    led.add_post(Post(id=post_id, parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="c", state=state,
                      scheduled_time=scheduled_iso,
                      media_urls=["file:///c1.mp4"], public_url=public_url))
    led.save()
    return post_id


def test_publish_now_writes_audit_entry(tmp_path, monkeypatch, mocker):
    """R3/D17: a successful publish_now MUST leave an audit breadcrumb naming the
    post id. The 5 ghost-publishes had no such trace."""
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1")
    def _fake_publish(_cfg, pid):
        led = Ledger.load(_cfg)
        led.posts[pid].state = PostState.published
        led.posts[pid].public_url = "https://www.instagram.com/p/audit/"
        led.save()
        return "published"
    mocker.patch("fanops.post.run.publish_post", side_effect=_fake_publish)
    from fanops.studio.actions import publish_now
    res = publish_now(cfg, "p1", confirmed=True)
    assert res.ok, f"publish_now failed: {res}"
    audit_path = cfg.control / "studio_audit.log"
    assert audit_path.exists(), "no audit log written by publish_now"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    pn = [e for e in entries if e["action"] == "publish_now"]
    assert pn, f"no publish_now audit entry: {entries}"
    assert "p1" in pn[0]["post_ids"]


def test_mark_published_writes_audit_entry(tmp_path):
    """R3/D17: 'I posted by hand' MUST be auditable — the operator-driven success
    path that produced 5 ghost-rows pre-R1 was the most opaque action of all."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1")
    from fanops.studio.actions import mark_published
    res = mark_published(cfg, "p1", url="https://www.instagram.com/p/abc/")
    assert res.ok
    entries = [json.loads(line) for line in
               (cfg.control / "studio_audit.log").read_text().splitlines()]
    mp = [e for e in entries if e["action"] == "mark_published"]
    assert mp, f"no mark_published audit entry: {entries}"
    assert "p1" in mp[0]["post_ids"]
    assert "instagram.com" in (mp[0].get("url") or "")


def test_approve_posts_writes_audit_entry(tmp_path, monkeypatch):
    """R3/D17: a batch approve writes ONE audit entry naming EVERY post promoted —
    not one entry per post (chatty), not zero (silent). The 5 ghost batch shared
    `batch_id=batch_c59d718170ea`; this gives the operator that batch back."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.awaiting_approval)
    _seed_queued_post(cfg, "p2", state=PostState.awaiting_approval, public_url="dryrun://p2")
    from fanops.studio.actions_approve import approve_posts
    res = approve_posts(cfg, ["p1", "p2"])
    assert res.ok, f"approve failed: {res}"
    entries = [json.loads(line) for line in
               (cfg.control / "studio_audit.log").read_text().splitlines()]
    ap = [e for e in entries if e["action"] == "approve"]
    assert len(ap) == 1, f"expected ONE batched approve entry, got {len(ap)}: {ap}"
    assert sorted(ap[0]["post_ids"]) == ["p1", "p2"], (
        f"batch entry missing one or both post ids: {ap[0]['post_ids']}")


def test_reschedule_bucket_writes_audit_entry(tmp_path, monkeypatch):
    """R3/D17: a respread is a state-changing action — the schedule MOVED on N
    posts. The audit names WHICH posts moved + the cadence trigger."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", scheduled_iso=iso_z(_NOW.replace(year=2020)))
    _seed_queued_post(cfg, "p2", scheduled_iso=iso_z(_NOW.replace(year=2020)),
                     public_url="dryrun://p2")
    from fanops.studio.actions import reschedule_bucket
    res = reschedule_bucket(cfg, now=_NOW)
    assert res.ok and res.detail["rescheduled"] == 2
    entries = [json.loads(line) for line in
               (cfg.control / "studio_audit.log").read_text().splitlines()]
    rb = [e for e in entries if e["action"] == "reschedule_bucket"]
    assert rb, f"no reschedule_bucket audit entry: {entries}"
    assert sorted(rb[0]["post_ids"]) == ["p1", "p2"]
    assert rb[0].get("rescheduled") == 2


def test_failed_action_does_not_write_audit_entry(tmp_path):
    """R3/D17 firewall: a FAILED action MUST NOT pollute the audit log — the log
    is the success trail, failures already log via get_logger. Otherwise a stuck
    operator clicking repeatedly fills the audit with non-events."""
    cfg = Config(root=tmp_path)
    from fanops.studio.actions import mark_published
    res = mark_published(cfg, "nope", url="https://x/y")    # unknown post
    assert not res.ok
    audit_path = cfg.control / "studio_audit.log"
    if audit_path.exists():
        lines = audit_path.read_text().splitlines()
        assert not lines, f"failed action wrote to audit log: {lines}"


# ---------- Task 3: bulk_send_to_review operator API (D7) ----------

def test_bulk_send_to_review_moves_posts(tmp_path):
    """R3/D7: the wipe-and-revert flow I ran by hand this session becomes a
    first-class API. Each id: state -> awaiting_approval; scheduled_time, public_url,
    metrics, published_at -> cleared. This is what an operator does after a
    bad-batch publish (or a config error like the cisumwolfhom drift) — it MUST be
    one atomic call, not 67 hand-edits."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.published,
                     public_url="https://www.instagram.com/p/old/")
    _seed_queued_post(cfg, "p2", state=PostState.queued, public_url="dryrun://p2")
    from fanops.studio.actions import bulk_send_to_review
    res = bulk_send_to_review(cfg, ["p1", "p2"], reason="bad_batch_revert")
    assert res.ok, f"bulk_send_to_review failed: {res}"
    assert res.detail["moved"] == 1 and res.detail["skipped"] == 1

    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published, "published posts must not bulk-revert"
    p2 = led.posts["p2"]
    assert p2.state is PostState.awaiting_approval
    assert not (p2.scheduled_time or "")
    assert not (p2.public_url or "")


def test_bulk_send_to_review_rejects_unknown_id(tmp_path):
    """R3/D7: an unknown id in the bulk is surfaced cleanly (count + the bad id),
    NOT silently dropped. Otherwise an operator typo passes for success."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1")
    from fanops.studio.actions import bulk_send_to_review
    res = bulk_send_to_review(cfg, ["p1", "nope"], reason="test")
    # The action is best-effort per id: it moves what it can, but the unknown id
    # is named in the result so the operator sees the typo.
    assert "nope" in str(res.detail.get("unknown") or []), (
        f"unknown id was silently dropped: {res.detail}")


def test_bulk_send_to_review_writes_audit_entry(tmp_path):
    """R3/D7+D17: the bulk-revert MUST audit — this is the most operator-impactful
    action in the system (it undoes a publish-or-schedule batch). The reason field
    is the operator's intent ('bad_batch_revert' / 'config_drift_repair' / etc)."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.queued, public_url="dryrun://p1")
    _seed_queued_post(cfg, "p2", state=PostState.queued, public_url="dryrun://p2")
    from fanops.studio.actions import bulk_send_to_review
    bulk_send_to_review(cfg, ["p1", "p2"], reason="bad_batch_revert")
    entries = [json.loads(line) for line in
               (cfg.control / "studio_audit.log").read_text().splitlines()]
    bsr = [e for e in entries if e["action"] == "bulk_send_to_review"]
    assert len(bsr) == 1, f"expected ONE bulk audit entry, got {len(bsr)}: {bsr}"
    assert sorted(bsr[0]["post_ids"]) == ["p1", "p2"]
    assert bsr[0]["reason"] == "bad_batch_revert"


# ---------- Task 4: CLI verbs ----------

def test_cli_audit_tail_prints_lines(tmp_path, monkeypatch, capsys):
    """R3/D7: `fanops audit tail` reads the audit log and prints the last N lines —
    the operator's review-what-just-happened command."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    from fanops.audit import write_audit
    for i in range(3):
        write_audit(cfg, "publish_now", [f"p{i}"], reason="t")
    from fanops.cli import main
    rc = main(["audit", "tail"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "publish_now" in out, f"audit tail did not print actions: {out!r}"
    assert "p0" in out and "p1" in out and "p2" in out


def test_cli_bulk_send_to_review(tmp_path, monkeypatch, capsys):
    """R3/D7: `fanops bulk-send-to-review p1 p2 --reason=…` runs the API from CLI —
    the CLI parity for the future Studio button."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.queued, public_url="dryrun://p1")
    from fanops.cli import main
    rc = main(["bulk-send-to-review", "p1", "--reason", "test_revert"])
    assert rc == 0
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.awaiting_approval


# ---------- Task 5: Posted-tub batch grouping/filter (D18) ----------
# NOTE: posted_library already accepts `batch=` (views_results.py:230) — what's
# missing is the chip rendering in the template and a Studio route that wires the
# query param. These tests pin both the library function (already there) AND the
# template chip (the surface).

def test_posted_library_filters_by_batch(tmp_path):
    """R3/D18 (already-built portion): the underlying library DOES filter by
    batch_id today. Pin it so the filter never regresses."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.published,
                     public_url="https://www.instagram.com/p/abc1/")
    _seed_queued_post(cfg, "p2", state=PostState.published,
                     public_url="https://www.instagram.com/p/abc2/")
    led = Ledger.load(cfg)
    led.posts["p1"].batch_id = "batch_A"; led.posts["p2"].batch_id = "batch_B"
    led.save()

    from fanops.studio.views_results import posted_library
    rows_a = posted_library(led, cfg, batch="batch_A")
    rows_b = posted_library(led, cfg, batch="batch_B")
    assert [r.post_id for r in rows_a] == ["p1"], (
        f"batch=batch_A returned wrong rows: {[r.post_id for r in rows_a]}")
    assert [r.post_id for r in rows_b] == ["p2"]


def test_posted_view_chip_links_to_batch_filter(tmp_path):
    """R3/D18: the Posted tub renders a per-row batch chip clickable to the
    ?batch=<id> filter — the operator can drill into a specific batch from the
    posted library without typing a query param by hand."""
    cfg = Config(root=tmp_path)
    _seed_queued_post(cfg, "p1", state=PostState.published,
                     public_url="https://www.instagram.com/p/abc1/")
    led = Ledger.load(cfg)
    led.posts["p1"].batch_id = "batch_c59d718170ea"   # the actual session batch id
    led.save()

    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/posted")
    assert r.status_code == 200
    body = r.data.decode()
    # The chip MUST link to the filter (?batch=...) and name the batch id.
    assert "batch_c59d718170ea" in body, (
        "Posted tub does not surface the batch id on the row")
    assert "?batch=batch_c59d718170ea" in body or "&batch=batch_c59d718170ea" in body, (
        "Posted tub does not link the batch chip to the filter")
