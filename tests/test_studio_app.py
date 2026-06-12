# tests/test_studio_app.py — CREATE
import json
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")  # the Studio web UI is an optional extra ([studio]); skip these
                              # route tests cleanly when Flask is absent (a core .[dev]-only venv/CI)
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

# NOW must track the REAL wall clock: these tests exercise the HTTP routes, which (unlike the
# actions layer) cannot inject `now=` — so the imminence guard inside compares seeded
# scheduled_times against datetime.now(). An absolute NOW time-bombed this file: every seed went
# "already due" once the calendar passed it (caught 2026-06-12, six days after the bomb date).
# microsecond=0 so _z() round-trips exactly through _normalize_z in the reschedule equality assert.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg, tmp_path):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    # seeds live under cfg.clips like real renders — the media routes only serve INSIDE cfg.base
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    variant = cfg.clips / "variant.mp4"; variant.write_bytes(b"\x00\x00\x00\x18ftypmp42VARIANT!")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_base", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="BASE", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.add_post(Post(id="p_var", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="VAR", state=PostState.queued,
                      media_urls=[f"file://{variant}"], scheduled_time=_z(NOW + timedelta(hours=4))))
    led.save()
    return base, variant

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()

def test_tabs_return_200(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    c = _client(cfg)
    for path, needle in [("/review", b"Review"), ("/schedule", b"Schedule"), ("/lift", b"Lift")]:
        r = c.get(path); assert r.status_code == 200 and needle in r.data

def test_root_redirects_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).get("/")
    assert r.status_code in (301, 302) and "/review" in r.headers["Location"]

def test_media_serves_variant_when_present(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    r = _client(cfg).get("/media/p_var")
    assert r.status_code == 200 and r.data == variant.read_bytes()   # variant file, not base

def test_media_falls_back_to_base_clip(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    r = _client(cfg).get("/media/p_base")
    assert r.status_code == 200 and r.data == base.read_bytes()

def test_media_404_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    assert _client(cfg).get("/media/nope").status_code == 404

def test_media_refuses_paths_outside_data_tree(tmp_path):
    # Stage-5/6 audit MEDIUM: ledger paths are trusted in normal operation, but a hand-edited or
    # corrupt ledger must not turn the localhost cockpit into an arbitrary-file server. Any path
    # resolving OUTSIDE cfg.base (here: under root but outside the data tree) must 404 on both
    # send_file routes, even though the file exists.
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    outside = tmp_path / "outside.txt"; outside.write_text("secret")
    led = Ledger.load(cfg)
    led.posts["p_var"].media_urls = [f"file://{outside}"]
    led.clips["clip_1"].path = str(outside)
    led.save()
    c = _client(cfg)
    assert c.get("/media/p_var").status_code == 404
    assert c.get("/clips/clip_1").status_code == 404

def test_media_404_missing_file(tmp_path):
    cfg = Config(root=tmp_path); base, variant = _seed(cfg, tmp_path)
    variant.unlink()   # stale path
    assert _client(cfg).get("/media/p_var").status_code == 404

def test_clips_serves_base_and_404(tmp_path):
    cfg = Config(root=tmp_path); base, _ = _seed(cfg, tmp_path)
    c = _client(cfg)
    assert c.get("/clips/clip_1").status_code == 200
    assert c.get("/clips/nope").status_code == 404

def test_reschedule_route_roundtrips_to_ledger(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    new = _z(NOW + timedelta(days=2))
    r = _client(cfg).post("/reschedule/p_base", data={"new_time": new})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p_base"].scheduled_time == new

def test_caption_route_roundtrips_to_ledger(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/caption/p_base", data={"caption": "EDITED VIA HTTP"})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p_base"].caption == "EDITED VIA HTTP"

def test_snooze_route_roundtrips(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/snooze/clip_1")
    assert r.status_code == 200
    from fanops.timeutil import parse_iso
    assert parse_iso(Ledger.load(cfg).posts["p_base"].scheduled_time) > NOW + timedelta(days=300)

def test_core_cli_imports_with_flask_absent(monkeypatch):
    # spec §10/§15: a no-[studio] install must still import fanops.cli and run non-studio verbs.
    import sys, builtins, importlib
    real_import = builtins.__import__
    def blocked(name, *a, **k):
        if name == "flask" or name.startswith("flask."):
            raise ImportError("flask blocked for test")
        return real_import(name, *a, **k)
    for m in list(sys.modules):
        if m == "flask" or m.startswith("flask.") or m.startswith("fanops.studio.app"):
            sys.modules.pop(m, None)
    monkeypatch.setattr(builtins, "__import__", blocked)
    importlib.reload(importlib.import_module("fanops.cli"))   # must NOT raise
    import fanops.cli as cli
    assert cli.main(["status"]) in (0, 1, 2)   # a real verb dispatches without Flask
    # ...and ONLY the studio verb needs Flask: this proves the import is lazy AND inside _dispatch
    # (a module-top import would have already failed the reload above; this catches a top-of-app
    # import that somehow still let the reload pass). The studio branch hits `from fanops.studio.app
    # import create_app` -> blocked flask -> ImportError, which main() does not swallow.
    with pytest.raises(ImportError, match="flask blocked"):
        cli.main(["studio"])
