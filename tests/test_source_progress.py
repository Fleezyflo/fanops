# U2: per-source progress read-model + edited_at stamp (foundation; no UI).
from datetime import datetime, timezone, timedelta

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (
    Source, SourceState, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt,
)
from fanops.studio.actions import edit_caption, regenerate_caption, reburn_hook
from fanops.studio.views_library import SourceProgress, source_progress, _APPROVED_STATES
from fanops.timeutil import iso_z

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_progress_matrix(tmp_path):
    """1 source → 3 clips → 6 posts: clips=3, approved=3, rejected=1, edited=1, published=1."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/inbox/vid.mp4", language="en",
                          state=SourceState.moments_decided))
    for i, mid in enumerate(("m1", "m2", "m3"), start=1):
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token=f"{i}-{i+5}", start=i, end=i + 5,
                              reason="r", state=MomentState.decided))
        led.add_clip(Clip(id=f"c{i}", parent_id=mid, path=f"/clips/c{i}.mp4", aspect=Fmt.r9x16,
                          state=ClipState.captioned))
    # approved lane: queued, submitted, published (published=1)
    led.add_post(Post(id="p_q", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="q", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=2))))
    led.add_post(Post(id="p_sub", parent_id="c1", account="b", account_id="2", platform=Platform.tiktok,
                      caption="s", state=PostState.submitted, scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_pub", parent_id="c2", account="a", account_id="1", platform=Platform.instagram,
                      caption="pub", state=PostState.published, public_url="https://example.com/p",
                      published_at=_z(NOW - timedelta(days=1))))
    # rejected + awaiting + edited (edited_at set on one approved-ish row)
    led.add_post(Post(id="p_rej", parent_id="c2", account="b", account_id="2", platform=Platform.tiktok,
                      caption="rej", state=PostState.rejected))
    led.add_post(Post(id="p_await", parent_id="c3", account="a", account_id="1", platform=Platform.instagram,
                      caption="await", state=PostState.awaiting_approval))
    led.add_post(Post(id="p_edit", parent_id="c3", account="b", account_id="2", platform=Platform.tiktok,
                      caption="edited", state=PostState.awaiting_approval,
                      edited_at=_z(NOW - timedelta(hours=1))))
    led.save()
    return cfg


def test_source_progress_count_matrix(tmp_path):
    cfg = _seed_progress_matrix(tmp_path)
    prog = source_progress(cfg)
    row = prog["src_1"]
    assert row.clips == 3
    assert row.posts == 6
    assert row.approved == 3
    assert row.rejected == 1
    assert row.edited == 1
    assert row.published == 1


def test_source_progress_field_defs(tmp_path):
    cfg = _seed_progress_matrix(tmp_path)
    row = source_progress(cfg)["src_1"]
    assert isinstance(row, SourceProgress)
    assert row.title  # inbox basename or id
    assert row.state == "moments_decided"
    assert row.bucket in ("actionable", "blocked_on_gates", "recoverable", "inventory")
    assert _APPROVED_STATES == frozenset({
        PostState.queued, PostState.submitting, PostState.submitted,
        PostState.published, PostState.analyzed,
    })


def test_source_progress_torn_ledger_returns_empty(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)

    def boom(_cfg):
        raise OSError("ledger torn")

    monkeypatch.setattr("fanops.studio.views_library.Ledger.load", boom)
    assert source_progress(cfg) == {}


def test_old_ledger_without_edited_at_loads():
    p = Post.model_validate({"id": "p_old", "parent_id": "c0", "account": "a", "account_id": "1",
                             "platform": "instagram", "caption": "x", "state": "queued"})
    assert p.edited_at is None


def test_edit_caption_stamps_edited_at(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    assert edit_caption(cfg, "p_edit", "raw energy, all gas no brakes", now=NOW).ok
    assert Ledger.load(cfg).posts["p_edit"].edited_at == iso_z(NOW)


def test_regenerate_caption_stamps_edited_at(tmp_path, monkeypatch):
    from fanops.models import CaptionSet, CaptionItem
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()

    def _model(_prompt, _schema):
        return CaptionSet(request_id="r", items=[CaptionItem(surface="a/instagram", caption="NEW", hashtags=[])]).model_dump()

    assert regenerate_caption(cfg, "p_edit", "punchier", model=_model, now=NOW).ok
    assert Ledger.load(cfg).posts["p_edit"].edited_at == iso_z(NOW)


def test_reburn_hook_stamps_edited_at(tmp_path, mocker, monkeypatch):
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook="OLD"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(tmp_path / "c.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    (tmp_path / "c.mp4").write_bytes(b"V")
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.awaiting_approval))
    led.save()
    from fanops.models import Clip as ClipModel
    mocker.patch("fanops.clip.render_moment", return_value=(None, ClipModel(
        id="clip_1", parent_id="mom_1", path=str(tmp_path / "c.mp4"), aspect=Fmt.r9x16, state=ClipState.queued)))
    assert reburn_hook(cfg, "p_edit", "NEW HOOK", now=NOW).ok
    assert Ledger.load(cfg).posts["p_edit"].edited_at == iso_z(NOW)
