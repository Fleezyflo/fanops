# tests/test_validation_gate.py — Phase 2: the OFF-until-proven gate. learning_validated(cfg) is
# True once cutover.json metrics_confirmed is set — manually OR auto-stamped on the first live
# shape-proven analyzed metric (track._auto_validate_metrics_shape).
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.validation_gate import learning_validated
from fanops.track import pull_metrics, _shape_proves_learning
from fanops import cutover


def test_unvalidated_without_cutover_file(tmp_path):
    assert learning_validated(Config(root=tmp_path)) is False

def test_validated_after_metrics_confirmed(tmp_path):
    cfg = Config(root=tmp_path)
    cutover._save_state(cfg, {"metrics_confirmed": True})
    assert learning_validated(cfg) is True

def test_unvalidated_when_only_posted_not_metrics(tmp_path):
    cfg = Config(root=tmp_path)
    cutover._save_state(cfg, {"submission_id": "s1"})    # posted, but metrics not yet confirmed
    assert learning_validated(cfg) is False

def test_unvalidated_on_corrupt_cutover(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.cutover_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cutover_path.write_text("{not json")
    assert learning_validated(cfg) is False


def test_postiz_shaped_live_metrics_auto_validate_learning(tmp_path, monkeypatch):
    # Postiz delivers shares/reach/likes but NEVER retention — rows stay lift_degraded yet the live
    # shape is proven once reach + a primary engagement key reconcile (learn_doctor gates on reach).
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="sub1", public_url="https://x"))
    assert learning_validated(cfg) is False
    postiz = {"shares": 30, "reach": 50000, "likes": 200}   # no saves/retention — Postiz-shaped
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub1", "metrics": postiz}])
    assert led.posts["p1"].metrics.get("lift_degraded") is True   # honest partial objective
    assert learning_validated(cfg) is True                        # shape proven -> auto-stamp

def test_shape_proves_learning_postiz_row_not_full_primary_set():
    # MOL-18c re-scope: this is now a PLATFORM-LESS row (no platform arg). It proves as today, AND stays
    # proving even with the IG-retention flag ON — the tightening is fail-open for a platform-less row.
    m = {"lift_score": 1.0, "lift_degraded": True, "lift_missing_keys": ["retention", "saves"],
         "shares": 30, "reach": 50000, "likes": 200}
    assert _shape_proves_learning(m) is True
    assert _shape_proves_learning(m, require_ig_retention=True) is True   # platform-less -> flag can't tighten it

def test_shape_proves_learning_rejects_reach_only_noise():
    assert _shape_proves_learning({"lift_score": 1.0, "likes": 3, "reach": 1000}) is False

def test_shape_proves_learning_rejects_present_but_null_primary():
    assert _shape_proves_learning({"lift_score": 1.0, "saves": None, "shares": 12, "retention": 0.7,
                                   "reach": 1000}) is False

def test_learning_validated_after_postiz_cutover(tmp_path, monkeypatch):
    # M3: the SINGLE freeze flag flips on the Postiz path too — _postiz_metrics writes metrics_confirmed,
    # which learning_validated already reads. No parallel "postiz_validated" flag (one flag, two writers).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    rows = [{"postSubmissionId": "pz1", "metrics": {"likes": 5}, "_raw_labels": ["Likes"]}]
    cutover.cutover_metrics(cfg, "pz1", list_posts=lambda w: rows)
    assert learning_validated(cfg) is True


# ---- Platform-aware learning proof (MOL-16/17/18, capability model) ----
from fanops.track import _shape_proves_learning as _P, _missing_high_weight, _PLATFORM_METRICS, record_metrics

def test_capability_map_retention_only_available_on_ig():
    # MOL-16: the ONE capability source — retention is IG-only (Meta Graph avg-watch); every non-IG
    # Platform (TikTok/youtube/facebook/twitter via Zernio/Postiz) has NO retention key. Guards against a
    # future "not TikTok" shortcut silently re-including youtube (a third Platform with retention absent).
    assert "retention" in _PLATFORM_METRICS[Platform.instagram]
    for pf in (Platform.tiktok, Platform.youtube, Platform.facebook, Platform.twitter):
        assert "retention" not in _PLATFORM_METRICS[pf]

def test_tiktok_reach_plus_saves_still_proves_untouched():
    # The 6a7323f shape-heuristic (reach + saves|shares, no retention) still PROVES for TikTok — pinned
    # here so the capability model never regresses the already-shipped unfreeze path.
    assert _P({"lift_score": 1.0, "reach": 5000, "saves": 40}, platform=Platform.tiktok) is True
    assert _P({"lift_score": 1.0, "reach": 5000, "shares": 12}, platform=Platform.tiktok) is True

def test_tiktok_residual_shape_without_saves_or_reach_still_fails():
    # MOL-18a: shares/views/likes with NO saves AND NO reach is genuinely under-proven — TikTok CAN
    # deliver saves+reach (both in its capability set), so a row lacking both clears no primary+reach
    # floor. The capability model's verdict is FAIL; assert it stays False (a characterization pin).
    assert _P({"lift_score": 1.0, "shares": 12, "views": 9000, "likes": 30}, platform=Platform.tiktok) is False

def test_tiktok_row_not_marked_retention_degraded(tmp_path):
    # MOL-18b: a TikTok row is NOT permanently lift_degraded for `retention` — a metric TikTok can never
    # emit. saves/reach present -> the row is a full primary set FOR TIKTOK -> no degraded marker at all.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="tk1", parent_id="c", account="@t", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.published, public_url="dryrun://tk1"))
    record_metrics(led, "tk1", {"reach": 5000, "saves": 40, "shares": 12})
    m = led.posts["tk1"].metrics
    assert "lift_degraded" not in m and "lift_missing_keys" not in m   # retention is not a TikTok gap

def test_tiktok_missing_key_list_excludes_retention_but_keeps_saves(tmp_path):
    # MOL-18b (finer): a TikTok row missing saves IS degraded on saves (TikTok delivers saves), but
    # retention NEVER appears in lift_missing_keys for TikTok even though _W weights it primary.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="tk2", parent_id="c", account="@t", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.published, public_url="dryrun://tk2"))
    record_metrics(led, "tk2", {"reach": 5000, "shares": 12, "likes": 3})   # no saves
    m = led.posts["tk2"].metrics
    assert m["lift_missing_keys"] == ["saves"]                 # saves listed, retention NOT (platform can't emit it)

def test_youtube_treated_like_tiktok_not_held_to_retention():
    # MOL-16 corollary: youtube publishes via Postiz with retention unavailable — a "not TikTok" check
    # would wrongly require retention here. reach+saves proves; retention is never a youtube gap.
    assert _P({"lift_score": 1.0, "reach": 5000, "saves": 40}, platform=Platform.youtube) is True
    assert _missing_high_weight({"reach": 5000, "saves": 40, "shares": 12}, None, Platform.youtube) == []

def test_ig_retention_flag_off_ig_proves_without_retention():
    # MOL-18c default-OFF: with the flag off (default), an IG row (reach + a primary engagement key, no
    # retention) proves exactly as the shipped behavior — the tightening is opt-in, never the default.
    m = {"lift_score": 1.0, "reach": 50000, "saves": 40, "shares": 12}
    assert _P(m, platform=Platform.instagram) is True                     # default: flag off -> proves
    assert _P(m, platform=Platform.instagram, require_ig_retention=False) is True

def test_ig_retention_flag_on_ig_without_retention_does_not_prove():
    # MOL-18c ON: with the flag ON, an IG row (retention-capable) that lacks a present-numeric retention
    # does NOT prove — the IG shape is held to include retention. This is NEW behavior behind the flag.
    m = {"lift_score": 1.0, "reach": 50000, "saves": 40, "shares": 12}          # no retention
    assert _P(m, platform=Platform.instagram, require_ig_retention=True) is False

def test_ig_retention_flag_on_ig_with_retention_proves():
    # MOL-18c ON, satisfied: an IG row WITH retention proves even under the flag.
    m = {"lift_score": 1.0, "reach": 50000, "saves": 40, "shares": 12, "retention": 0.62}
    assert _P(m, platform=Platform.instagram, require_ig_retention=True) is True

def test_ig_retention_flag_on_does_not_tighten_tiktok():
    # MOL-18c fail-open: the flag ON must NOT freeze TikTok — TikTok can't deliver retention, so the
    # gate is skipped for it and reach+saves still proves.
    assert _P({"lift_score": 1.0, "reach": 5000, "saves": 40}, platform=Platform.tiktok,
              require_ig_retention=True) is True

def test_reach_only_noise_fails_on_every_platform_even_with_flags():
    # MOL-18d: reach+likes noise (no saves/shares) proves NOWHERE — capability-independent floor.
    for pf in (Platform.instagram, Platform.tiktok, Platform.youtube, None):
        assert _P({"lift_score": 1.0, "reach": 1000, "likes": 3}, platform=pf) is False
        assert _P({"lift_score": 1.0, "reach": 1000, "likes": 3}, platform=pf,
                  require_ig_retention=True) is False

def test_present_but_null_primary_fails_closed_with_platform(tmp_path, monkeypatch):
    # MOL-18d / D1: a present-but-null primary still fails closed WITH a platform threaded — the null
    # guard runs before any capability widening, so a null saves never auto-unfreezes (even on TikTok).
    from fanops.validation_gate import learning_validated
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="tk3", parent_id="c", account="@t", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.published, public_url="https://x", submission_id="s3"))
    record_metrics(led, "tk3", {"saves": None, "shares": 12, "reach": 1000})
    from fanops.track import _auto_validate_metrics_shape
    _auto_validate_metrics_shape(led, cfg)
    assert learning_validated(cfg) is False                   # null saves is unproven regardless of platform

def test_tiktok_only_account_auto_unfreezes_on_delivered_signals(tmp_path, monkeypatch):
    # The hypothesis end-to-end: a TikTok-only account unfreezes learning on reach + saves (no retention)
    # via the LIVE pull path — the platform-aware proof lets the shape prove on what TikTok delivers.
    from fanops.track import pull_metrics
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="tk4", parent_id="c1", account="@t", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.published, submission_id="sub4", public_url="https://x"))
    assert learning_validated(cfg) is False
    zernio = {"reach": 5000, "saves": 40, "shares": 12}       # Zernio-shaped, no retention
    pull_metrics(led, cfg, list_posts=lambda w: [{"postSubmissionId": "sub4", "metrics": zernio}])
    assert learning_validated(cfg) is True                    # proved on TikTok-delivered signals
