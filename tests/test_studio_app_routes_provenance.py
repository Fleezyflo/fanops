# tests/test_studio_app_routes_provenance.py — MOL-756: the operator-triggered origin backfill, end to end
# through the Studio routes. (The shorter `test_studio_provenance.py` is taken by S2's unrelated provenance-
# CHIPS suite, so this file uses check_scope's other convention name for app_routes_provenance.py.)
# The point of the ticket's amendment is that missing provenance must be VISIBLE and
# FIXABLE in the cockpit, not discoverable by shell — so these assert on the rendered surface: the count
# on Home, `origin: unlabelled` on a Review card, the plan the button renders, and the confirm that a
# stale/absent token refuses. tmp-path fixtures ONLY.
import json
import re
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Moment, MomentOrigin, Platform, Post, PostState, Source)

IN = ["2026-07-29", "2026-07-30"]


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        for tag, day in (("A", IN[0]), ("B", IN[1]), ("Z", "2026-07-13")):
            led.add_moment(Moment(id=f"m{tag}", parent_id="s1", content_token=tag, start=0, end=2, reason="r"))
            led.add_clip(Clip(id=f"c{tag}", parent_id=f"m{tag}", path=f"/{tag}.mp4", state=ClipState.queued))
            led.add_post(Post(id=f"p{tag}", parent_id=f"c{tag}", account="a", account_id="1",
                              platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                              created_at=f"{day}T10:00:00Z"))


def _token(body: str) -> str:
    m = re.search(r'name="plan_token" value="([^"]+)"', body)
    assert m, "the plan did not render a confirm form carrying its token"
    return m.group(1)


def test_home_shows_the_unlabelled_count_and_the_day_histogram(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    body = _client(cfg).get("/").get_data(as_text=True)
    assert 'id="provenance-panel"' in body
    assert "3 of 3 moment(s) read" in body and "unlabelled" in body
    for day in (*IN, "2026-07-13"):
        assert f'data-testid="prov-day-{day}"' in body        # every birth day the ledger HAS, measured, not literal


def test_the_panel_retires_itself_once_nothing_is_unlabelled(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:
        for mid in list(led.moments):
            led.moments[mid] = led.moments[mid].model_copy(update={"origin": MomentOrigin.operator})
    body = _client(cfg).get("/").get_data(as_text=True)
    assert 'data-testid="provenance-plan-btn"' not in body    # Home is not a museum of finished migrations


def test_a_review_card_reads_origin_unlabelled(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    body = _client(cfg).get("/review?account=all").get_data(as_text=True)
    assert 'data-testid="card-origin"' in body and "origin: unlabelled" in body


def test_plan_renders_the_selection_and_every_invariant_it_just_measured(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    body = _client(cfg).post("/provenance/plan", data={"day": IN}).get_data(as_text=True)
    assert 'data-testid="provenance-plan"' in body
    assert "machine_inferred" in body and "<strong>2</strong> moment(s) would be labelled" in body
    assert 'data-testid="provenance-invariants"' in body and "lineage 2 → 2 → 2 (1:1:1)" in body
    assert _token(body)


def test_plan_with_no_day_says_so_instead_of_looking_dead(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    body = _client(cfg).post("/provenance/plan", data={}).get_data(as_text=True)
    assert "pick at least one day" in body
    assert 'data-testid="provenance-confirm-btn"' not in body


def test_a_broken_invariant_renders_the_measured_values_and_offers_no_confirm(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:                      # a second post on a selected clip: not 1:1:1
        led.add_post(Post(id="pA2", parent_id="cA", account="b", account_id="2", platform=Platform.tiktok,
                          caption="x", state=PostState.awaiting_approval, created_at=f"{IN[0]}T12:00:00Z"))
    body = _client(cfg).post("/provenance/plan", data={"day": IN}).get_data(as_text=True)
    assert 'data-testid="provenance-stops"' in body and "Refused" in body
    assert 'data-testid="provenance-confirm-btn"' not in body
    assert "max 2 post(s) per clip" in body                   # the refusal SHOWS what it measured


def test_confirm_carries_the_token_end_to_end(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    c = _client(cfg)
    token = _token(c.post("/provenance/plan", data={"day": IN}).get_data(as_text=True))
    body = c.post("/provenance/confirm", data={"day": IN, "plan_token": token}).get_data(as_text=True)
    assert "Labelled 2 moment(s)" in body and "Rollback snapshot" in body
    led = Ledger.load(cfg)
    assert led.moments["mA"].origin is MomentOrigin.machine_inferred
    assert led.moments["mZ"].origin is MomentOrigin.unknown
    assert "1 of 3 moment(s) read" in c.get("/").get_data(as_text=True)


def test_confirm_without_a_token_refuses_at_the_route(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    body = _client(cfg).post("/provenance/confirm", data={"day": IN}).get_data(as_text=True)
    assert "show the plan first" in body
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_confirm_with_a_stale_token_refuses_at_the_route(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    c = _client(cfg)
    token = _token(c.post("/provenance/plan", data={"day": IN}).get_data(as_text=True))
    with Ledger.transaction(cfg) as led:                      # the daemon mints while the operator reads
        led.add_moment(Moment(id="mC", parent_id="s1", content_token="C", start=0, end=2, reason="r"))
        led.add_clip(Clip(id="cC", parent_id="mC", path="/C.mp4", state=ClipState.queued))
        led.add_post(Post(id="pC", parent_id="cC", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, created_at=f"{IN[0]}T13:00:00Z"))
    body = c.post("/provenance/confirm", data={"day": IN, "plan_token": token}).get_data(as_text=True)
    assert "the plan is stale" in body
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown
