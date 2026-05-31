import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post import get_poster
from fanops.post.dryrun import DryRunPoster

def test_factory_defaults_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert isinstance(get_poster(Config(root=tmp_path)), DryRunPoster)

def test_dryrun_writes_payload_with_media(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98432",
                      platform=Platform.instagram, caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2026-06-02T18:00:00Z", state=PostState.queued))
    led = DryRunPoster(cfg).publish(led, "p1")
    body = json.loads((cfg.scheduled / "p1.json").read_text())
    assert body["post"]["content"]["text"] == "hello"
    assert body["post"]["content"]["mediaUrls"] == ["https://h/v.mp4"]
    assert body["post"]["accountId"] == "98432"
    assert led.posts["p1"].state is PostState.submitted
