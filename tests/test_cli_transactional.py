"""CLI write verbs persist under Ledger.transaction; network stays outside the flock.

Iron law: call cli.main / named cmd_*; mock only requests. No fanops.* setattr.
"""
import json

from fanops.cli import main
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (
    Clip,
    ClipState,
    Moment,
    MomentState,
    Platform,
    Post,
    PostState,
    Source,
    SourceState,
)
from tests.conftest import ledger_lock_is_free as _ledger_lock_is_free


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        return self._payload


def _save_empty(tmp_path):
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    return cfg


def _analyzed(led, pid, *, lift, sid="src_1"):
    if sid not in led.sources:
        led.add_source(Source(
            id=sid, source_path="/s.mp4", state=SourceState.moments_decided, duration=30.0,
            transcript=[{"start": 14, "end": 18, "text": "they slept on me"}],
            signal_peaks=[], meta={"transcribed": True}))
    mid, cid = f"m_{pid}", f"c_{pid}"
    led.add_moment(Moment(
        id=mid, parent_id=sid, content_token="14-21", start=14, end=21,
        reason="punchline", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(
        id=pid, parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
        caption="x", state=PostState.analyzed, metrics={"lift_score": lift},
        public_url=f"https://example.test/{pid}"))


def test_cmd_adjust_persists_amplify_on_a_winner(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _analyzed(led, "p1", lift=400.0)
    assert main(["adjust"]) == 0
    assert "winners=" in capsys.readouterr().out
    again = Ledger.load(cfg)
    parked = again.sources["src_1"].meta.get("pending_reopen") or {}
    assert parked.get("origin") == "amplify"


def test_cmd_ingest_empty_inbox_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _save_empty(tmp_path)
    assert main(["ingest"]) == 0
    out = capsys.readouterr().out.lower()
    assert "ingested" in out
    assert Ledger.load(Config(root=tmp_path)).sources == {}


def test_cmd_track_empty_ledger_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _save_empty(tmp_path)
    assert main(["track"]) == 0
    assert "tracked" in capsys.readouterr().out.lower()


def test_cmd_track_network_runs_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(
            id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
            caption="x", state=PostState.published, submission_id="postiz_1",
            public_url="https://example.test/p"))
    seen = {}

    def fake_get(url, **kw):
        seen["lock_free"] = _ledger_lock_is_free(cfg)
        seen["url"] = url
        return _Resp([])

    monkeypatch.setattr("requests.get", fake_get)
    assert main(["track"]) == 0
    assert seen.get("lock_free") is True
    assert "analytics" in (seen.get("url") or "")


def test_learn_pass_fetch_runs_outside_the_lock(tmp_path, monkeypatch):
    from fanops.cli import _learn_pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(
            id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
            caption="x", state=PostState.published, submission_id="postiz_1",
            public_url="https://example.test/p"))
    seen = {}

    def fake_get(url, **kw):
        seen["lock_free"] = _ledger_lock_is_free(cfg)
        return _Resp([])

    monkeypatch.setattr("requests.get", fake_get)
    _learn_pass(cfg)
    assert seen.get("lock_free") is True


def test_learn_pass_does_not_amplify_or_retire_by_default(tmp_path, monkeypatch):
    from fanops.cli import _learn_pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_LEARN_AMPLIFY", raising=False)
    monkeypatch.delenv("FANOPS_LEARN_RETIRE", raising=False)
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _analyzed(led, "p1", lift=400.0)
        _analyzed(led, "p2", lift=1.0)
    _learn_pass(cfg)
    again = Ledger.load(cfg)
    assert "pending_reopen" not in again.sources["src_1"].meta
    assert int(again.sources["src_1"].meta.get("amplify_count", 0)) == 0
    assert again.clips["c_p2"].state is ClipState.analyzed


def test_learn_pass_amplifies_only_with_intent(tmp_path, monkeypatch):
    from fanops.cli import _learn_pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    monkeypatch.delenv("FANOPS_LEARN_AMPLIFY", raising=False)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _analyzed(led, "p1", lift=400.0)
    _learn_pass(cfg)
    assert "pending_reopen" not in Ledger.load(cfg).sources["src_1"].meta
    monkeypatch.setenv("FANOPS_LEARN_AMPLIFY", "1")
    _learn_pass(Config(root=tmp_path))
    parked = Ledger.load(cfg).sources["src_1"].meta.get("pending_reopen") or {}
    assert parked.get("origin") == "amplify"


def test_learn_pass_retires_only_with_intent(tmp_path, monkeypatch):
    from fanops.cli import _learn_pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_LEARN_RETIRE", raising=False)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        for i, lift in enumerate((80, 70, 60, 50, 40, 30, 20, 10), start=1):
            _analyzed(led, f"p{i}", lift=float(lift), sid=f"src_{i}")
    _learn_pass(cfg)
    assert all(Ledger.load(cfg).clips[f"c_p{i}"].state is ClipState.analyzed for i in range(1, 9))
    monkeypatch.setenv("FANOPS_LEARN_RETIRE", "1")
    _learn_pass(Config(root=tmp_path))
    recs = [json.loads(ln) for ln in cfg.log_path.read_text().splitlines() if ln.strip()]
    retired = [r for r in recs if r.get("stage") == "learn" and r.get("outcome") == "retired"]
    assert retired and int(retired[-1]["losers"]) >= 1


def test_learn_pass_with_both_flags_off_logs_skips_and_writes_nothing(tmp_path, monkeypatch):
    from fanops.cli import _learn_pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_LEARN_AMPLIFY", raising=False)
    monkeypatch.delenv("FANOPS_LEARN_RETIRE", raising=False)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _analyzed(led, "p1", lift=400.0)
        _analyzed(led, "p2", lift=1.0)
    _learn_pass(cfg)
    recs = [json.loads(ln) for ln in cfg.log_path.read_text().splitlines() if ln.strip()]
    skipped = {r["outcome"]: r for r in recs if r.get("stage") == "learn"}
    assert int(skipped["amplify_skipped"]["winners"]) >= 1
    assert "retire_skipped" in skipped
    assert "pending_reopen" not in Ledger.load(cfg).sources["src_1"].meta


def test_cmd_map_media_uses_a_transaction(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _save_empty(tmp_path)
    assert main(["map-media"]) == 0
    assert "media mapped" in capsys.readouterr().out.lower()


def test_cmd_map_media_network_runs_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok")
    monkeypatch.setenv("META_IG_USER_ID", "ig1")
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    seen = {}

    def fake_get(url, **kw):
        seen["lock_free"] = _ledger_lock_is_free(cfg)
        return _Resp({"data": []})

    monkeypatch.setattr("requests.get", fake_get)
    assert main(["map-media"]) == 0
    assert seen.get("lock_free") is True


def test_cmd_reconcile_poll_runs_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(
            id="p", parent_id="c", account="tt", account_id="1", platform=Platform.tiktok,
            caption="x", state=PostState.needs_reconcile, submission_id="zreal_1",
            public_url="dryrun://p"))
    seen = {}

    def fake_get(url, **kw):
        seen["lock_free"] = _ledger_lock_is_free(cfg)
        return _Resp({"status": "in-progress"})

    monkeypatch.setattr("requests.get", fake_get)
    assert main(["reconcile"]) == 0
    assert seen.get("lock_free") is True


def test_cmd_reconcile_still_promotes_published(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(
            id="p", parent_id="c", account="tt", account_id="1", platform=Platform.tiktok,
            caption="x", state=PostState.needs_reconcile, submission_id="zreal_1",
            public_url="dryrun://p"))

    def fake_get(url, **kw):
        url = str(url)
        if "oembed" in url:
            return _Resp({"author_unique_id": "tt", "author_url": "https://www.tiktok.com/@tt"})
        return _Resp({
            "status": "published",
            "publicUrl": "https://www.tiktok.com/@tt/video/7",
            "platforms": [{"platform": "tiktok", "status": "published",
                           "platformPostUrl": "https://www.tiktok.com/@tt/video/7",
                           "accountId": {"username": "tt"}}],
        })

    monkeypatch.setattr("requests.get", fake_get)
    assert main(["reconcile"]) == 0
    again = Ledger.load(cfg)
    assert again.posts["p"].state is PostState.published
    assert again.posts["p"].public_url == "https://www.tiktok.com/@tt/video/7"


def test_cmd_reconcile_postiz_date_windows_each_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(
            id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
            caption="x", state=PostState.needs_reconcile, submission_id="postiz_9",
            scheduled_time="2099-01-01T00:00:00Z", public_url="dryrun://p"))
    seen = {}

    def fake_get(url, **kw):
        seen["params"] = kw.get("params")
        return _Resp({"posts": [{"id": "postiz_9", "state": "PUBLISHED",
                                 "releaseURL": "https://www.instagram.com/reel/X/"}]})

    monkeypatch.setattr("requests.get", fake_get)
    assert main(["reconcile"]) == 0
    p = seen.get("params") or {}
    assert "date" not in p and p["startDate"] <= "2099-01-01" <= p["endDate"]
    assert Ledger.load(cfg).posts["p"].state is PostState.published


def test_cmd_reconcile_postiz_without_key_skips_cleanly(tmp_path, monkeypatch, capsys):
    from fanops import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    assert cli.cmd_reconcile(cfg) == 0
    assert "reconciled" in capsys.readouterr().out
