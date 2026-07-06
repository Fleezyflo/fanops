# tests/test_review_lanes_e2e.py — RF6: the ONE cross-face end-to-end test. Proves the lane's +cast / −uncast
# BUTTONS reach the crosspost GATE (account_selection_admits), not merely the ledger record. This is the exact
# failure mode RF1's diagnosis flagged ("the brain works but never reaches output"): a cast that updates a
# selection but doesn't change what the gate admits is dead. So every assertion here checks the GATE verdict
# (admit/deny), with the ledger record as corroboration. Casting is ON (the firewall admits-all when OFF, which
# would mask the whole point). Slow UNIT (`@pytest.mark.slow`) — runs in CI `unit`, fully deterministic (time injected).
import json
import pytest
pytestmark = pytest.mark.slow
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Platform, ClipState, MomentState,
                           AccountSelection, SelectionMethod, account_selection_id, Fmt)
from fanops.casting import account_selection_admits

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")


def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
        led.add_moment(Moment(id="m0", parent_id="src1", content_token="0-7", start=0, end=7, reason="hook", state=MomentState.decided))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="8-15", start=8, end=15, reason="bridge", state=MomentState.decided))
        led.add_clip(Clip(id="c0", parent_id="m0", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        # @a is cast on m0 (so the source HAS selections — @b's absence then means DENY, not legacy fan-to-all).
        led.add_account_selection(AccountSelection(id=account_selection_id("src1", "a"), source_id="src1",
                                                   account="a", moment_ids=["m0"], method=SelectionMethod.llm))

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def test_lane_cast_button_drives_gate_admit_then_uncast_denies(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert cfg.account_casting is True                           # the gate must discriminate (OFF would admit-all)
    client = _client(cfg)

    # 1) BEFORE: @b is uncast on m0 -> the lane shows a +cast button AND the gate DENIES @b on m0.
    html = client.get("/review?view=lanes&source=src1").data.decode()
    assert "/cast/add/m0?source=src1&amp;cast_account=b" in html  # +cast for (b, m0); cast_account, NOT the ?account= filter
    assert "&amp;account=@b" not in html                          # the click must NOT set the global account filter (scope bleed)
    led = Ledger.load(cfg)
    assert account_selection_admits(cfg, led, led.moments["m0"], "b") is False   # gate: @b not cast -> DENY

    # 2) CLICK +cast: POST the route the button targets (exactly its url_for args).
    r = client.post("/cast/add/m0?source=src1&cast_account=@b&view=lanes")
    assert r.status_code == 200
    body = r.data.decode()
    assert "/cast/remove/m0?source=src1&amp;cast_account=b" in body  # the swapped body now shows −uncast for (b, m0)

    # 3) AFTER add: the GATE now ADMITS @b on m0 (the button reached output, not just the ledger).
    led = Ledger.load(cfg)
    sel = led.account_selection_for("src1", "b")
    assert sel is not None and "m0" in sel.moment_ids and sel.method == SelectionMethod.operator   # corroboration
    assert account_selection_admits(cfg, led, led.moments["m0"], "b") is True    # gate: ADMIT
    assert account_selection_admits(cfg, led, led.moments["m1"], "b") is False   # only m0 was cast -> m1 still DENY

    # 4) CLICK −uncast (the last pick): POST the remove route -> record DROPS -> gate DENIES again.
    # Deliberately uses the LEGACY ?account= arg (not cast_account) to keep the back-compat fallback
    # (request.args.get("cast_account") or _account_arg()) under test — an old caller must still work.
    r = client.post("/cast/remove/m0?source=src1&account=@b&view=lanes")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.account_selection_for("src1", "b") is None       # last pick removed -> no illegal empty operator row
    assert account_selection_admits(cfg, led, led.moments["m0"], "b") is False   # gate: back to DENY


def test_lane_cast_scopes_caption_surfaces(tmp_path):
    # the gate the lane drives is the SAME one caption-scoping uses (scoped_caption_surfaces) — so a freshly
    # cast moment also pulls the account into captioning. Proves reach into the OTHER gate consumer, not just crosspost.
    from types import SimpleNamespace
    from fanops.casting import scoped_caption_surfaces
    cfg = Config(root=tmp_path); _seed(cfg)
    client = _client(cfg)
    surfaces = [SimpleNamespace(account="a", platform=Platform.instagram),
                SimpleNamespace(account="b", platform=Platform.instagram)]
    led = Ledger.load(cfg)
    # BEFORE: @b uncast -> only @a survives the caption-scope gate for m0.
    assert ("b", Platform.instagram) not in scoped_caption_surfaces(cfg, led, led.moments["m0"], surfaces)
    client.post("/cast/add/m0?source=src1&account=@b&view=lanes")
    led = Ledger.load(cfg)
    # AFTER: the lane cast pulled @b into m0's caption scope too.
    assert ("b", Platform.instagram) in scoped_caption_surfaces(cfg, led, led.moments["m0"], surfaces)
