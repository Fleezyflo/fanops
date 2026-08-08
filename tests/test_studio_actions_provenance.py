# tests/test_studio_actions_provenance.py — MOL-756: the Studio action layer over the origin backfill.
# The engine's refusals are pinned in tests/test_origin_backfill.py; what this file proves is that the
# ACTION surfaces them as a readable operator refusal (with the measured values still attached) instead of
# a 500 or a silent no-op, and that a confirm which never planned is refused before the engine is reached.
# tmp-path fixtures ONLY.
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Moment, MomentOrigin, Platform, Post, PostState, Source)
from fanops.studio import actions_provenance

IN = ["2026-07-29", "2026-07-30"]


def _seed(cfg, *, extra=None):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        for tag, day in (("A", IN[0]), ("B", IN[1]), ("Z", "2026-07-13")):
            led.add_moment(Moment(id=f"m{tag}", parent_id="s1", content_token=tag, start=0, end=2, reason="r"))
            led.add_clip(Clip(id=f"c{tag}", parent_id=f"m{tag}", path=f"/{tag}.mp4", state=ClipState.queued))
            led.add_post(Post(id=f"p{tag}", parent_id=f"c{tag}", account="a", account_id="1",
                              platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                              created_at=f"{day}T10:00:00Z"))
        if extra:
            extra(led)


def test_plan_is_read_only_and_carries_the_histogram_and_the_corpus_count(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    before = cfg.ledger_path.read_bytes()
    res = actions_provenance.preview_backfill(cfg, days=IN)
    assert res.ok and res.detail["moments"] == 2 and res.detail["unlabelled"] == 2
    assert res.detail["day_histogram"] == {"2026-07-13": 1, IN[0]: 1, IN[1]: 1}
    assert res.detail["corpus_unlabelled"] == 3 and res.detail["corpus_moments"] == 3
    assert cfg.ledger_path.read_bytes() == before


def test_plan_with_no_day_still_reports_the_ledger(tmp_path):
    # This is the Home entry state: nothing selected, but the operator must still SEE that provenance is
    # missing and on which days posts exist. It must not look like an error.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_provenance.preview_backfill(cfg, days=[])
    assert res.ok and res.detail["moments"] == 0 and res.detail["corpus_unlabelled"] == 3
    assert len(res.detail["day_histogram"]) == 3


def test_confirm_without_a_token_refuses_before_touching_anything(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_provenance.confirm_backfill(cfg, days=IN, token="")
    assert not res.ok and "show the plan first" in res.error
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown
    assert not list(cfg.control.glob("ledger.snapshot.*"))     # a refusal that never planned costs no snapshot


def test_confirm_with_a_stale_token_refuses(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_provenance.confirm_backfill(cfg, days=IN, token="NOPLAN")
    assert not res.ok and "stale" in res.error
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_confirm_with_a_matching_token_applies_and_reports_the_snapshot(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_provenance.preview_backfill(cfg, days=IN).detail["plan_token"]
    res = actions_provenance.confirm_backfill(cfg, days=IN, token=token)
    assert res.ok and res.detail["labelled"] == 2 and res.detail["snapshot"]
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.machine_inferred
    assert Ledger.load(cfg).moments["mZ"].origin is MomentOrigin.unknown


def test_a_broken_invariant_becomes_a_readable_refusal_not_an_exception(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, extra=lambda led: led.add_post(Post(id="pA2", parent_id="cA", account="b", account_id="2",
                                                   platform=Platform.tiktok, caption="x",
                                                   state=PostState.awaiting_approval,
                                                   created_at=f"{IN[0]}T12:00:00Z")))
    token = actions_provenance.preview_backfill(cfg, days=IN).detail["plan_token"]
    res = actions_provenance.confirm_backfill(cfg, days=IN, token=token)
    assert not res.ok and "refused:" in res.error and "nothing was labelled" in res.error
    assert res.detail["stops"]                                  # the measured values ride along for the panel
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_an_unreadable_ledger_is_a_clean_refusal(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    cfg.ledger_path.write_bytes(b"not a sqlite database at all")
    res = actions_provenance.preview_backfill(cfg, days=IN)
    assert not res.ok and "ledger unreadable" in res.error
