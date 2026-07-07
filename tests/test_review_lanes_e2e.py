# tests/test_review_lanes_e2e.py — RF6/P13: the ONE cross-face end-to-end test. Proves the lane's +cast / −uncast
# BUTTONS reach the crosspost GATE (affinity_admits), not merely the ledger record. This is the exact failure mode
# RF1's diagnosis flagged ("the brain works but never reaches output"): a cast that updates the record but doesn't
# change what the gate admits is dead. So every assertion checks the GATE verdict (admit/deny), with the ledger
# Moment.affinities as corroboration. Casting is ON (the firewall admits-all when OFF, masking the point).
import json
import pytest
pytestmark = pytest.mark.slow
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt
from fanops.casting import affinity_admits

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
        # m0 is single-owner @a (attributed) -> @b is DENIED on it (no silent fan-to-all). m1 is persona-blind.
        led.add_moment(Moment(id="m0", parent_id="src1", content_token="0-7", start=0, end=7, reason="hook",
                              state=MomentState.decided, affinities=["a"]))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="8-15", start=8, end=15, reason="bridge",
                              state=MomentState.decided, affinities=["a"]))
        led.add_clip(Clip(id="c0", parent_id="m0", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))

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
    assert affinity_admits(cfg, led.moments["m0"], "b") is False  # gate: @b not an owner -> DENY

    # 2) CLICK +cast: POST the route the button targets (exactly its url_for args).
    r = client.post("/cast/add/m0?source=src1&cast_account=@b&view=lanes")
    assert r.status_code == 200
    body = r.data.decode()
    assert "/cast/remove/m0?source=src1&amp;cast_account=b" in body  # the swapped body now shows −uncast for (b, m0)

    # 3) AFTER add: the GATE now ADMITS @b on m0 (the button reached output, not just the ledger).
    led = Ledger.load(cfg)
    assert "b" in led.moments["m0"].affinities                   # corroboration: operator co-owned m0
    assert affinity_admits(cfg, led.moments["m0"], "b") is True   # gate: ADMIT
    assert affinity_admits(cfg, led.moments["m1"], "b") is False  # only m0 was cast -> m1 (owned by @a) still DENY

    # 4) CLICK −uncast: POST the remove route -> @b pops out of affinities -> gate DENIES again.
    # Deliberately uses the LEGACY ?account= arg (not cast_account) to keep the back-compat fallback under test.
    r = client.post("/cast/remove/m0?source=src1&account=@b&view=lanes")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert "b" not in led.moments["m0"].affinities              # removed
    assert affinity_admits(cfg, led.moments["m0"], "b") is False  # gate: back to DENY

# NB (P10 / MOL-151): the old `test_lane_cast_scopes_caption_surfaces` was removed. Caption scoping is now
# owner × platform via `affinity_admits` (the SAME gate crosspost enforces), NOT the AccountSelection the
# cast lane writes — so the lane no longer drives caption scope. Owner-scoping is covered by
# tests/test_caption_scoping.py + tests/test_mol151_p10_captions.py.
