# tests/test_moments_segments.py — S2 supercut consumer forks + authoring (MOL-177)
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, MomentState, MomentPick)
from fanops.agentstep import request_path
from fanops.moments import (_drop_overlaps, _token, _content_token, _window_frames, request_moment_hooks)
from fanops.prompts import moment_pick_prompt
from fanops.studio import actions
from tests.test_moments import _ingest_picks, _src, request_moments

def _mp(s, e, reason="r", **kw):
    return MomentPick(start=s, end=e, reason=reason, **kw)

def test_dedup_keeps_single_window_pick_in_supercut_gap():
    supercut = _mp(15, 50, "supercut", segments=[(15, 25), (35, 50)])
    single = _mp(30, 40, "gap pick")
    kept = _drop_overlaps([supercut, single])
    assert len(kept) == 2
    assert {(p.start, p.end) for p in kept} == {(15, 50), (30, 40)}

def test_dedup_empty_segments_coerces_to_envelope():
    a, b = _mp(0, 18), _mp(5, 20)
    kept = _drop_overlaps([a, b])
    assert len(kept) == 1 and kept[0].start == 0

def test_hook_frames_distributed_across_spans_total_budget(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    calls: list[tuple[float, float, int]] = []
    def _fake_kf(video, start, end, *, count, out_dir, **kw):
        calls.append((start, end, count)); return [f"{start:.0f}-{end:.0f}-{count}"]
    mocker.patch("fanops.moments.extract_keyframes", side_effect=_fake_kf)
    src = Source(id="s1", source_path=str(tmp_path / "v.mp4"), duration=60.0)
    (tmp_path / "v.mp4").write_bytes(b"x")
    spans = [(10.0, 20.0), (30.0, 45.0), (50.0, 55.0)]
    frames = _window_frames(cfg, src, 10.0, 55.0, segments=spans)
    assert 1 <= len(frames) <= 3
    assert sum(c for _, _, c in calls) <= 3
    assert all(s >= 10 and e <= 55 for s, e, _ in calls)
    assert not any(s >= 20 and e <= 30 for s, e, _ in calls)

def test_hook_frames_single_window_unchanged(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.moments.extract_keyframes", return_value=["a", "b", "c"])
    src = Source(id="s1", source_path=str(tmp_path / "v.mp4"), duration=60.0)
    (tmp_path / "v.mp4").write_bytes(b"x")
    assert _window_frames(cfg, src, 14.0, 28.0) == ["a", "b", "c"]
    assert _window_frames(cfg, src, 14.0, 28.0, segments=None) == ["a", "b", "c"]

def test_hook_peaks_scoped_to_segments(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _src(led, cfg, dur=60.0)
    led.add_moment(Moment(id="m1", parent_id="src_1", content_token="15.00-50.00", start=15, end=50,
                          reason="sc", state=MomentState.picked, segments=[(15, 25), (35, 50)]))
    led.sources["src_1"].signal_peaks = [{"t": 18.0, "kind": "beat"}, {"t": 32.0, "kind": "gap_peak"},
                                         {"t": 40.0, "kind": "in_span"}]
    led = request_moment_hooks(led, cfg, "src_1", accounts=None)
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.15.00-50.00").read_text())
    pts = {p["t"] for p in req["signal_peaks"]}
    assert 32.0 not in pts and 18.0 in pts and 40.0 in pts

def test_pick_prompt_offers_segments_rule():
    p = moment_pick_prompt({"duration": 60.0, "transcript": [], "signal_peaks": [], "language": "en", "guidance": ""})
    assert "segments" in p.lower() and "non-overlapping" in p.lower()

def test_ingest_carries_segments(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(15, 50, "supercut", segments=[(15, 25), (35, 50)])])
    assert led.moments_of("src_1")[0].segments == [(15, 25), (35, 50)]

def test_segment_token_distinct():
    a = _token(_mp(15, 50, "a", segments=[(15, 25), (35, 50)]))
    b = _token(_mp(15, 50, "b", segments=[(15, 30), (40, 50)]))
    bare = _content_token(15, 50, [])
    assert a != b and a != bare and "\x1f" in a

def test_single_window_token_and_frames_unchanged():
    assert _token(_mp(14.0, 18.5)) == "14.00-18.50"

def _seed_moment(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/v.mp4", duration=60.0))
    led.add_moment(Moment(id="m0", parent_id="s", content_token="10.00-20.00", start=10, end=20,
                          reason="r", state=MomentState.picked))
    led.save()

def test_operator_set_segments_validates(tmp_path):
    cfg = Config(root=tmp_path); _seed_moment(cfg)
    res = actions.set_segments(cfg, "s", "m0", [(10, 15), (18, 20)])
    assert res.ok
    m = Ledger.load(cfg).moments["m0"]
    assert m.segments == [(10, 15), (18, 20)] and m.start == 10 and m.end == 20

def test_operator_clear_segments_reverts(tmp_path):
    cfg = Config(root=tmp_path); _seed_moment(cfg)
    actions.set_segments(cfg, "s", "m0", [(10, 15), (18, 20)])
    res = actions.clear_segments(cfg, "s", "m0")
    assert res.ok
    m = Ledger.load(cfg).moments["m0"]
    assert m.segments == [] and m.content_token == "10.00-20.00"

def test_operator_set_segments_rejects_foreign_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed_moment(cfg)
    assert actions.set_segments(cfg, "other", "m0", [(10, 15)]).ok is False
    assert actions.set_segments(cfg, "s", "nope", [(10, 15)]).ok is False
