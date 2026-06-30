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

def test_dryrun_writes_payload_with_media(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98432",
                      platform=Platform.instagram, caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=PostState.queued, public_url=f"dryrun://p1"))
    led = DryRunPoster(cfg).publish(led, "p1")
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    assert body["post"]["content"]["text"] == "hello"
    assert body["post"]["content"]["mediaUrls"] == ["https://h/v.mp4"]
    assert body["post"]["accountId"] == "98432"
    assert led.posts["p1"].state is PostState.submitted


def test_dryrun_sets_synthetic_submission_id(tmp_path):
    # AUDIT C4: dryrun must emulate the real posters and stamp a submission_id, else track.py
    # (which binds metrics rows by submission_id) can NEVER reach a dryrun post -> the whole
    # learning loop is dead in the default backend. The id mirrors dryrun_media_url's pattern:
    # an honest, self-documenting stand-in for the real Blotato field, derived from the post id.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p9", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.tiktok, caption="x", media_urls=["https://h/v.mp4"],
                      state=PostState.queued, public_url=f"dryrun://p9"))
    led = DryRunPoster(cfg).publish(led, "p9")
    assert led.posts["p9"].submission_id == "dryrun_p9"
    assert led.posts["p9"].state is PostState.submitted
