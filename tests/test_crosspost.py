import json, subprocess, sys, textwrap
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Platform, Fmt
from fanops.accounts import Accounts
from fanops.crosspost import surface_time, crosspost_clips
from fanops.ids import _hash

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _captioned(led, cfg, mocker):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    # one already-rendered 9:16 clip; crosspost will render 16:9 on demand for youtube
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "ig cap", "hashtags": ["#x"]},
                          "@a/youtube": {"caption": "yt cap", "hashtags": ["#y"]}}
    led.add_clip(clip)
    # NOTE: mocker.patch("fanops.clip.subprocess.run", ...) patches the SHARED subprocess
    # module singleton, so the patch also intercepts the real interpreter spawns the
    # subprocess tests make in their own body. Fake only ffmpeg render commands; pass any
    # non-ffmpeg call (e.g. [sys.executable, "-c", ...]) through to the real subprocess.run
    # so the cross-process idempotency / stability tests can actually launch a child.
    real_run = subprocess.run
    def fake_run(cmd, **kw):
        if not (isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg"):
            return real_run(cmd, **kw)
        from pathlib import Path
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

def test_surface_time_reproducible_ordered_and_future():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    t0 = surface_time(base, "@a", "instagram", "2026-06-02", index=0)
    t0b = surface_time(base, "@a", "instagram", "2026-06-02", index=0)
    t1 = surface_time(base, "@a", "instagram", "2026-06-02", index=1)
    assert t0 == t0b                                  # reproducible (no hash() seed)
    assert t0 < t1                                    # later index => later time (ordered)
    assert t0 > base.isoformat().replace("+00:00", "Z")  # in the future vs base
    assert t0.endswith("Z")


def test_surface_time_is_monotonic_across_many_indices():
    # AUDIT H1/H2: the per-index increment was a FRESH random draw per call (rng reseeded with
    # seed + index*7919), so a higher index could draw a smaller step and land EARLIER than a
    # lower index — non-monotonic. The old test only checked index 0 vs 1. Assert strict monotonic
    # ordering across a full run of indices.
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    times = [surface_time(base, "@a", "instagram", "2026-06-02", index=i) for i in range(12)]
    assert times == sorted(times) and len(set(times)) == len(times)   # strictly increasing, no dupes


def test_surface_time_differs_per_clip_no_minute_collision():
    # AUDIT H1/H2: surface_time ignored the clip entirely, so two different clips posting to the
    # SAME surface (same account/platform/index) got the IDENTICAL timestamp — a lockstep, exact-
    # minute collision and a fingerprint. Threading clip_id must separate them.
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    a = surface_time(base, "@a", "instagram", "2026-06-02", index=0, clip_id="clip_1")
    b = surface_time(base, "@a", "instagram", "2026-06-02", index=0, clip_id="clip_2")
    assert a != b                                     # distinct clips -> distinct times on a surface
    # still deterministic per (clip, surface, index)
    assert a == surface_time(base, "@a", "instagram", "2026-06-02", index=0, clip_id="clip_1")

def test_surface_time_stable_across_processes():
    code = textwrap.dedent("""
        from datetime import datetime, timezone
        from fanops.crosspost import surface_time
        base = datetime(2026,6,2,18,0,tzinfo=timezone.utc)
        print(surface_time(base, "@a", "tiktok", "2026-06-02", index=2))
    """)
    r1 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r1.stdout.strip() == r2.stdout.strip() != ""

def test_crosspost_fans_out_with_right_aspect_and_account_id(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432",
                          "platforms": ["instagram", "youtube"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = [p for p in led.posts.values() if led.clips[p.parent_id].parent_id == "mom_1"]
    assert len(posts) == 2
    by_plat = {p.platform: p for p in posts}
    assert by_plat[Platform.instagram].caption == "ig cap"
    assert by_plat[Platform.youtube].caption == "yt cap"
    # account_id is the resolved NUMERIC id, not the handle (FIX F06)
    assert all(p.account_id == "98432" for p in posts)
    assert all(p.account == "@a" for p in posts)
    # right aspect per platform (FIX F20): IG 9:16, YouTube 16:9
    assert by_plat[Platform.instagram].aspect is Fmt.r9x16
    assert by_plat[Platform.youtube].aspect is Fmt.r16x9
    # staggered
    assert by_plat[Platform.instagram].scheduled_time != by_plat[Platform.youtube].scheduled_time

def test_crosspost_idempotent_across_processes(tmp_path, mocker):
    # FIX F00/F56: re-running in a SEPARATE process must not duplicate posts.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.clips["clip_1"].state = ClipState.captioned   # simulate a re-run finding it captioned again
    led.save()
    # re-run crosspost in a fresh interpreter against the SAME ledger
    code = textwrap.dedent(f"""
        from fanops.config import Config
        from fanops.ledger import Ledger
        from fanops.accounts import Accounts
        from fanops.crosspost import crosspost_clips
        cfg = Config(root=r"{tmp_path}")
        led = Ledger.load(cfg)
        led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
        led.save()
        print(len([p for p in led.posts.values()]))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.stdout.strip() == "1", r.stderr

def test_crosspost_skips_held_and_retired(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led.clips["clip_1"].state = ClipState.held
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values()] == []
    # retired moment lineage also skipped
    led.clips["clip_1"].state = ClipState.captioned
    led.retire_clip("clip_1")
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values()] == []

def test_crosspost_multi_account_fans_out_n_times_m(tmp_path, mocker):
    # N accounts x M platforms = N*M posts, distinct account_ids.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "youtube"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "youtube"], "status": "active"},
    ])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {
        "@a/instagram": {"caption": "a ig", "hashtags": []}, "@a/youtube": {"caption": "a yt", "hashtags": []},
        "@b/instagram": {"caption": "b ig", "hashtags": []}, "@b/youtube": {"caption": "b yt", "hashtags": []},
    }
    led.add_clip(clip)
    def fake_run(cmd, **kw):
        from pathlib import Path
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 4
    assert {p.account_id for p in led.posts.values()} == {"1", "2"}


def test_crosspost_two_clips_same_surface_do_not_collide_on_time(tmp_path, mocker):
    # AUDIT H1/H2: two clips (distinct moments) posting to the SAME surface must not land on the
    # same minute (surface_time previously ignored the clip -> identical timestamps -> lockstep
    # fingerprint across an account's posts).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    for mid, cid in [("mom_1", "clip_1"), ("mom_2", "clip_2")]:
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token=cid, start=0, end=7,
                              reason="r", state=MomentState.clipped))
        c = Clip(id=cid, parent_id=mid, path=f"/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
        c.meta_captions = {"@a/instagram": {"caption": f"{cid} cap", "hashtags": []}}
        led.add_clip(c)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    ig_posts = [p for p in led.posts.values() if p.platform is Platform.instagram]
    assert len(ig_posts) == 2
    times = {p.scheduled_time for p in ig_posts}
    assert len(times) == 2, "two clips on the same surface collided on the same scheduled_time"

def test_crosspost_renders_missing_aspect_on_demand(tmp_path, mocker):
    # Only a 9:16 clip exists; youtube needs 16:9 -> a NEW clip is created (rendered, file exists).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["youtube"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)   # seeds one 9:16 clip + meta for @a/youtube
    assert len(led.clips) == 1
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    # a 16:9 clip was rendered on demand
    aspects = {c.aspect for c in led.clips.values()}
    assert Fmt.r16x9 in aspects and len(led.clips) == 2
    yt_post = next(p for p in led.posts.values() if p.platform is Platform.youtube)
    assert yt_post.parent_id != "clip_1"                    # points at the new 16:9 clip
    assert led.clips[yt_post.parent_id].state is ClipState.rendered

def test_crosspost_does_not_reuse_error_clip_and_no_post_for_failed_render(tmp_path, mocker):
    # FIX: a failed on-demand render must NOT yield a post pointing at a dangling file,
    # and the error clip must not be reused as a render target.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["youtube"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c_9x16.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"@a/youtube": {"caption": "yt", "hashtags": []}}
    led.add_clip(clip)
    # ffmpeg FAILS for the on-demand 16:9 render (returncode 1, no output file written)
    def failing_run(cmd, **kw):
        class R: returncode = 1; stderr = "boom"
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=failing_run)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    # the 16:9 clip is in error state...
    err_clips = [c for c in led.clips.values() if c.aspect is Fmt.r16x9]
    assert len(err_clips) == 1 and err_clips[0].state is ClipState.error
    # ...and NO post was created (a post pointing at a fileless error clip would be the bug)
    assert all(led.clips[p.parent_id].state is not ClipState.error for p in led.posts.values())

def test_crossposted_post_gets_a_client_token_submission_id(tmp_path, mocker):
    # AUDIT H1: every post is stamped at BIRTH with a stable, content-addressed client idempotency
    # token as its submission_id (f"fanops_{_hash('idemp', pid)}"). This guarantees an ambiguous
    # publish is ALWAYS pollable/reconcilable (no post can ever be stranded id-less). Stable because
    # the post id is content-addressed, so a re-run computes the identical token.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432",
                          "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    ig = next(p for p in led.posts.values() if p.platform is Platform.instagram)
    assert ig.submission_id == f"fanops_{_hash('idemp', ig.id)}"
    assert ig.submission_id.startswith("fanops_")

def test_crosspost_appends_artist_tag_when_decided(tmp_path, mocker):
    # The \n@mohflow append branch must actually fire and be correct (own line, right handle).
    from fanops.tagging import ARTIST_HANDLE
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)   # @a/instagram caption present
    # Force the tag decision on by monkeypatching decide_tag to True for this clip.
    import fanops.crosspost as xp
    mocker.patch.object(xp, "decide_tag", return_value=True)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    ig = next(p for p in led.posts.values() if p.platform is Platform.instagram)
    assert ig.caption.endswith(f"\n{ARTIST_HANDLE}")        # tag on its OWN line, never in the hook
    assert ig.caption.startswith("ig cap")
