import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
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
