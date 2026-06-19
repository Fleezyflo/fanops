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

def test_core_cli_imports_with_flask_absent(monkeypatch, tmp_path):
    # spec §10/§15: a no-[studio] install must still import fanops.cli and run non-studio verbs.
    monkeypatch.chdir(tmp_path)                    # fresh root: `status` must SUCCEED, not just run
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
    # == 0, not `in (0,1,2)` (stage-6 audit): the tolerant assert accepted a CRASHING status verb;
    # on a fresh root, status must actually succeed without Flask.
    assert cli.main(["status"]) == 0
    # ...and ONLY the studio verb needs Flask: this proves the import is lazy AND inside _dispatch
    # (a module-top import would have already failed the reload above; this catches a top-of-app
    # import that somehow still let the reload pass). The studio branch hits `from fanops.studio.app
    # import create_app` -> blocked flask -> ImportError, which main() does not swallow.
    with pytest.raises(ImportError, match="flask blocked"):
        cli.main(["studio"])

# ---- M5.1: held-clip RELEASE route (UI twin of `fanops unhold`) ----
def _seed_held(cfg, tmp_path):
    base, _variant = _seed(cfg, tmp_path)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk: slur"))
    led.save()
    return base

def test_review_held_card_shows_release_button(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg, tmp_path)
    r = _client(cfg).get("/review")
    assert r.status_code == 200
    assert b"/unhold/clip_held" in r.data and b"Release" in r.data and b'hx-target="#card-clip_held"' in r.data

def test_unhold_success_returns_empty_fragment(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg, tmp_path)
    r = _client(cfg).post("/unhold/clip_held")
    assert r.status_code == 200
    assert b"HELD" not in r.data and b"Release" not in r.data   # empty fragment: the held card is gone in place

def test_unhold_success_clip_leaves_held_bucket(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg, tmp_path)
    c = _client(cfg); c.post("/unhold/clip_held")
    r = c.get("/review")
    assert r.status_code == 200                                 # guard: absence assert must not pass on a 500
    assert b"/unhold/clip_held" not in r.data                   # left the held bucket (no Release form)
    # NEW behavior: a released clip (captions_requested, no posts) now surfaces in the 'prepared'
    # bucket instead of vanishing — the post-less-clips-are-invisible bug is fixed.
    assert b"card-clip_held" in r.data
    assert Ledger.load(cfg).clips["clip_held"].held is False

def test_unhold_non_held_clip_returns_inline_error(tmp_path):
    cfg = Config(root=tmp_path); _seed_held(cfg, tmp_path)      # clip_1 is queued, not held
    r = _client(cfg).post("/unhold/clip_1")
    assert r.status_code == 200 and b"not held" in r.data
    assert Ledger.load(cfg).clips["clip_1"].state is ClipState.queued

# ---- prepared bucket: produced-but-post-less clips must be visible (the 57-clips-0-posts bug) ----
def _seed_prepared(cfg, tmp_path):
    # one source -> one clip, queued, NO posts (mirrors a fresh ingest+advance that hasn't crossposted)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "prep.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42PREP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_p", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_p", parent_id="src_p", content_token="0-7", start=0, end=7,
                          reason="big drop", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_p", parent_id="mom_p", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    led.save()
    return base

def test_review_surfaces_prepared_clips(tmp_path):
    cfg = Config(root=tmp_path); _seed_prepared(cfg, tmp_path)
    r = _client(cfg).get("/review")
    assert r.status_code == 200
    assert b"card-clip_p" in r.data                             # the post-less clip is VISIBLE now
    assert b"Ready to prepare" in r.data                        # the prepared bucket header
    assert b"Nothing in the ledger" not in r.data               # the false-empty message is gone
    assert b'href="/run"' in r.data                             # a working forward path (the Run tab)

def test_review_empty_state_honest_when_truly_empty(tmp_path):
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": []}))  # no sources, no clips
    r = _client(cfg).get("/review")
    assert r.status_code == 200
    assert b"No footage yet" in r.data                          # honest empty message
    assert b"fanops advance" not in r.data                      # no CLI verb in a no-terminal product

def test_mark_posted_success_does_not_leak_raw_dict_repr(tmp_path):
    # DEFECT: _result.html dumped result.detail's Python repr when it had no scheduled_time/caption
    # key — so "Mark posted" with no URL showed the operator `✓ {'post_id': 'p_base', 'url': None}`.
    # A success message must be human-readable, never a dict repr.
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/publish/posted/p_base")            # no url -> detail={'post_id':..,'url':None}
    assert r.status_code == 200
    assert b"post_id" not in r.data                             # no raw Python dict key leaked (Jinja escapes ' -> &#39;)
    assert b"\xe2\x9c\x93" in r.data                            # still shows the ✓ success mark

def test_publish_now_success_does_not_leak_raw_dict_repr(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)           # dryrun backend -> publishes locally only
    r = _client(cfg).post("/publish/now/p_base", data={"confirm": "1"})
    assert r.status_code == 200
    assert b"post_id" not in r.data and b"&#39;" not in r.data  # no leaked dict repr (key or escaped quote)
    assert b"\xe2\x9c\x93" in r.data                            # ✓ success, human-readable


# ---- content-lifecycle Phase 4: cross-account reuse routes ----
def _seed_xacct_route(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        cfg.clips.mkdir(parents=True, exist_ok=True)
        cpath = cfg.clips / "c.mp4"; cpath.write_bytes(b"\x00")          # real render file — #10 guard checks existence
        c = Clip(id="clip_1", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16, state=ClipState.queued)
        c.meta_captions = {"@b/instagram": {"caption": "reuse", "hashtags": []}}
        led.add_clip(c)
        led.add_post(Post(id="p_a", parent_id="clip_1", account="@a", account_id="ig_a",
                          platform=Platform.instagram, caption="on A", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z"))

def test_crosspost_route_mints_on_target(tmp_path):
    cfg = Config(root=tmp_path); _seed_xacct_route(cfg)
    r = _client(cfg).post("/posts/crosspost/clip_1", data={"target_account": "@b", "platform": "instagram"})
    assert r.status_code == 200
    awaiting = [p for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval and p.account == "@b"]
    assert len(awaiting) == 1

def test_crosspost_route_bad_target_is_banner_not_500(tmp_path):
    cfg = Config(root=tmp_path); _seed_xacct_route(cfg)
    r = _client(cfg).post("/posts/crosspost/clip_1", data={"target_account": "@nope", "platform": "instagram"})
    assert r.status_code == 200                                # fail-open: a result banner, never a 500
    assert b"no active surface" in r.data

def test_crosspost_all_route_bulk(tmp_path):
    cfg = Config(root=tmp_path); _seed_xacct_route(cfg)
    r = _client(cfg).post("/posts/crosspost-all", data={"source_account": "@a", "target_account": "@b", "platform": "instagram"})
    assert r.status_code == 200
    awaiting = [p for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval and p.account == "@b"]
    assert len(awaiting) == 1
