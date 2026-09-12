# tests/test_dryrun_boundary.py
# dryrun-boundary M1 (PRD Finding #1): a dryrun post is built, approved, scheduled — and then simply is
# NOT eligible to enter distribution, because there is no real backend to distribute it to. The boundary is
# enforced at the single chokepoint that already resolves the provider: publish_due claims a post ONLY when
# its resolved provider is a REAL backend. A dryrun post (cfg.is_live False -> provider "dryrun") stays
# `queued` — approved + scheduled + built, awaiting a backend that never comes. No new state, no fabricated
# submission_id/public_url, no threading.
#
# These tests drive the REAL chokepoint (publish_due), NOT DryRunPoster.publish directly (that poster
# contract is M2's). Pure-fixture: a not-live Config + a seeded due `queued` post.
#
# test_ship_route_steps_2_6_dryrun_smoke — PR unit proof for docs/design/v0.1-ship-route.md steps 2–6
# (dryrun; step 6 writes preview sidecar, never distributes).
import json
import os, stat
from datetime import datetime, timezone

from fanops import doctor
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform, Clip, ClipState, Moment, MomentState, SourceState
from fanops.post.run import publish_due
from fanops.studio import actions, views
from fanops.studio.actions import approve_posts


def _cfg(tmp_path, monkeypatch):
    # NOT live: no FANOPS_LIVE, no live poster -> _post_provider returns "dryrun" for every post.
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    return Config(root=tmp_path)


def _due_queued_post(pid="p1", *, plat=Platform.instagram):
    # An approved (queued) post whose schedule is already due (past), so publish_due considers it.
    return Post(id=pid, parent_id="c1", account="a", account_id="98432", platform=plat,
                caption="hello", media_urls=["file:///tmp/v.mp4"],
                scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued)


def _lineage(led):
    """Materialize the moment -> clip "c1" ancestry the post names. It was never built, so the fixture made an
    ORPHAN post — a shape production cannot produce. Harmless while the publish guard failed OPEN on a missing
    ancestor; publish_due now asks Ledger.can_promote, which fails CLOSED. These tests pin the DRYRUN boundary,
    so the post must be refused for having no live backend — not for having no lineage."""
    led.add_moment(Moment(id="m1", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", state=ClipState.queued))


def test_dryrun_publish_due_leaves_queued(tmp_path, monkeypatch):
    # THE boundary: publish_due on a dryrun (not-live) system must leave an approved post `queued` — it is
    # built and scheduled, but has no live channel, so it never enters the distribution rail.
    cfg = _cfg(tmp_path, monkeypatch)
    led = Ledger.load(cfg)
    _lineage(led)
    led.add_post(_due_queued_post("p1"))
    led.save()

    summary = publish_due(cfg)

    post = Ledger.load(cfg).posts["p1"]
    assert post.state is PostState.queued                       # NOT submitting/submitted/published
    assert summary["published"] == 0                            # nothing entered distribution


def test_dryrun_publish_due_mints_no_distribution_artifacts(tmp_path, monkeypatch):
    # The M1 boundary path must NOT fabricate the phantom-publish artifacts: no dryrun_ submission_id, no
    # dryrun:// public_url. (A dryrun post never reaches the poster, so nothing stamps them.)
    cfg = _cfg(tmp_path, monkeypatch)
    led = Ledger.load(cfg)
    _lineage(led)
    led.add_post(_due_queued_post("p1"))
    led.save()

    publish_due(cfg)

    post = Ledger.load(cfg).posts["p1"]
    assert post.submission_id is None                           # no dryrun_<id> minted
    assert post.public_url is None                              # no dryrun://<id> minted


def test_dryrun_boundary_writes_preview_not_artifacts(tmp_path, monkeypatch):
    # M2: the boundary is the ONLY place a dryrun post is now processed (DryRunPoster.publish is never
    # called post-M1). So the would-send PREVIEW sidecar must be written HERE — an honest "here's what
    # WOULD ship, nothing was sent" record — while stamping NONE of the phantom-publish artifacts and
    # leaving the post `queued`.
    cfg = _cfg(tmp_path, monkeypatch)
    led = Ledger.load(cfg)
    _lineage(led)
    led.add_post(_due_queued_post("p1"))
    led.save()

    publish_due(cfg)

    sidecar = cfg.scheduled / "p1.json"
    assert sidecar.exists()                                     # preview WAS written at the boundary
    assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o600     # owner-only at rest (caption/media/target)
    led = Ledger.load(cfg)
    post = led.posts["p1"]
    assert post.state is PostState.queued                      # still held at the boundary
    assert post.submission_id is None and post.public_url is None   # no fabricated distribution artifacts
    from fanops.caption_compose import posted_text_for
    payload = json.loads(sidecar.read_text())
    assert payload["text"] == posted_text_for(cfg, led, post)


def _ship_route_workspace(cfg):
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("BRAND: dryrun smoke.")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}, "persona": "v"},
    ]}))


def _awaiting_post(pid="p_smoke"):
    return Post(id=pid, parent_id="c1", account="ig", account_id="ig_1", platform=Platform.instagram,
                caption="hello", media_urls=["file:///tmp/v.mp4"],
                scheduled_time="2099-01-01T00:00:00Z", state=PostState.awaiting_approval)


def test_ship_route_steps_2_6_dryrun_smoke(tmp_path, monkeypatch, mocker):
    # v0.1 ship route steps 2–6 in one dryrun workspace (docs/design/v0.1-ship-route.md).
    cfg = _cfg(tmp_path, monkeypatch)
    _ship_route_workspace(cfg)
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")

    # §0 step 2 · Doctor — workspace structurally ready (brand brief + accounts valid).
    rep = doctor.doctor_report(cfg)
    brief = next(c for c in rep["checks"] if "brand brief" in c["label"].lower())
    accounts = next(c for c in rep["checks"] if "accounts.json" in c["label"])
    assert brief["ok"] and accounts["ok"]

    # §0 step 3 · Go Live intake — channel readiness matrix green (dryrun; no go_live flip).
    st = views.golive_status(cfg)
    assert st.next_blocker == "" and all(c.ready for c in st.channels)

    # §0 step 4 · Upload + ingest inbox.
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / "clip.mp4").write_bytes(b"Vclip")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    cat = actions.catalogue_inbox(cfg)
    assert cat.ok and cat.detail.get("added", 0) >= 1
    led = Ledger.load(cfg)
    assert any(s.state is SourceState.pending for s in led.sources.values())

    # §0 step 5 · Review approve → Schedule (seed post; prepare/crosspost tested elsewhere).
    _lineage(led)
    led.add_post(_awaiting_post())
    led.save()
    fixed = datetime(2026, 6, 21, tzinfo=timezone.utc)
    assert approve_posts(cfg, ["p_smoke"], now=fixed).ok is True
    assert Ledger.load(cfg).posts["p_smoke"].state is PostState.queued

    # §0 step 6 · Schedule → publish (dryrun): preview sidecar, post stays queued.
    with Ledger.transaction(cfg) as txn:
        txn.posts["p_smoke"].scheduled_time = "2020-01-01T00:00:00Z"
    summary = publish_due(cfg)
    post = Ledger.load(cfg).posts["p_smoke"]
    assert summary["published"] == 0
    assert post.state is PostState.queued
    assert post.submission_id is None and post.public_url is None
    assert (cfg.scheduled / "p_smoke.json").exists()
