import json, subprocess, sys, textwrap
from datetime import datetime, timezone
from pathlib import Path
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
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
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
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
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

def test_crosspost_skips_surface_when_clip_exceeds_platform_max(tmp_path, mocker):
    # AUDIT (g): per-surface duration clamp. A clip's PLAYABLE duration is its MOMENT window
    # (end - start), NOT a Clip field (Clip has no .duration). A 120s window is OVER the
    # youtube cap (60s) but UNDER tiktok's (600s). The over-cap surface must get NO queued post
    # while the under-cap surface still posts — per-surface SKIP, not all-or-nothing-wedged.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1",
                          "platforms": ["tiktok", "youtube"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, duration=300.0))
    # 120s window: > youtube cap (60), < tiktok cap (600)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-120", start=0.0, end=120.0,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    # captions present for BOTH surfaces so a post would otherwise be created for each
    clip.meta_captions = {"@a/tiktok": {"caption": "tt cap", "hashtags": []},
                          "@a/youtube": {"caption": "yt cap", "hashtags": []}}
    led.add_clip(clip)
    def fake_run(cmd, **kw):   # satisfy the on-demand 16:9 render for youtube
        from pathlib import Path
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    plats = {p.platform for p in led.posts.values()}
    assert Platform.youtube not in plats, "over-cap surface (youtube 60s) must be skipped for a 120s clip"
    assert Platform.tiktok in plats, "under-cap surface (tiktok 600s) must still post — per-surface, not all-or-nothing"


def test_crosspost_posts_when_duration_unknown(tmp_path, mocker):
    # AUDIT (g) fail-open: a 0-length moment window (end - start == 0) yields an UNKNOWN/
    # None-equivalent duration. Never silently drop a post over an unprobed duration — post to
    # ALL surfaces (the old behavior posted regardless; the clamp only acts on a KNOWN dur > 0).
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1",
                          "platforms": ["tiktok", "youtube"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    # 0-length window -> dur == 0.0 -> unknown -> fail-open (even though youtube's cap is 60s).
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="5-5", start=5.0, end=5.0,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/tiktok": {"caption": "tt cap", "hashtags": []},
                          "@a/youtube": {"caption": "yt cap", "hashtags": []}}
    led.add_clip(clip)
    def fake_run(cmd, **kw):
        from pathlib import Path
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    plats = {p.platform for p in led.posts.values()}
    assert Platform.youtube in plats and Platform.tiktok in plats, \
        "unknown duration (0-length window) must fail-open: post to ALL surfaces, never silently drop"


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


def test_crosspost_creates_per_account_variant_when_enabled(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.accounts import Accounts, Account, AccountStatus
    from fanops.models import Source, Moment, Clip, MomentState, ClipState, Fmt, Platform
    import fanops.overlay as overlay  # noqa: F401
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # two active accounts, same platform -> same aspect, so they'd share a clip today
    accts = Accounts(cfg); accts.accounts = [
        Account(handle="@a", account_id="1", platforms=[Platform.instagram], status=AccountStatus.active),
        Account(handle="@b", account_id="2", platforms=[Platform.instagram], status=AccountStatus.active)]
    led.add_source(Source(id="s1", source_path=str(tmp_path/"s.mp4"), width=1080, height=1920))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5, reason="r",
                          state=MomentState.clipped, hook="default hook"))
    # a captioned base clip with per-surface captions+hooks for both accounts
    clip = Clip(id="c1", parent_id="m1", path=str(tmp_path/"c1.mp4"), aspect=Fmt.r9x16,
                state=ClipState.captioned)
    Path(clip.path).write_bytes(b"BASECLIP")
    clip.meta_captions = {"@a/instagram": {"caption": "A cap", "hashtags": [], "hook": "HOOK A"},
                          "@b/instagram": {"caption": "B cap", "hashtags": [], "hook": "HOOK B"}}
    led.add_clip(clip)
    # make burn_hook_only deterministic + observable (write a distinct file per call)
    calls = []
    def fake_burn(base, out, hook, **kw):
        calls.append((out, hook)); Path(out).write_bytes(("V:"+hook).encode()); return True
    mocker.patch("fanops.crosspost.overlay.burn_hook_only", side_effect=fake_burn)
    from fanops.crosspost import crosspost_clips
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    posts = [p for p in led.posts.values()]
    assert len(posts) == 2
    by_acct = {p.account: p for p in posts}
    # each account got a DIFFERENT variant_hook + variant_key, and burn_hook_only was called per account
    assert by_acct["@a"].variant_hook == "HOOK A" and by_acct["@b"].variant_hook == "HOOK B"
    assert by_acct["@a"].variant_key and by_acct["@a"].variant_key != by_acct["@b"].variant_key
    assert len(calls) == 2 and {h for _, h in calls} == {"HOOK A", "HOOK B"}
    # DETERMINISM (pinned in-test, not only by construction): variant_key MUST be the
    # content-addressed surface_key — a future swap to random/uuid (distinct but non-reproducible,
    # the #1 v1 duplicate-post bug) would still pass the distinctness check above, so assert the
    # exact content-addressed value here.
    from fanops.ids import surface_key
    assert by_acct["@a"].variant_key == surface_key("@a", "instagram")
    assert by_acct["@b"].variant_key == surface_key("@b", "instagram")

def test_crosspost_no_variant_when_disabled(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_CREATIVE_VARIATION", raising=False)   # OFF
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.accounts import Accounts, Account, AccountStatus
    from fanops.models import Source, Moment, Clip, MomentState, ClipState, Fmt, Platform
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = Accounts(cfg); accts.accounts = [
        Account(handle="@a", account_id="1", platforms=[Platform.instagram], status=AccountStatus.active)]
    led.add_source(Source(id="s1", source_path=str(tmp_path/"s.mp4"), width=1080, height=1920))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5, reason="r",
                          state=MomentState.clipped))
    clip = Clip(id="c1", parent_id="m1", path=str(tmp_path/"c1.mp4"), aspect=Fmt.r9x16, state=ClipState.captioned)
    Path(clip.path).write_bytes(b"BASECLIP")
    clip.meta_captions = {"@a/instagram": {"caption": "A cap", "hashtags": [], "hook": "HOOK A"}}
    led.add_clip(clip)
    burn = mocker.patch("fanops.crosspost.overlay.burn_hook_only")
    from fanops.crosspost import crosspost_clips
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    p = next(iter(led.posts.values()))
    assert p.variant_key is None and p.variant_hook is None     # off -> today's behavior
    burn.assert_not_called()


from datetime import timedelta
from fanops.timeutil import parse_iso

def test_surface_time_lead_zero_is_byte_identical_to_no_lead():
    # The default lead=0 must produce the EXACT same string as today (determinism regression guard).
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    for i in range(6):
        a = surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1")
        b = surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=0)
        assert a == b

def test_surface_time_lead_shifts_every_time_by_exactly_the_constant():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    lead = 120
    for i in range(6):
        t0 = parse_iso(surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1"))
        tl = parse_iso(surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=lead))
        assert tl - t0 == timedelta(minutes=lead)   # constant shift, identical per index

def test_surface_time_lead_preserves_monotonicity():
    base = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    times = [surface_time(base, "@a", "instagram", "2026-06-02", index=i, clip_id="clip_1", lead_minutes=200)
             for i in range(12)]
    assert times == sorted(times) and len(set(times)) == len(times)

def test_crosspost_clips_applies_publish_lead_minutes(tmp_path, mocker, monkeypatch):
    # End-to-end: crosspost_clips must read cfg.publish_lead_minutes and pass it through, so a
    # post's scheduled_time is shifted by exactly the lead vs the no-lead run.
    base_time = "2026-06-02T18:00:00Z"
    def _run(lead_env):
        cfg = Config(root=tmp_path / lead_env)   # isolated root per run
        _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432",
                              "platforms": ["instagram"], "status": "active"}])
        led = Ledger.load(cfg); _captioned(led, cfg, mocker)
        if lead_env:
            monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", lead_env)
        else:
            monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)
        led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time=base_time)
        ig = [p for p in led.posts.values() if p.platform.value == "instagram"][0]
        return parse_iso(ig.scheduled_time)
    t_no = _run("")
    t_lead = _run("90")
    assert t_lead - t_no == timedelta(minutes=90)
