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

def test_root_renders_home(tmp_path):
    # Face 2: GET / is a real status home page now, NOT a redirect to /review.
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).get("/")
    assert r.status_code == 200 and b"Home" in r.data and b"Posted" in r.data

def test_home_nav_link(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    assert b">Home<" in _client(cfg).get("/review").data   # the primary nav carries a Home anchor

def test_home_links_to_golive(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    assert b"/golive" in _client(cfg).get("/").data        # onboarding CTA into the Go-Live connect flow

def test_home_metrics_per_account(tmp_path):
    # S10: an ACTIVE account's post count renders INLINE on its account row; the #home-metrics table is now
    # only the orphan fallback (handles with history but no active account), so it is absent when @a is active.
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)       # _seed births @a posts (@a is an active account)
    html = _client(cfg).get("/").data.decode()
    assert 'data-slot="metrics"' in html                   # the section still exists (orphan fallback)
    assert 'data-acct-count="@a"' in html                  # @a's count is inline on its account row
    assert 'data-metric="by-account"' not in html          # no orphans -> no fallback table

def test_home_batch_deep_link_and_zero_result(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    from fanops.batches import create_batch
    led = Ledger.load(cfg)
    create_batch(led, name="Ghost", target_accounts=["@ghost"], now_iso="2026-06-22T00:00:00.000001Z"); led.save()
    html = _client(cfg).get("/").data.decode()
    assert "/review?batch=" in html and 'data-warn="zero-result"' in html   # deep-link + the silent-fail badge

def test_home_no_zero_result_for_matched_batch(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    from fanops.batches import create_batch
    led = Ledger.load(cfg)
    b = create_batch(led, name="Real", target_accounts=["@a"], now_iso="2026-06-22T00:00:00.000003Z")
    led.add_post(Post(id="p_rb", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.queued, batch_id=b.id)); led.save()
    assert b'data-warn="zero-result"' not in _client(cfg).get("/").data   # matched target -> no false alarm

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

def test_review_renders_removed_hook_badge(tmp_path, monkeypatch):
    # slice 1: a moment whose hook was stripped surfaces a "hook removed" badge + the text in /review,
    # so the operator SEES the hook that was killed (the clip itself still ran clean).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")   # M3d: the badge is OFF-mode (hidden when per-surface hooks own the burn)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped, hook_removed="made it and lost everything"))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval))
    r = _client(cfg).get("/review?view=list")
    assert r.status_code == 200
    assert b"hook removed" in r.data and b"made it and lost everything" in r.data


def _seed_removed_hook(cfg):
    # slice 2: a clip whose moment hook was stripped, with one awaiting post — the removed-hook choice setup.
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped, hook_removed="made it and lost everything"))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval))


def test_review_renders_both_hook_choice_buttons(tmp_path, monkeypatch):
    # slice 2: the removed-hook card offers BOTH one-click choices.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")   # M3d: the restore choice is OFF-mode only (hidden when ON)
    cfg = Config(root=tmp_path); _seed_removed_hook(cfg)
    r = _client(cfg).get("/review?view=list")
    assert r.status_code == 200
    assert b"Approve with hook" in r.data and b"Approve as-is" in r.data


def test_approve_with_hook_route_restores_and_approves(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")   # M3d: the moment-restore flow is OFF-mode (ON -> per-surface hooks own the burn)
    cfg = Config(root=tmp_path); _seed_removed_hook(cfg)
    def _fake(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
        c = next(c for c in led.clips.values() if c.parent_id == moment_id and c.aspect is aspect)
        new = c.model_copy(update={"state": ClipState.rendered, "meta_captions": {}})
        led.clips[c.id] = new; return led, new
    mocker.patch("fanops.clip.render_moment", side_effect=_fake)
    r = _client(cfg).post("/posts/approve-with-hook/clip_1")
    assert r.status_code == 200 and b"hook restored" in r.data
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook == "made it and lost everything" and led.moments["mom_1"].hook_removed is None
    assert led.posts["p1"].state is PostState.queued


def test_approve_as_is_route_approves_clean(tmp_path):
    cfg = Config(root=tmp_path); _seed_removed_hook(cfg)
    r = _client(cfg).post("/posts/approve-as-is/clip_1")
    assert r.status_code == 200 and b"Approved 1 post" in r.data
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.queued
    assert led.moments["mom_1"].hook is None                 # shipped clean — not restored


# ---- P1: suggest/clear UI controls + the stale-input swap fix ----
def _seed_awaiting(cfg, tmp_path, *, pid="p_aw"):
    # an awaiting_approval post on the SAME clip as _seed, so /review shows it in the editable bucket.
    _seed(cfg, tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id=pid, parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="AWAIT", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=5))))
    led.save()

def test_review_renders_suggestion_and_clear_action(tmp_path):
    cfg = Config(root=tmp_path); _seed_awaiting(cfg, tmp_path)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert "/clear/p_aw" in html                              # Clear-time form action for the editable surface
    assert "Suggested" in html                                # the suggestion hint is shown

def test_schedule_renders_suggestion_and_clear_action(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)         # p_base/p_var are queued + editable
    html = _client(cfg).get("/schedule").data.decode()
    assert "/schedule/clear/p_base" in html                   # Clear-time form action for the editable row
    assert "Use suggested" in html

def test_clear_route_on_awaiting_returns_empty_time_input(tmp_path):
    cfg = Config(root=tmp_path); _seed_awaiting(cfg, tmp_path)
    r = _client(cfg).post("/clear/p_aw")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'name="new_time" value=""' in body                 # the re-rendered editor's time input is EMPTY (not stale)
    assert Ledger.load(cfg).posts["p_aw"].scheduled_time is None

def test_schedule_clear_route_moves_queued_back_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, tmp_path)
    r = _client(cfg).post("/schedule/clear/p_base")
    assert r.status_code == 200
    assert b"p_base" not in r.data                             # the row is GONE from the re-rendered bucket
    led = Ledger.load(cfg)
    assert led.posts["p_base"].state is PostState.awaiting_approval and led.posts["p_base"].scheduled_time is None

def test_review_reschedule_surface_reflects_new_time_in_input(tmp_path):
    # the stale-input fix: rescheduling from the Review editor re-renders _surface_edit.html with the NEW
    # value in the time input (not _result.html, which left the old value visible). local-time: storage stays
    # canonical UTC, but the datetime-local input shows the operator's LOCAL form of that instant.
    from fanops.timeutil import to_local_input
    cfg = Config(root=tmp_path); _seed_awaiting(cfg, tmp_path)
    new = _z(NOW + timedelta(days=3))
    r = _client(cfg).post("/reschedule-surface/p_aw", data={"new_time": new})
    assert r.status_code == 200
    body = r.data.decode()
    assert f'name="new_time" value="{to_local_input(new)}"' in body   # the editor shows the fresh value, localized
    assert Ledger.load(cfg).posts["p_aw"].scheduled_time == new       # ...but the ledger keeps UTC

def test_reschedule_surface_local_input_stored_as_utc(tmp_path):
    # the datetime-local control submits a naive LOCAL value; the route interprets it as local and stores
    # canonical UTC. tz-INDEPENDENT: a UTC instant -> its local-input form -> back through the route == itself.
    from fanops.timeutil import to_local_input
    cfg = Config(root=tmp_path); _seed_awaiting(cfg, tmp_path)
    z = _z(NOW.replace(second=0) + timedelta(days=4))         # minute-granular (datetime-local has no seconds)
    r = _client(cfg).post("/reschedule-surface/p_aw", data={"new_time": to_local_input(z)})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p_aw"].scheduled_time == z
