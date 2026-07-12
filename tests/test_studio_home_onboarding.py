# tests/test_studio_home_onboarding.py — S10: Home tells ONE "what needs you now" story. The workflow spine
# (the bar above) is the SOLE next-action CTA; the "Get started" panel is now pure ORIENTATION shown only at
# the TRUE zero-state (no accounts AND no footage) and carries NO competing done/next/todo ladder (no
# data-state="next"). Once the operator has either accounts or footage, the spine alone guides them. The
# zero-result batch warning is promoted to a list-level flag, and each active account's post count renders
# inline (the #home-metrics table is now only the orphan fallback for handles with no active account).
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Post, Platform, PostState, Clip, ClipState


def _accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _active(handle="a"):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active"}


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


# ── ONE next-action: the spine, not a competing onboarding ladder ──────────────────────────────────
def test_true_zero_state_shows_orientation_only(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [])           # no accounts, no footage → truly new
    html = _client(cfg).get("/").data.decode()
    assert "Get started" in html                              # orientation is shown
    assert 'data-state="next"' not in html                    # but it carries NO competing next-ladder
    assert html.count("spine-next-kicker") == 1               # the spine's CTA is the SOLE next-action


def test_accounts_no_footage_hides_onboarding_keeps_spine(tmp_path):
    # S10 change: the onboarding guard narrowed OR→AND, so having accounts (even with no footage) hides the
    # orientation panel; the spine still points to the next step (no double-message).
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/").data.decode()
    assert "Get started" not in html
    assert "spine-next-kicker" in html                        # the single next-action remains


def test_footage_no_accounts_also_hides_onboarding(tmp_path):
    # the symmetric case: footage but no accounts is no longer "true zero" -> spine-only.
    cfg = Config(root=tmp_path); _accounts(cfg, [])
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4", origin_kind="native"))
    html = _client(cfg).get("/").data.decode()
    assert "Get started" not in html
    assert "spine-next-kicker" in html


def test_fully_setup_hides_get_started(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4", origin_kind="native"))
    html = _client(cfg).get("/").data.decode()
    assert "Get started" not in html                          # established Home stays lean


# ── zero-result batch warnings removed from Home (live on Review ?batch=) ────────────────────────
def test_zero_result_batch_not_on_home(tmp_path):
    from fanops.batches import create_batch
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    led = Ledger.load(cfg)
    ghost = create_batch(led, name="Ghost", target_accounts=["ghost"], now_iso="2026-06-22T00:00:00.000001Z")
    led.add_source(Source(id="s_ghost", source_path="/v.mp4", batch_id=ghost.id)); led.save()
    html = _client(cfg).get("/").data.decode()
    assert 'data-warn="zero-result-summary"' not in html
    assert 'data-warn="zero-result"' not in html


def test_emptied_batch_not_on_home(tmp_path):
    from fanops.batches import create_batch
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    led = Ledger.load(cfg)
    create_batch(led, name="Shell", target_accounts=["ghost"], now_iso="2026-06-22T00:00:00.000001Z"); led.save()
    html = _client(cfg).get("/").data.decode()
    assert 'data-warn="zero-result-summary"' not in html


def test_no_zero_result_summary_when_all_batches_match(tmp_path):
    from fanops.batches import create_batch
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    led = Ledger.load(cfg)
    b = create_batch(led, name="Real", target_accounts=["a"], now_iso="2026-06-22T00:00:00.000003Z")
    led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.queued))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, batch_id=b.id, public_url="dryrun://p")); led.save()
    html = _client(cfg).get("/").data.decode()
    assert 'data-warn="zero-result-summary"' not in html


# ── batches disclosure removed from Home ─────────────────────────────────────────────────────────
def test_batches_disclosure_not_on_home(tmp_path):
    from fanops.batches import create_batch
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    led = Ledger.load(cfg)
    create_batch(led, name="Real", target_accounts=["a"], now_iso="2026-06-22T00:00:00.000009Z"); led.save()
    html = _client(cfg).get("/").data.decode()
    assert "home-batches" not in html and "<summary>Batches" not in html


# ── per-account posted total on account tiles ──────────────────────────────────────────────────────
def test_inline_per_account_post_count(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.published, public_url="dryrun://p1"))
    html = _client(cfg).get("/").data.decode()
    assert "home-acct-tile" in html and "1 posted" in html


def test_orphan_handle_not_on_home(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c", account="ghost", account_id="9", platform=Platform.instagram,
                          caption="x", state=PostState.published, public_url="dryrun://p1"))
    html = _client(cfg).get("/").data.decode()
    assert 'data-metric="by-account"' not in html


# ── the first page an operator sees must never 500 ─────────────────────────────────────────────────
def test_home_torn_ledger_still_200(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    def _boom(c): raise RuntimeError("torn")
    monkeypatch.setattr(Ledger, "load", _boom)
    r = _client(cfg).get("/")
    assert r.status_code == 200                                # fail-open: zeroed shell, never a 500


# ── U3: Home account tiles distinguish awaiting badge from clear accounts ─────────────────────────
def _b(handle="b"):
    return {"handle": handle, "account_id": "2", "platforms": ["instagram"], "status": "active"}

def _future_iso():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def test_clear_account_tile_has_no_badge(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/").data.decode()
    assert "home-acct-tile" in html and "home-acct-badge" not in html


def test_review_awaiting_shows_tile_badge(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p1"))
    html = _client(cfg).get("/").data.decode()
    assert 'class="home-acct-badge">1</span>' in html
    assert "/review?account=a" in html


def test_scheduled_post_appears_on_calendar_not_tile_badge(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.queued, scheduled_time=_future_iso(), public_url="dryrun://p1"))
    html = _client(cfg).get("/").data.decode()
    assert "home-cal-chip" in html
    assert 'class="home-acct-badge"' not in html


def test_zero_count_row_carries_no_pending_cue(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_active()])
    html = _client(cfg).get("/").data.decode()
    assert "home-acct-badge" not in html
