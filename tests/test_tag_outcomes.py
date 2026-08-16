# tests/test_tag_outcomes.py — F2-I: account-keyed selection outcomes.
# Trainer writes (platform, account, tag) → {n, p50} from PostState.analyzed only.
# vet_hashtags uses that rank when n≥OUTCOME_MIN_N; else size_rank_key. No rollup.
from __future__ import annotations
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from fanops.config import Config
from fanops.hashtags import SIZE_FIELD, vet_hashtags, vet_hashtags_traced
from fanops.models import Platform, PostState
from fanops.tag_outcomes import (
    OUTCOME_MIN_N, TAG_OUTCOMES_NAME, load_tag_outcomes, lookup_outcome,
    refresh_tag_outcomes, tag_outcomes_path,
)


_LEARNING_MODULES = ["track.py", "variant_learning.py", "variant_amplify.py", "variant_transfer.py",
                     "moment_hook_learning.py", "p4_dim_bias.py"]


def _post(*, pid, account, platform=Platform.instagram, state=PostState.analyzed,
          hashtags=("#hiphop",), views=None, reach=None, metrics=None):
    m = dict(metrics or {})
    if views is not None:
        m["views"] = views
    if reach is not None:
        m["reach"] = reach
    return SimpleNamespace(id=pid, account=account, platform=platform, state=state,
                           hashtags=list(hashtags), metrics=m)


class _Led:
    def __init__(self, *posts):
        self.posts = {p.id: p for p in posts}


def _volume_cache(cfg, rows):
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    blob = {}
    for i, (tag, rec) in enumerate(rows.items()):
        blob[tag] = {"graph_id": f"id-{i}", "measured_at": "2026-08-17T00:00:00+00:00",
                     SIZE_FIELD: rec[SIZE_FIELD]}
    cfg.hashtags_path.write_text(json.dumps(blob))


def _write_outcomes(cfg, table):
    p = tag_outcomes_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(table))


def test_outcome_min_n_is_4():
    assert OUTCOME_MIN_N == 4
    assert TAG_OUTCOMES_NAME == "tag_outcomes.json"


def test_refresh_writes_sidecar_under_cfg_control(tmp_path):
    cfg = Config(root=tmp_path)
    posts = [_post(pid=f"m{i}", account="markmakmouly", hashtags=["#hiphop"], views=10 + i)
             for i in range(4)]
    table = refresh_tag_outcomes(cfg, _Led(*posts))
    path = cfg.control / "tag_outcomes.json"
    assert path == tag_outcomes_path(cfg)
    assert path.is_file()
    on_disk = json.loads(path.read_text())
    assert on_disk == table
    row = on_disk["instagram"]["markmakmouly"]["#hiphop"]
    assert row["n"] == 4
    assert row["p50"] == 11.5                         # median of 10,11,12,13


def test_key_is_platform_account_tag_no_platform_tag_rollup(tmp_path):
    cfg = Config(root=tmp_path)
    led = _Led(
        _post(pid="ig-a", account="markmakmouly", platform=Platform.instagram,
              hashtags=["#hiphop"], views=100),
        _post(pid="ig-b", account="hrmny-blog", platform=Platform.instagram,
              hashtags=["#hiphop"], views=0),
        _post(pid="tt-a", account="markmakmouly", platform=Platform.tiktok,
              hashtags=["#hiphop"], views=50),
    )
    table = refresh_tag_outcomes(cfg, led)
    assert set(table) == {"instagram", "tiktok"}
    assert "#hiphop" not in table                      # no (platform, tag) rollup at the top
    assert "markmakmouly" not in table
    assert lookup_outcome(table, Platform.instagram, "markmakmouly", "#hiphop")["p50"] == 100.0
    assert lookup_outcome(table, Platform.instagram, "hrmny-blog", "#hiphop")["p50"] == 0.0
    assert lookup_outcome(table, Platform.tiktok, "markmakmouly", "#HipHop")["p50"] == 50.0
    assert lookup_outcome(table, Platform.instagram, "markmakmouly", "#missing") is None


def test_metric_prefers_numeric_views_else_reach(tmp_path):
    cfg = Config(root=tmp_path)
    led = _Led(
        _post(pid="both", account="a", hashtags=["#x"], metrics={"views": 10, "reach": 999}),
        _post(pid="reach", account="a", hashtags=["#y"], reach=40),
        _post(pid="bad", account="a", hashtags=["#z"], metrics={"views": "nope", "reach": True}),
        _post(pid="neg", account="a", hashtags=["#z"], views=-1),
    )
    table = refresh_tag_outcomes(cfg, led)
    assert table["instagram"]["a"]["#x"]["p50"] == 10.0
    assert table["instagram"]["a"]["#y"]["p50"] == 40.0
    assert "#z" not in table["instagram"]["a"]


def test_trainer_skips_youtube_and_non_analyzed(tmp_path):
    cfg = Config(root=tmp_path)
    led = _Led(
        _post(pid="yt", account="a", platform=Platform.youtube, hashtags=["#hiphop"], views=9999),
        _post(pid="pub", account="a", state=PostState.published, hashtags=["#hiphop"], views=8888),
        _post(pid="ok", account="a", hashtags=["#hiphop"], views=3),
    )
    table = refresh_tag_outcomes(cfg, led)
    assert "youtube" not in table
    assert table["instagram"]["a"]["#hiphop"] == {"n": 1, "p50": 3.0}


def test_refresh_fail_open_never_raises_and_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    assert refresh_tag_outcomes(cfg, None) == {}
    assert refresh_tag_outcomes(cfg, object()) == {}
    led = _Led(*[_post(pid=f"p{i}", account="a", hashtags=["#hiphop"], views=i) for i in range(4)])
    first = refresh_tag_outcomes(cfg, led)
    second = refresh_tag_outcomes(cfg, led)
    assert first == second
    assert json.loads(tag_outcomes_path(cfg).read_text()) == first


def test_load_missing_or_corrupt_is_empty(tmp_path):
    cfg = Config(root=tmp_path)
    assert load_tag_outcomes(cfg) == {}
    assert load_tag_outcomes(None) == {}
    p = tag_outcomes_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{")
    assert load_tag_outcomes(cfg) == {}
    p.write_text(json.dumps(["not", "a", "map"]))
    assert load_tag_outcomes(cfg) == {}


def test_n_below_k_keeps_size_order(tmp_path):
    cfg = Config(root=tmp_path)
    _volume_cache(cfg, {"#alpha": {SIZE_FIELD: 80_000}, "#beta": {SIZE_FIELD: 200_000}})
    store = ["#alpha", "#beta"]
    _write_outcomes(cfg, {"instagram": {"markmakmouly": {
        "#alpha": {"n": 3, "p50": 99_000.0}, "#beta": {"n": 3, "p50": 1.0}}}})
    size = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg)
    sparse = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                          account="markmakmouly")
    assert size == sparse
    assert size.index("#beta") < size.index("#alpha")


def test_n_at_least_k_ranks_by_p50_not_size(tmp_path):
    cfg = Config(root=tmp_path)
    _volume_cache(cfg, {"#alpha": {SIZE_FIELD: 80_000}, "#beta": {SIZE_FIELD: 200_000}})
    store = ["#alpha", "#beta"]
    _write_outcomes(cfg, {"instagram": {"markmakmouly": {
        "#alpha": {"n": 4, "p50": 9_000.0}, "#beta": {"n": 4, "p50": 10.0}}}})
    size = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg)
    own = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                       account="markmakmouly")
    assert size.index("#beta") < size.index("#alpha")
    assert own.index("#alpha") < own.index("#beta")


def test_confound_account_p50_does_not_cross(tmp_path):
    """hrmny-blog zeros cannot reorder markmakmouly. Each menu uses ITS p50."""
    cfg = Config(root=tmp_path)
    _volume_cache(cfg, {"#alpha": {SIZE_FIELD: 80_000}, "#beta": {SIZE_FIELD: 200_000}})
    store = ["#alpha", "#beta"]
    _write_outcomes(cfg, {"instagram": {
        "markmakmouly": {"#alpha": {"n": 4, "p50": 12_000.0}, "#beta": {"n": 4, "p50": 20.0}},
        "hrmny-blog": {"#alpha": {"n": 4, "p50": 0.0}, "#beta": {"n": 4, "p50": 8_000.0}},
    }})
    living = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                          account="markmakmouly")
    dead = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                        account="hrmny-blog")
    none = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg)
    assert living.index("#alpha") < living.index("#beta")
    assert dead.index("#beta") < dead.index("#alpha")
    assert none.index("#beta") < none.index("#alpha")
    assert living != dead


def test_account_none_matches_omitted_kwarg(tmp_path):
    cfg = Config(root=tmp_path)
    _volume_cache(cfg, {"#alpha": {SIZE_FIELD: 80_000}, "#beta": {SIZE_FIELD: 200_000}})
    store = ["#alpha", "#beta"]
    _write_outcomes(cfg, {"instagram": {"markmakmouly": {
        "#alpha": {"n": 4, "p50": 9_000.0}, "#beta": {"n": 4, "p50": 1.0}}}})
    a = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg)
    b = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg, account=None)
    c = vet_hashtags_traced(store, Platform.instagram, "en", store=store, cfg=cfg)[0]
    assert a == b == c


def test_refresh_then_vet_uses_trained_p50(tmp_path):
    cfg = Config(root=tmp_path)
    _volume_cache(cfg, {"#alpha": {SIZE_FIELD: 80_000}, "#beta": {SIZE_FIELD: 200_000}})
    living = [_post(pid=f"l{i}", account="markmakmouly", hashtags=["#alpha"], views=1_000)
              for i in range(4)]
    living += [_post(pid=f"lb{i}", account="markmakmouly", hashtags=["#beta"], views=1)
               for i in range(4)]
    dead = [_post(pid=f"d{i}", account="hrmny-blog", hashtags=["#alpha"], views=0)
            for i in range(4)]
    dead += [_post(pid=f"db{i}", account="hrmny-blog", hashtags=["#beta"], views=500)
             for i in range(4)]
    refresh_tag_outcomes(cfg, _Led(*living, *dead))
    store = ["#alpha", "#beta"]
    living_menu = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                               account="markmakmouly")
    dead_menu = vet_hashtags(store, Platform.instagram, "en", store=store, cfg=cfg,
                             account="hrmny-blog")
    assert living_menu.index("#alpha") < living_menu.index("#beta")
    assert dead_menu.index("#beta") < dead_menu.index("#alpha")


def test_request_captions_calls_refresh():
    from fanops.caption import request_captions
    src = inspect.getsource(request_captions)
    assert "refresh_tag_outcomes" in src


def test_ingest_and_regen_pass_account():
    from fanops.caption import ingest_captions
    from fanops.studio.actions import regenerate_caption
    assert "account=handle" in inspect.getsource(ingest_captions)
    assert "account=p.account" in inspect.getsource(regenerate_caption)
    assert "account" in inspect.getsource(vet_hashtags)
    assert "account" in inspect.getsource(vet_hashtags_traced)


def test_selection_may_read_outcomes_learning_modules_stay_blind():
    """EXCEPTION named: selection may read outcomes. lift_score / _LEARNING_MODULES must not."""
    from fanops.track import _W, lift_score
    src = Path(__file__).resolve().parents[1] / "src" / "fanops"
    trainer = (src / "tag_outcomes.py").read_text()
    assert "tag_outcomes" not in _LEARNING_MODULES
    assert trainer.count("PostState.analyzed")
    assert ".hashtags" in trainer or "hashtags" in trainer
    assert "lift_score" not in trainer
    assert "tag_reach_means" not in trainer
    assert "rank_tags_by_reach" not in trainer
    for name in ("track", "variant_learning", "variant_amplify", "variant_transfer",
                 "moment_hook_learning", "p4_dim_bias"):
        assert f"fanops.{name}" not in trainer
        assert f"import {name}" not in trainer
    for name in _LEARNING_MODULES:
        text = (src / name).read_text()
        assert "tag_outcomes" not in text, f"{name} imported the selection trainer"
        assert "tag_reach_means" not in text
        assert "rank_tags_by_reach" not in text
        assert ".hashtags" not in text, f"{name} reads a post's hashtags — attribution leak"
    metrics = {"saves": 10, "shares": 5, "retention": 0.4, "reach": 9000, "likes": 100}
    assert lift_score({**metrics, "hashtags": ["#viral"]}) == lift_score(metrics)
    assert not any("hashtag" in k or k == "tags" for k in _W)
