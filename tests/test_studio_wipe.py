# tests/test_studio_wipe.py — ledger-rebuild M4 (MOL-33): the Studio operator surface for the wipe.
# A destructive action behind a TYPED CONFIRM (mirrors Go-Live), with a read-only PREVIEW (the would-remove
# id-set + per-entity counts) rendered BEFORE the confirm. Snapshot-first + verified-restorable (MOL-32) is
# enforced in the action. tmp-path fixtures ONLY — nothing runs against live 00_control.
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, LIFT_SCORE)
from fanops.studio import actions_wipe


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _seed(cfg):
    # a kept analyzed post + a never-shipped awaiting post off a different moment (the unbacked one).
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        led.add_moment(Moment(id="mk", parent_id="s1", content_token="K", start=0, end=2, reason="k"))
        led.add_clip(Clip(id="ck", parent_id="mk", path="/ck.mp4", state=ClipState.analyzed))
        led.add_post(Post(id="pk", parent_id="ck", account="@a", account_id="1", platform=Platform.instagram,
                          caption="kept", state=PostState.analyzed, public_url="https://ig/reel/k/",
                          metrics={LIFT_SCORE: 0.5}))
        led.add_moment(Moment(id="md", parent_id="s1", content_token="D", start=3, end=5, reason="d"))
        led.add_clip(Clip(id="cd", parent_id="md", path="/cd.mp4", state=ClipState.rendered))
        led.add_post(Post(id="pd", parent_id="cd", account="@a", account_id="1", platform=Platform.instagram,
                          caption="never", state=PostState.awaiting_approval, public_url="dryrun://pd"))


# ---- action: preview ----
def test_preview_reports_would_remove_without_touching_ledger(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    before = cfg.ledger_path.read_bytes()
    res = actions_wipe.preview_wipe(cfg)
    assert res.ok
    assert res.detail["counts"]["posts"] == 1
    assert "pd" in res.detail["post_ids"] and "pk" not in res.detail["post_ids"]
    assert res.detail["kept_posts"] == 1
    assert cfg.ledger_path.read_bytes() == before        # PREVIEW never mutates the ledger


# ---- action: typed confirm gate ----
def test_confirm_wipe_refuses_wrong_typed_word(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_wipe.confirm_wipe(cfg, typed="delete please")
    assert not res.ok and "pd" in Ledger.load(cfg).posts   # nothing removed on a wrong confirm word


def test_confirm_wipe_refuses_empty_typed(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_wipe.confirm_wipe(cfg, typed="")
    assert not res.ok and "pd" in Ledger.load(cfg).posts


def test_confirm_wipe_executes_on_exact_word_and_snapshots(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_wipe.preview_wipe(cfg).detail["token"]   # MOL-71: preview-ran gate supplies the token
    res = actions_wipe.confirm_wipe(cfg, typed=actions_wipe.CONFIRM_WORD, token=token)
    assert res.ok
    led = Ledger.load(cfg)
    assert "pd" not in led.posts and "md" not in led.moments   # unbacked removed
    assert "pk" in led.posts and "mk" in led.moments and "s1" in led.sources   # kept history intact
    # a snapshot was taken first and is reported (rollback point)
    snap = res.detail.get("snapshot")
    assert snap and __import__("pathlib").Path(snap).exists()


def test_confirm_wipe_is_reversible_via_snapshot(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_wipe.preview_wipe(cfg).detail["token"]   # MOL-71: preview-ran gate supplies the token
    res = actions_wipe.confirm_wipe(cfg, typed=actions_wipe.CONFIRM_WORD, token=token)
    assert "pd" not in Ledger.load(cfg).posts
    Ledger.restore_snapshot(cfg, res.detail["snapshot"])
    assert "pd" in Ledger.load(cfg).posts                 # the operator can restore


# ---- routes ----
def test_wipe_preview_route_shows_counts_and_ids(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).post("/live-library/wipe/preview")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "pd" in body                                   # the would-remove id is shown
    # a typed-confirm input appears only AFTER the preview (the destructive step is gated behind the preview)
    assert actions_wipe.CONFIRM_WORD in body


def test_wipe_confirm_route_requires_typed_word(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).post("/live-library/wipe/confirm", data={"confirm_text": "nope"})
    assert r.status_code == 200
    assert "pd" in Ledger.load(cfg).posts                 # wrong word -> no removal


def test_wipe_confirm_route_executes_on_word(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_wipe.preview_wipe(cfg).detail["token"]   # MOL-71: confirm carries the preview token
    r = _client(cfg).post("/live-library/wipe/confirm",
                          data={"confirm_text": actions_wipe.CONFIRM_WORD, "preview_token": token})
    assert r.status_code == 200
    assert "pd" not in Ledger.load(cfg).posts and "pk" in Ledger.load(cfg).posts


def test_wipe_preview_empty_when_all_backed(tmp_path):
    # a ledger with ONLY analyzed history -> nothing to remove -> preview says so, confirm is a no-op.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        led.add_moment(Moment(id="mk", parent_id="s1", content_token="K", start=0, end=2, reason="k"))
        led.add_clip(Clip(id="ck", parent_id="mk", path="/ck.mp4", state=ClipState.analyzed))
        led.add_post(Post(id="pk", parent_id="ck", account="@a", account_id="1", platform=Platform.instagram,
                          caption="kept", state=PostState.analyzed, public_url="https://ig/reel/k/",
                          metrics={LIFT_SCORE: 0.5}))
    res = actions_wipe.preview_wipe(cfg)
    assert res.ok and res.detail["total"] == 0


# ---- MOL-71: server-enforced preview-before-confirm (the preview token gate) ----
def test_preview_returns_a_token_for_the_would_remove_set(tmp_path):
    # the preview carries a token fingerprinting exactly the would-remove set — the confirm step validates it.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_wipe.preview_wipe(cfg)
    assert res.ok and res.detail.get("token")             # a non-empty token is present in the preview


def test_confirm_refuses_missing_token_even_with_right_word(tmp_path):
    # a direct/replayed POST to confirm (no preview ever ran -> no token) is REFUSED before any snapshot/removal.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions_wipe.confirm_wipe(cfg, typed=actions_wipe.CONFIRM_WORD, token="")
    assert not res.ok and "preview" in res.error.lower()  # explicit "run the preview" error
    assert "pd" in Ledger.load(cfg).posts                 # nothing removed


def test_confirm_refuses_stale_token_after_ledger_changed(tmp_path):
    # a token from a preview whose would-remove set no longer matches the ledger is REFUSED (stale preview).
    cfg = Config(root=tmp_path); _seed(cfg)
    stale = actions_wipe.preview_wipe(cfg).detail["token"]
    # the ledger changes under the operator: the unbacked post gets shipped/analyzed history (now KEPT).
    with Ledger.transaction(cfg) as led:
        led.posts["pd"] = led.posts["pd"].model_copy(update={"state": PostState.analyzed, "metrics": {LIFT_SCORE: 0.9}})
    res = actions_wipe.confirm_wipe(cfg, typed=actions_wipe.CONFIRM_WORD, token=stale)
    assert not res.ok and "stale" in res.error.lower()    # the mismatch is reported as stale
    assert "pd" in Ledger.load(cfg).posts                 # nothing removed on a stale token


def test_confirm_executes_with_a_fresh_matching_token(tmp_path):
    # preview -> confirm with the token it returned -> the wipe proceeds exactly as today.
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_wipe.preview_wipe(cfg).detail["token"]
    res = actions_wipe.confirm_wipe(cfg, typed=actions_wipe.CONFIRM_WORD, token=token)
    assert res.ok
    led = Ledger.load(cfg)
    assert "pd" not in led.posts and "pk" in led.posts    # unbacked removed, history kept


def test_confirm_route_carries_token_end_to_end(tmp_path):
    # the route path: preview render exposes the token; confirm with it proceeds, without it refuses.
    cfg = Config(root=tmp_path); _seed(cfg)
    token = actions_wipe.preview_wipe(cfg).detail["token"]
    client = _client(cfg)
    r_bad = client.post("/live-library/wipe/confirm", data={"confirm_text": actions_wipe.CONFIRM_WORD})
    assert r_bad.status_code == 200 and "pd" in Ledger.load(cfg).posts   # no token -> refused, no removal
    r_ok = client.post("/live-library/wipe/confirm",
                       data={"confirm_text": actions_wipe.CONFIRM_WORD, "preview_token": token})
    assert r_ok.status_code == 200 and "pd" not in Ledger.load(cfg).posts   # token -> proceeds
