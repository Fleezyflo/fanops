import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post import get_poster
from fanops.post.dryrun import DryRunPoster

def test_factory_defaults_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)            # don't inherit a leaked live flag from a prior test
    assert isinstance(get_poster(Config(root=tmp_path)), DryRunPoster)

def test_dryrun_writes_payload_but_no_distribution_artifacts(tmp_path):
    # dryrun-boundary M2: DryRunPoster.publish is now a PREVIEW writer — it writes the would-send
    # sidecar and touches NOTHING else. A dry run does not distribute, so it fabricates no state/id/url.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98432",
                      platform=Platform.instagram, caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=PostState.queued))
    led = DryRunPoster(cfg).publish(led, "p1")
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    # Backend-neutral would-send preview: flat fields, the honest record of what a real poster WOULD send.
    assert body["text"] == "hello"
    assert body["media_urls"] == ["https://h/v.mp4"]
    assert body["account_id"] == "98432"
    assert body["platform"] == "instagram"
    # No distribution artifacts: state unchanged, no synthetic id, no synthetic url.
    assert led.posts["p1"].state is PostState.queued
    assert led.posts["p1"].submission_id is None
    assert led.posts["p1"].public_url is None


def test_dryrun_stamps_no_synthetic_submission_id(tmp_path):
    # dryrun-boundary M2 (was: AUDIT C4 synthetic id). Post-M1 a dryrun post halts `queued` at the
    # publish_due boundary and never reaches a distribution state, so it is never in track.py's pollable
    # set — the old `dryrun_` learning loop is dead. The preview writer therefore stamps NO submission_id.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p9", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.tiktok, caption="x", media_urls=["https://h/v.mp4"],
                      state=PostState.queued))
    led = DryRunPoster(cfg).publish(led, "p9")
    assert led.posts["p9"].submission_id is None
    assert led.posts["p9"].state is PostState.queued
