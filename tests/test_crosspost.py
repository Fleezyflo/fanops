import json, subprocess, sys, textwrap
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, Batch, ClipState, MomentState, Platform, Fmt, PostState
from fanops.accounts import Accounts
from fanops.crosspost import surface_time, crosspost_clips, _STEP_MIN, _JITTER_MAX
from fanops.ids import _hash

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _captioned(led, cfg, mocker):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    # one already-rendered 9:16 clip (instagram/youtube/tiktok reuse it now that youtube=Shorts 9:16;
    # a 16:9 surface like twitter renders 16:9 on demand)
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


def test_jitter_max_strictly_less_than_step_min_is_asserted_at_import():
    # MOL-69: the H1/H2 monotonicity invariant (index*_STEP + jitter monotonic in index) requires
    # _JITTER_MAX < _STEP_MIN. Pin the live relationship AND prove a module-level assert enforces it
    # at import — so inverting the constants (or deleting the assert) can never ship silently.
    assert _JITTER_MAX < _STEP_MIN                     # live invariant holds

    # Negative path: extract the constants-through-assert fragment from the module source, invert
    # _JITTER_MAX past _STEP_MIN, and exec it in isolation — the module-level assert MUST fire. If
    # the assert line were deleted, exec would NOT raise, so this pins its presence. RED.
    import re, pytest
    from fanops import crosspost
    src = Path(crosspost.__file__).read_text()
    m = re.search(r"^(_STEP_MIN = \d+.*?^assert _JITTER_MAX < _STEP_MIN.*?$)", src, re.M | re.S)
    assert m, "constants + module-level assert block not found in crosspost.py source"
    inverted = re.sub(r"_JITTER_MAX = 30\b", "_JITTER_MAX = 50", m.group(1), count=1)
    assert "_JITTER_MAX = 50" in inverted                # substitution applied
    with pytest.raises(AssertionError):
        exec(compile(inverted, "<mol69-probe>", "exec"), {})


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
    # right aspect per platform (FIX F20): IG 9:16, YouTube 9:16 (Shorts — was 16:9 long-form)
    assert by_plat[Platform.instagram].aspect is Fmt.r9x16
    assert by_plat[Platform.youtube].aspect is Fmt.r9x16
    # staggered
    assert by_plat[Platform.instagram].scheduled_time != by_plat[Platform.youtube].scheduled_time

def test_crosspost_stamps_created_at(tmp_path, mocker):
    # content-lifecycle Phase 2: a born post carries a wall-clock AWARE created_at (the birth-day anchor).
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    p = next(iter(led.posts.values()))
    assert p.created_at and parse_iso(p.created_at).tzinfo is not None

def test_crosspost_one_handle_two_platforms_distinct_per_platform_ids(tmp_path, mocker):
    # M1: a SINGLE multi-platform handle whose channels are different Postiz integrations -> each
    # platform's Post carries its OWN integration id, not one shared id (the mis-routing fix). No
    # crosspost.py change needed: surfaces() now sources the id per-platform.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "fallback",
                          "platforms": ["instagram", "youtube"], "status": "active",
                          "integrations": {"instagram": "ig_intg", "youtube": "yt_intg"}}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    by_plat = {p.platform: p for p in led.posts.values()}
    assert by_plat[Platform.instagram].account_id == "ig_intg"   # IG -> its own integration
    assert by_plat[Platform.youtube].account_id == "yt_intg"     # YouTube -> a DIFFERENT integration
    assert by_plat[Platform.instagram].account_id != by_plat[Platform.youtube].account_id

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

def test_stitch_draft_clip_is_structurally_unpostable(tmp_path, mocker):
    # M3 safety spine: a clip born in stitch_draft is UNSELECTABLE by crosspost_clips — it is neither
    # `captioned` (the selection state) nor in _REUSABLE_CLIP_STATES (the render-reuse allowlist). The
    # posting query literally cannot reach it until an operator approval transitions it to captioned.
    from fanops.crosspost import _REUSABLE_CLIP_STATES
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)
    led.clips["clip_1"].state = ClipState.stitch_draft         # born pre-approval
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert [p for p in led.posts.values()] == []               # ZERO posts (structurally unpostable)
    assert ClipState.stitch_draft not in _REUSABLE_CLIP_STATES  # never reused as a render target either

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

# ---- Account-First Studio: the per-surface batch-target SKIP (casting-OFF enforcement) + denormalization ----
def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

def _two_accounts_clip(cfg, *, source_batch_id=None):
    _seed_accounts(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "youtube"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "youtube"], "status": "active"},
    ])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, batch_id=source_batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {
        "@a/instagram": {"caption": "a ig", "hashtags": []}, "@a/youtube": {"caption": "a yt", "hashtags": []},
        "@b/instagram": {"caption": "b ig", "hashtags": []}, "@b/youtube": {"caption": "b yt", "hashtags": []},
    }
    led.add_clip(clip)
    return led

def test_crosspost_batch_target_skips_off_target_surfaces(tmp_path, mocker):
    # A source ingested under a batch targeting ONLY @a: posts are born for @a's surfaces only (the
    # casting-OFF enforcement path) and each carries the denormalized batch_id; @b is skipped, not posted.
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id="batch_x")
    led.add_batch(Batch(id="batch_x", name="launch", target_accounts=["@a"]))
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"}          # @b surfaces skipped (not in target)
    assert len(led.posts) == 2                                        # @a x {instagram, youtube}
    assert all(p.batch_id == "batch_x" for p in led.posts.values())  # denormalized onto the Post
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "batch_target_skip" in log and "@b" in log                # off-target skip left a breadcrumb (mirrors skipped_surface)

def test_crosspost_emits_batch_target_summary_count(tmp_path, mocker):
    # Face 1-fu (T5): a batched clip emits ONE structured exclusion summary the surfaces can read. The
    # excluded surfaces become no Post, so this run-log line is the ONLY persistent record of "N excluded".
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id="batch_x")            # 4 surfaces: @a/@b x ig/yt
    led.add_batch(Batch(id="batch_x", name="launch", target_accounts=["@a"]))   # targets @a -> @b's 2 skip
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert log.count("batch_target_summary") == 1                       # exactly one summary per clip
    assert "skipped=2" in log and "kept=2" in log                      # @b's 2 surfaces excluded, @a's 2 kept

def test_crosspost_unbatched_emits_no_summary(tmp_path, mocker):
    # An unbatched clip (tgt == []) emits NO batch_target_summary line (byte-identity gate).
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "batch_target_summary" not in log

def test_crosspost_unbatched_fans_to_all_with_none_batch(tmp_path, mocker):
    # No batch (source.batch_id is None) => byte-identical fan-out to all 4 surfaces, batch_id None.
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 4 and all(p.batch_id is None for p in led.posts.values())

def test_crosspost_empty_target_batch_fans_to_all(tmp_path, mocker):
    # A batch with target_accounts == [] is the ALL-ACTIVE sentinel: no skip, fans to all 4, batch stamped.
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id="batch_all")
    led.add_batch(Batch(id="batch_all", name="everyone", target_accounts=[]))
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 4 and all(p.batch_id == "batch_all" for p in led.posts.values())

def test_crosspost_affinity_skips_off_affinity_surfaces(tmp_path, mocker, monkeypatch):
    # Face 3: with casting ON, a cast moment (affinities=['@a']) fans ONLY to @a's surfaces — the affinity skip
    # composes with the batch-target skip; an uncast moment ([] affinities) fans to all (byte-identical).
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    led.moments["mom_1"].affinities = ["@a"]
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"} and len(led.posts) == 2

def test_crosspost_affinity_ignored_when_casting_off(tmp_path, mocker, monkeypatch):
    # A2 (kill-switch): casting OFF (now an EXPLICIT off-word — the flag DEFAULTS ON) IGNORES persisted
    # affinities and fans to ALL surfaces, even on a ledger already cast in a prior pass. "Off" is fully off —
    # the crosspost affinity skip is gated on cfg.account_casting, not just on the presence of m.affinities.
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    led.moments["mom_1"].affinities = ["@a"]          # a prior cast pass stamped this
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a", "@b"} and len(led.posts) == 4   # cast ignored when OFF

def test_crosspost_cast_moment_fans_only_to_its_account(tmp_path, mocker, monkeypatch):
    # The affinity gate (Face 3): a CAST moment (affinities==['@a']) fans ONLY to @a, never to @b.
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    led.moments["mom_1"].affinities = ["@a"]
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"} and len(led.posts) == 2

def test_crosspost_uncast_moment_fans_to_all(tmp_path, mocker, monkeypatch):
    # An UNCAST moment (affinities==[]) — casting evaluated it but assigned no one — falls through and fans to
    # ALL surfaces (the only routing now; byte-identical to the shipped Face 3 budget-mode behavior).
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    led = _two_accounts_clip(cfg, source_batch_id=None)
    led.moments["mom_1"].affinities = []
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(led.posts) == 4                        # uncast fans to all


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
    # Only a 9:16 clip exists; twitter needs 16:9 -> a NEW clip is created (rendered, file exists).
    # (youtube is now 9:16 Shorts and would reuse the clip; twitter is the surviving 16:9 surface.)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["twitter"], "status": "active"}])
    led = Ledger.load(cfg); _captioned(led, cfg, mocker)   # seeds one 9:16 clip
    led.clips["clip_1"].meta_captions["@a/twitter"] = {"caption": "tw cap", "hashtags": []}
    assert len(led.clips) == 1
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    # a 16:9 clip was rendered on demand
    aspects = {c.aspect for c in led.clips.values()}
    assert Fmt.r16x9 in aspects and len(led.clips) == 2
    tw_post = next(p for p in led.posts.values() if p.platform is Platform.twitter)
    assert tw_post.parent_id != "clip_1"                    # points at the new 16:9 clip
    assert led.clips[tw_post.parent_id].state is ClipState.rendered

def test_crosspost_does_not_reuse_error_clip_and_no_post_for_failed_render(tmp_path, mocker):
    # FIX: a failed on-demand render must NOT yield a post pointing at a dangling file,
    # and the error clip must not be reused as a render target.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["twitter"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c_9x16.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"@a/twitter": {"caption": "tw", "hashtags": []}}    # twitter=16:9 -> on-demand render
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
    # instagram cap (90s) but UNDER tiktok's (600s). The over-cap surface must get NO queued post
    # while the under-cap surface still posts — per-surface SKIP, not all-or-nothing-wedged.
    # (youtube's cap is now 180s for Shorts, so instagram is the over-cap surface at 120s.)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1",
                          "platforms": ["tiktok", "instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, duration=300.0))
    # 120s window: > instagram cap (90), < tiktok cap (600)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-120", start=0.0, end=120.0,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    # captions present for BOTH surfaces so a post would otherwise be created for each
    clip.meta_captions = {"@a/tiktok": {"caption": "tt cap", "hashtags": []},
                          "@a/instagram": {"caption": "ig cap", "hashtags": []}}
    led.add_clip(clip)
    def fake_run(cmd, **kw):   # both surfaces are 9:16 (reuse the clip); harmless render stub
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
    assert Platform.instagram not in plats, "over-cap surface (instagram 90s) must be skipped for a 120s clip"
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
    # ROOT FIX: the per-account on-screen hooks are the FRAME-SEEING moment author's, keyed by handle
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5, reason="r",
                          state=MomentState.clipped, hook="default hook",
                          hooks_by_persona={"@a": "HOOK A", "@b": "HOOK B"}))
    # a captioned base clip; the caption gate writes NO hook now — a sentinel proves crosspost IGNORES it
    clip = Clip(id="c1", parent_id="m1", path=str(tmp_path/"c1.mp4"), aspect=Fmt.r9x16,
                state=ClipState.captioned)
    Path(clip.path).write_bytes(b"BASECLIP")
    clip.meta_captions = {"@a/instagram": {"caption": "A cap", "hashtags": [], "hook": "CAPHOOK_IGNORED"},
                          "@b/instagram": {"caption": "B cap", "hashtags": [], "hook": "CAPHOOK_IGNORED"}}
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
    # each account got a DIFFERENT variant_hook + variant_key; slice 2 (burn on approval) DEFERS the ffmpeg
    # burn to approval, so the mint RECORDS the per-account intent but runs NO burn and mints no Render.
    assert by_acct["@a"].variant_hook == "HOOK A" and by_acct["@b"].variant_hook == "HOOK B"
    assert by_acct["@a"].variant_key and by_acct["@a"].variant_key != by_acct["@b"].variant_key
    assert calls == [] and led.renders == {}                          # mint defers the burn (no ffmpeg, no Render)
    assert all(p.render_id is None and p.media_urls == [] for p in posts)
    # DETERMINISM (pinned in-test, not only by construction): variant_key MUST be the
    # content-addressed surface_key — a future swap to random/uuid (distinct but non-reproducible,
    # the #1 v1 duplicate-post bug) would still pass the distinctness check above, so assert the
    # exact content-addressed value here.
    from fanops.ids import surface_key
    assert by_acct["@a"].variant_key == surface_key("@a", "instagram")
    assert by_acct["@b"].variant_key == surface_key("@b", "instagram")


def test_recrosspost_rewrites_stale_hook_on_awaiting_post_only(tmp_path, monkeypatch, mocker):
    # M2 (audit): the post id is content-addressed on (clip, surface), NOT the per-account hook, so a
    # re-decision that changes this account's hook can't mint a superseding post — add_post's first-write-wins
    # would keep a STALE hook and the operator would review/approve the old one. crosspost must rewrite the
    # still-AWAITING post's variant_hook in place; a QUEUED/approved post must be left untouched.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hooks_by_persona={"@a": "HOOK A"}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "cap", "hashtags": ["#x"]}}
    led.add_clip(clip); led.save()

    def _burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"V"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=_burn)

    def _run():
        ld = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
        ld.save(); return ld

    led = _run()
    posts = list(led.posts.values())
    assert len(posts) == 1 and posts[0].variant_hook == "HOOK A"
    pid = posts[0].id

    def _redecide(hook):                                          # a re-caption resets the clip + changes this account's hook
        ld = Ledger.load(cfg)
        ld.moments["mom_1"] = ld.moments["mom_1"].model_copy(update={"hooks_by_persona": {"@a": hook}})
        ld.set_clip_state("clip_1", ClipState.captioned); ld.save()

    _redecide("HOOK B"); led = _run()
    assert len(led.posts) == 1                                    # SAME post (content-addressed) — not duplicated
    assert led.posts[pid].variant_hook == "HOOK B"               # rewritten in place — never a stale hook

    from fanops.studio.actions_approve import approve_posts       # now APPROVE, then re-decide -> queued must NOT change
    approve_posts(cfg, [pid])
    assert Ledger.load(cfg).posts[pid].state is PostState.queued
    _redecide("HOOK C"); _run()
    assert Ledger.load(cfg).posts[pid].variant_hook == "HOOK B"  # queued post keeps the approved hook (untouched)

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

def test_crosspost_logs_skipped_surface_missing_caption(tmp_path, mocker):
    # FIX 1: when a surface has no caption (clip captioned for some surfaces but not this one), the
    # surface is silently continue'd — in an autonomous run that drops a real post with no trace.
    # crosspost_clips must emit a `skipped_surface` log breadcrumb before skipping.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "98432",
                          "platforms": ["instagram", "youtube"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "ig cap", "hashtags": []}}   # NO youtube caption
    led.add_clip(clip)
    real_run = subprocess.run
    def fake_run(cmd, **kw):
        if not (isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg"): return real_run(cmd, **kw)
        out = Path(cmd[-1])
        if not str(cmd[-1]).startswith("-"):
            out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = [p for p in led.posts.values() if led.clips[p.parent_id].parent_id == "mom_1"]
    assert {p.platform.value for p in posts} == {"instagram"}   # only IG posted, YT skipped
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "skipped_surface" in log and "youtube" in log       # the skip left a breadcrumb


def test_crosspost_stamps_creative_provenance_onto_post(tmp_path, mocker, monkeypatch):
    # P1 T3b: the Post carries the attribution key P3 aggregates reach by —
    # first_frame_kind + cut_seconds (clip), clip_profile (the global video-type knob, stamped here).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")   # M3d: this tests post provenance on the OFF (shared-clip) path; a fake clip path isn't readable for an ON render
    monkeypatch.setenv("FANOPS_CLIP_PROFILE", "song")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1",
                          "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-18", start=0, end=18,
                          reason="r", state=MomentState.clipped, hook="wait for the drop"))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned, first_frame_kind="visual", cut_seconds=18.0)
    clip.meta_captions = {"@a/instagram": {"caption": "ig cap", "hashtags": ["#x"]}}
    led.add_clip(clip)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    post = list(led.posts.values())[0]
    assert post.first_frame_kind == "visual"
    assert post.cut_seconds == 18.0
    assert post.clip_profile == "song"


def test_crosspost_stamps_variation_axis_from_caption(tmp_path, mocker, monkeypatch):
    # P2 T3: the per-surface variant's declared axis is stamped onto its Post (variation_axis) so P3 can
    # attribute reach by the axis a variation moved. P2 writes variation_axis.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1",
                          "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-18", start=0, end=18,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "ig cap", "hashtags": ["#x"],
                                           "hook": "wait for the drop", "axis": "hook_string",
                                           "rationale": "different opening words"}}
    led.add_clip(clip)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    post = list(led.posts.values())[0]
    assert post.variation_axis == "hook_string"


def test_stitch_draft_clip_never_crossposts(tmp_path, mocker):
    # M4 structural operator-gate: a stitch_draft clip in EVERY pre-release state is absent from the
    # crosspost selection predicate -> crosspost_clips creates ZERO posts for it (the M3 guarantee, asserted
    # for an impact-cut-born clip). Only the explicit operator RELEASE (-> captioned) makes it postable.
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r", state=MomentState.clipped))
    stitch = Clip(id="stitch_x", parent_id="mom_1", path="/stitch_x.mp4", aspect=Fmt.r9x16,
                  state=ClipState.stitch_draft)
    stitch.meta_captions = {"@a/instagram": {"caption": "c", "hashtags": ["#x"]}}   # even WITH captions
    led.add_clip(stitch)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert not any(p.parent_id == "stitch_x" for p in led.posts.values())           # structurally unpostable
