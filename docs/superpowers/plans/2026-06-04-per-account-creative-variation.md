# Per-Account Creative Variation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each fan account a genuinely different caption + burned-in hook per clip so the existing `track → analyzed → adjust` lift loop can attribute which creative treatment wins per audience (observe-only v1 — surfaced in the digest, no auto-propagation).

**Architecture:** Variants are decided at caption time (per-(account,platform) caption AND hook from the caption agent) and realized at crosspost time (a cheap second ffmpeg pass burns each account's hook onto the SHARED reframed+subtitled base clip → a per-account output file; the Post stamps a deterministic `variant_key` + the hook). Purely additive + fail-open: gated by `FANOPS_CREATIVE_VARIATION` (default OFF); with it off, or no per-surface hook, or no ffmpeg text filter → today's shared-clip behavior. v1 touches NONE of the `amplify`/`classify_outcomes` machinery (which has a C1 cascade-delete bug history).

**Tech Stack:** Python 3.12, pydantic models, ffmpeg (ffmpeg-full / libass — burned-in text), SHA content-addressed ids (`ids.surface_key`/`_hash`), pytest + pytest-mock + pytest-timeout, ruff.

**Spec:** `docs/superpowers/specs/2026-06-04-per-account-creative-variation-design.md`

**Baseline:** `main` @ the latest commit, suite **338 passed, 1 skipped**, `ruff check src/` green. Work in a fresh worktree off `main` with its own python3.12 venv (`pip install -e ".[dev]"`). Run every test with `source .venv/bin/activate && python -m pytest ...`.

---

### Task 1: Model fields — per-surface caption hook + Post variant attribution

**Files:**
- Modify: `src/fanops/models.py` (CaptionItem ~L171, Post ~L93-108)
- Test: `tests/test_models.py` (create if absent; else append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py — ADD (create file with: from fanops.models import CaptionItem, Post, Platform, PostState  if new)
def test_caption_item_has_optional_hook():
    from fanops.models import CaptionItem
    item = CaptionItem(surface="@a/instagram", caption="x", hashtags=[], language="en", hook="WATCH THIS")
    assert item.hook == "WATCH THIS"
    # optional: old payloads without hook still validate
    assert CaptionItem(surface="@a/instagram", caption="x").hook is None

def test_post_has_optional_variant_fields():
    from fanops.models import Post, Platform, PostState
    p = Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
             caption="x", state=PostState.queued, variant_key="vk1", variant_hook="WATCH THIS")
    assert p.variant_key == "vk1" and p.variant_hook == "WATCH THIS"
    # old ledgers (no variant fields) still load
    p2 = Post(id="p2", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
              caption="x", state=PostState.queued)
    assert p2.variant_key is None and p2.variant_hook is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_models.py -k "hook or variant" -v`
Expected: FAIL — `CaptionItem`/`Post` have no `hook`/`variant_key`/`variant_hook` (pydantic rejects the unknown kwarg).

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/models.py — in class CaptionItem, after the `language` field (~L175):
    hook: Optional[str] = None          # per-surface on-screen hook (creative variation); None -> use moment default

# src/fanops/models.py — in class Post, after the `metrics` field (~L108):
    variant_key: Optional[str] = None   # creative-variation attribution: deterministic per-(account,platform,clip) key
    variant_hook: Optional[str] = None  # the burned-in hook text this account's variant used (observe-only)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_models.py -k "hook or variant" -v && python -m pytest -q`
Expected: PASS; full suite still 338+2 (no regression — fields are optional).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/models.py tests/test_models.py
git commit -m "feat (variation 1): CaptionItem.hook + Post.variant_key/variant_hook (optional; old ledgers load)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `overlay.burn_hook_only` — cheap per-account hook overlay pass

**Files:**
- Modify: `src/fanops/overlay.py` (add function; reuse the existing ASS HOOK style + `ffmpeg_has_textfilter`/`subtitles_vf`)
- Test: `tests/test_overlay.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_overlay.py — ADD
def test_burn_hook_only_builds_hook_ass_and_cmd(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"VARIANT")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.overlay.subprocess.run", side_effect=fake_run)
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920, font="Arial Unicode MS")
    assert ok is True and out.exists()
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "subtitles=" in vf                      # the hook is burned via an ass
    assert captured["cmd"][-1] == str(out)         # output is last (matches fake_run + clip.py convention)
    # a .ass containing the hook text was written next to the output
    ass = list(tmp_path.glob("*.ass"))
    assert ass and "WATCH THIS" in ass[0].read_text()

def test_burn_hook_only_failopen_when_no_textfilter(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=False)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    ran = mocker.patch("fanops.overlay.subprocess.run")
    ok = overlay.burn_hook_only(str(base), str(out), "WATCH THIS", width=1080, height=1920)
    assert ok is False                              # fail-open: signalled no burn
    assert out.exists() and out.read_bytes() == b"BASE"   # output is a copy of the base, unchanged
    ran.assert_not_called()                         # no ffmpeg invoked

def test_burn_hook_only_failopen_when_hook_empty(tmp_path, mocker):
    import fanops.overlay as overlay
    overlay._TEXTFILTER_CACHE = None
    mocker.patch("fanops.overlay.ffmpeg_has_textfilter", return_value=True)
    base = tmp_path / "base.mp4"; base.write_bytes(b"BASE")
    out = tmp_path / "variant.mp4"
    ran = mocker.patch("fanops.overlay.subprocess.run")
    ok = overlay.burn_hook_only(str(base), str(out), "", width=1080, height=1920)
    assert ok is False and out.exists() and out.read_bytes() == b"BASE"
    ran.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_overlay.py -k burn_hook_only -v`
Expected: FAIL — `overlay.burn_hook_only` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/overlay.py — ADD (uses the existing build_ass/write_ass/subtitles_vf/ffmpeg_has_textfilter).
# Build a HOOK-ONLY ass: reuse build_ass with NO transcript segments and the hook spanning the
# whole clip's head — but since burn_hook_only runs on an already-cut base clip, the clip window is
# [0, <base duration>]; we pass a generous clip_end so build_ass emits the hook for the first ~2.5s.
import shutil

def burn_hook_only(base_clip_path: str, out_path: str, hook: str, *,
                   width: int = 1080, height: int = 1920, font: str = "Arial Unicode MS") -> bool:
    """Burn ONLY a hook (top-third) onto an already-rendered base clip -> out_path. Returns True if
    the hook was burned, False if it FAILED OPEN (no text filter or empty hook) — in which case
    out_path is a byte copy of the base clip (the caller still gets a usable per-account file).
    Cheap second pass for per-account creative variation: the base reframe+subtitle render is done
    once; this adds one account's hook."""
    if not hook or not hook.strip() or not ffmpeg_has_textfilter():
        shutil.copyfile(base_clip_path, out_path)        # fail-open: usable file, no hook
        return False
    # hook-only ass: no subtitle segments, hook over the first 2.5s of the (already-cut) base clip.
    ass_text = build_ass([], hook=hook, clip_start=0.0, clip_end=2.5, width=width, height=height, font=font)
    ass_path = str(Path(out_path).with_suffix(".ass"))
    write_ass(ass_text, ass_path)
    cmd = ["ffmpeg", "-y", "-i", base_clip_path, "-vf", subtitles_vf(ass_path),
           "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", out_path]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        shutil.copyfile(base_clip_path, out_path)        # ffmpeg vanished mid-run: fail-open
        return False
    if r.returncode != 0 or not Path(out_path).exists():
        shutil.copyfile(base_clip_path, out_path)
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_overlay.py -q && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/overlay.py tests/test_overlay.py
git commit -m "feat (variation 2): overlay.burn_hook_only — cheap per-account hook overlay on a shared base clip (fail-open)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Caption agent emits a per-surface hook; `ingest_captions` stores it

**Files:**
- Modify: `src/fanops/prompts.py` (`caption_prompt` ~L32 — ask for a per-surface `hook`)
- Modify: `src/fanops/caption.py` (`ingest_captions` ~L129 — store the hook into `meta_captions`)
- Test: `tests/test_caption.py` (append), `tests/test_prompts.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts.py — ADD
def test_caption_prompt_asks_for_per_surface_hook():
    from fanops.prompts import caption_prompt
    p = caption_prompt({"clip_id": "c1", "transcript_excerpt": "they slept on me",
                        "language": "en", "guidance": "",
                        "surfaces": [{"surface": "@a/instagram", "platform": "instagram"}]})
    assert "hook" in p.lower()        # the prompt instructs the model to return a per-surface hook

# tests/test_caption.py — ADD
def test_ingest_captions_stores_per_surface_hook(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, MomentState, ClipState, CaptionSet, CaptionItem
    from fanops.agentstep import write_request, write_response_path  # write_response_path: see note
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5,
                          reason="r", state=MomentState.decided))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.captions_requested))
    from fanops import caption as capmod
    led = capmod.request_captions(led, cfg, "c1", [("@a", __import__("fanops.models", fromlist=["Platform"]).Platform.instagram)])
    # write a response carrying a hook
    import json
    from fanops.agentstep import request_path, response_path
    rid = json.loads(request_path(cfg, "captions", "c1").read_text())["request_id"]
    resp = {"request_id": rid, "items": [{"surface": "@a/instagram", "caption": "they slept on me, watch",
            "hashtags": ["#x"], "language": "en", "hook": "THEY SLEPT ON ME"}]}
    response_path(cfg, "captions", "c1").write_text(json.dumps(resp))
    led = capmod.ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].meta_captions["@a/instagram"]["hook"] == "THEY SLEPT ON ME"
```

> **Note for the implementer:** check `src/fanops/agentstep.py` for the exact helper names to write a response file (e.g. `response_path`); match the pattern other caption tests use (grep `tests/test_caption.py` for how they write a response). The test above uses `response_path(cfg, "captions", clip_id)`. If the helper differs, adjust the test to the real API before running — do NOT invent a helper.

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_prompts.py -k hook tests/test_caption.py -k per_surface_hook -v`
Expected: FAIL — prompt doesn't mention a hook; `meta_captions[surface]` has no `hook` key.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/prompts.py — in caption_prompt, ADD to the HARD RULES block (the bulleted rules ~L44):
        "  - ALSO return a short on-screen `hook` per item: a punchy <=7-word line (same language "
        "as the caption) that grabs attention in the first 2 seconds. Make each surface's hook "
        "GENUINELY DIFFERENT (different angle/words) — these are A/B creative variants per account. "
        "If you cannot, omit `hook` and a default will be used.\n"

# src/fanops/caption.py — in ingest_captions, the loop body that stores the caption (~L129),
# change the stored dict to include the hook (CaptionItem.hook is optional -> may be None):
        clip.meta_captions[item.surface] = {"caption": item.caption, "hashtags": item.hashtags,
                                            "hook": item.hook}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_prompts.py tests/test_caption.py -q && python -m pytest -q`
Expected: PASS; full suite green (existing caption tests unaffected — `hook` is additive; existing `meta_captions` readers that only read `caption`/`hashtags` keep working).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/prompts.py src/fanops/caption.py tests/test_prompts.py tests/test_caption.py
git commit -m "feat (variation 3): caption agent returns a per-surface hook; ingest_captions stores it in meta_captions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `FANOPS_CREATIVE_VARIATION` config toggle (default OFF)

**Files:**
- Modify: `src/fanops/config.py` (add property, mirror `burn_subs`)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — ADD
def test_creative_variation_defaults_off_and_respects_env(tmp_path, monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_CREATIVE_VARIATION", raising=False)
    assert Config(root=tmp_path).creative_variation is False           # default OFF (opt-in)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "true")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    assert Config(root=tmp_path).creative_variation is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -k creative_variation -v`
Expected: FAIL — no `creative_variation` property.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/config.py — ADD a property (note: DEFAULT OFF, opposite of burn_subs which defaults on):
    @property
    def creative_variation(self) -> bool:
        v = (os.getenv("FANOPS_CREATIVE_VARIATION") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -k creative_variation -v && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/config.py tests/test_config.py
git commit -m "feat (variation 4): FANOPS_CREATIVE_VARIATION toggle (default OFF, opt-in)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Crosspost wires per-account variant (clip + variant_key + variant_hook)

**Files:**
- Modify: `src/fanops/crosspost.py` (the surface loop in `crosspost_clips` ~L69-98)
- Test: `tests/test_crosspost.py` (append)

**Context the implementer must read first:** `crosspost.py` lines 69-98 — per surface it resolves `target_clip` (the shared per-aspect clip), reads `cap = clip.meta_captions[surface]`, builds `pid = child_id("post", target_clip.id, skey)`, and creates the `Post`. The variant wiring inserts: when `cfg.creative_variation` AND `cap.get("hook")`, produce a per-account variant file from `target_clip.path` via `overlay.burn_hook_only`, point the Post's media at it, and stamp `variant_key`/`variant_hook`. `Post.media_urls` is the field the poster uploads (grep `media_urls`/`ensure_clip_media` to confirm how the clip file reaches the poster — the variant must flow through the SAME seam; if the poster uploads `clip.path` via the clip id, the variant needs its own clip record OR the Post must carry the variant path). **Determinism:** `variant_key = surface_key(surf.account, surf.platform.value)` (already content-addressed) — do NOT use `random`/`hash()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crosspost.py — ADD (match the file's existing Source/Moment/Clip/Accounts fixtures)
def test_crosspost_creates_per_account_variant_when_enabled(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.accounts import Accounts, Account, AccountStatus
    from fanops.models import Source, Moment, Clip, MomentState, ClipState, Fmt, Platform
    import fanops.overlay as overlay
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # two active accounts, same platform -> same aspect, so they'd share a clip today
    accts = Accounts(); accts.accounts = [
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

def test_crosspost_no_variant_when_disabled(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_CREATIVE_VARIATION", raising=False)   # OFF
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.accounts import Accounts, Account, AccountStatus
    from fanops.models import Source, Moment, Clip, MomentState, ClipState, Fmt, Platform
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = Accounts(); accts.accounts = [
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_crosspost.py -k "variant" -v`
Expected: FAIL — crosspost ignores variation; `overlay` not imported there; Posts have no `variant_key`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/crosspost.py — at top, ADD:
from fanops import overlay
from fanops.models import PLATFORM_ASPECT, Fmt   # (Fmt already imported; ensure available)

# In crosspost_clips, the per-surface block (~after `caption = cap["caption"]`, before add_post),
# REPLACE the media/clip selection so an enabled variant burns a per-account file + stamps the Post.
# Read the CURRENT add_post(...) call (it sets parent_id=target_clip.id, caption=..., etc.) and
# extend it. Concretely:
            variant_key = None
            variant_hook = None
            media_path = target_clip.path
            hook_v = (cap.get("hook") if isinstance(cap, dict) else None)
            if cfg.creative_variation and hook_v:
                # cheap per-account overlay on the shared base clip; deterministic per-account file id
                variant_key = surface_key(surf.account, surf.platform.value)
                tw, th = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080),
                          Fmt.r16x9: (1920, 1080)}.get(aspect, (1080, 1920))
                vpath = str(cfg.clips / f"{target_clip.id}_{_hash('variant', variant_key)}.mp4")
                overlay.burn_hook_only(target_clip.path, vpath, hook_v, width=tw, height=th,
                                       font=cfg.subtitle_font)   # fail-open: vpath always exists
                media_path = vpath
                variant_hook = hook_v
            led.add_post(Post(
                id=pid, parent_id=target_clip.id, state=PostState.queued,
                account=surf.account, account_id=surf.account_id, platform=surf.platform,
                caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
                scheduled_time=sched,
                media_urls=[f"file://{media_path}"] if cfg.creative_variation and hook_v else [],
                submission_id=f"fanops_{_hash('idemp', pid)}",
                variant_key=variant_key, variant_hook=variant_hook))
```

> **Implementer note:** verify how the clip file currently reaches the poster. Grep `media_urls`, `ensure_clip_media`, `clip.media_url` in `src/fanops/post/`. If the poster derives the upload from `target_clip.media_url`/`clip.path` (NOT `Post.media_urls`), then pointing the variant requires either (a) the Post to carry the variant path in a field the poster reads, or (b) a per-account Clip record. Pick the option that flows through the EXISTING upload seam with the smallest change, and adjust the `media_urls=` line above to match the real field the poster reads. Keep `dst` last in any ffmpeg cmd. Do NOT break the existing crosspost tests (run them).

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_crosspost.py -q && python -m pytest -q`
Expected: PASS; full suite green (the existing crosspost tests run with variation OFF by default → unchanged path).

- [ ] **Step 5: Commit**

```bash
git add src/fanops/crosspost.py tests/test_crosspost.py
git commit -m "feat (variation 5): crosspost burns a per-account hook variant + stamps Post.variant_key/variant_hook (deterministic; gated; fail-open)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Digest — "Lift by variant" section

**Files:**
- Modify: `src/fanops/digest.py` (`render_digest` — add a section after "Published but unmeasured")
- Test: `tests/test_digest.py` (append; create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest.py — ADD (grep the file for how it builds a ledger; match that style)
def test_digest_shows_lift_by_variant(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, variant_key="vk_a", variant_hook="HOOK A",
                      metrics={"lift_score": 80.0}))
    led.add_post(Post(id="p2", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram,
                      caption="y", state=PostState.analyzed, variant_key="vk_b", variant_hook="HOOK B",
                      metrics={"lift_score": 30.0}))
    out = render_digest(led, cfg)
    assert "Lift by variant" in out
    assert "HOOK A" in out and "80" in out          # the winning variant + its lift surface
    assert "HOOK B" in out

def test_digest_no_variant_section_when_none(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path)
    out = render_digest(Ledger.load(cfg), cfg)
    assert "Lift by variant" not in out             # absent when no variant posts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest.py -k variant -v`
Expected: FAIL — no "Lift by variant" section.

- [ ] **Step 3: Write minimal implementation**

```python
# src/fanops/digest.py — in render_digest, ADD after the `unmeasured` block (~L56), before `awaiting`:
    # Creative-variation observability (v1): rank analyzed posts that carry a variant by lift_score,
    # so the operator sees which per-account creative treatment performs. Observe-only — no automated
    # propagation (that touches the amplify machinery, deferred).
    variant_posts = [p for p in led.posts.values()
                     if p.variant_key and p.state is PostState.analyzed and "lift_score" in p.metrics]
    if variant_posts:
        rows = sorted(variant_posts, key=lambda p: p.metrics.get("lift_score", 0.0), reverse=True)
        lines = [f"- `{p.variant_hook or p.variant_key}` ({p.account}/{p.platform.value}): "
                 f"lift {p.metrics.get('lift_score', 0.0)}" for p in rows]
        out.append("\n## Lift by variant (which creative is winning)\n" + "\n".join(lines) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest.py -q && python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/fanops/digest.py tests/test_digest.py
git commit -m "feat (variation 6): digest 'Lift by variant' section — observe-only attribution of which creative wins

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Integration — real per-account variant render + docs

**Files:**
- Test: `tests/integration/test_variation_render.py` (create; marked `integration`, like `test_e2e_real.py`)
- Modify: `MohFlow-FanOps/00_control/RUNTIME.md` (mark (j) status + document `FANOPS_CREATIVE_VARIATION`), `README.md`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_variation_render.py — CREATE
import subprocess, shutil
from pathlib import Path
import pytest
import fanops.overlay as overlay

REQUIRE = pytest.mark.integration

@REQUIRE
def test_two_accounts_get_distinct_burned_hooks(tmp_path):
    if not overlay.ffmpeg_has_textfilter():
        pytest.skip("ffmpeg lacks text filters (libass) — burned-hook variation not provable here")
    # a real base clip
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=navy:s=720x1280:d=4", "-f", "lavfi", "-i", "sine=frequency=300:d=4",
                    "-shortest", str(base), "-y"], check=True)
    out_a = tmp_path / "a.mp4"; out_b = tmp_path / "b.mp4"
    ok_a = overlay.burn_hook_only(str(base), str(out_a), "HOOK ALPHA", width=720, height=1280)
    ok_b = overlay.burn_hook_only(str(base), str(out_b), "HOOK BETA", width=720, height=1280)
    assert ok_a and ok_b and out_a.exists() and out_b.exists()
    # the two per-account files DIFFER from each other and from the base (different burned text)
    assert out_a.read_bytes() != out_b.read_bytes()
    assert out_a.stat().st_size != base.stat().st_size
    # OCR proof if tesseract is available (else the differ proof above stands)
    if shutil.which("tesseract"):
        fa = tmp_path / "fa.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(out_a),
                        "-frames:v", "1", str(fa), "-y"], check=True)
        txt = subprocess.run(["tesseract", str(fa), "-", "--psm", "6"], capture_output=True, text=True).stdout.upper()
        assert "ALPHA" in txt
```

- [ ] **Step 2: Run test to verify it fails (or skips cleanly without the toolchain)**

Run: `source .venv/bin/activate && FANOPS_REQUIRE_E2E= python -m pytest tests/integration/test_variation_render.py -v`
Expected: with ffmpeg-full present → it RUNS and currently FAILS only if `burn_hook_only` is broken (by Task 2 it passes); on a stripped ffmpeg → SKIP. (This task's value is the real-render proof; it should PASS once Tasks 1-2 are in.)

- [ ] **Step 3: Implementation = docs (the render code already exists from Task 2)**

```text
# MohFlow-FanOps/00_control/RUNTIME.md — §Backlog: change item (j) from open to:
- **(j) Per-account creative variation — v1 DONE (observe-only).** With FANOPS_CREATIVE_VARIATION=1,
  each active account gets a genuinely different caption + burned-in on-screen hook per clip (the
  caption agent returns a per-surface hook; crosspost burns it onto the shared base clip via a cheap
  per-account overlay pass). The `track → analyzed → adjust` lift loop already attributes per-post;
  the digest's "Lift by variant" section shows which creative wins. Default OFF (opt-in). Fail-open:
  no hook / no libass / toggle off -> today's shared-clip behavior. Auto-propagating winners into
  amplify is a documented follow-up (touches the C1-risk machinery; needs real lift-by-variant data).
# Also ADD FANOPS_CREATIVE_VARIATION to the env table.

# README.md — note: set FANOPS_CREATIVE_VARIATION=1 for per-account A/B creative (caption + hook);
# the digest reports lift-by-variant.
```

- [ ] **Step 4: Run the full suite + ruff + the real render**

Run: `source .venv/bin/activate && python -m pytest -q && ruff check src/ && python -m pytest tests/integration/test_variation_render.py -v`
Expected: full suite green (338 + the variation tests); ruff clean; the integration render PASSES (ffmpeg-full present).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_variation_render.py MohFlow-FanOps/00_control/RUNTIME.md README.md
git commit -m "feat (variation 7): real per-account variant render integration test + docs (mark (j) v1 DONE)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** ✅ caption+hook variant axis (Tasks 3,5) · per-account deterministic assignment (Task 5, `surface_key`/`_hash`) · shared-base+overlay render (Task 2 `burn_hook_only` + Task 5 wiring) · observe-only attribution (Task 1 fields + Task 6 digest) · toggle default OFF + fail-open (Task 4 + fail-open in Tasks 2,5) · backward-compat (optional fields, OFF = today's behavior, verified in Tasks 1,5) · real-render proof (Task 7). NONE of `amplify`/`classify_outcomes`/`_delete_moment_cascade` touched (spec out-of-scope honored).

**Placeholder scan:** the two flagged `> Implementer note`s (Task 3 response-helper name, Task 5 poster upload seam) are NOT placeholders for the design — they're explicit "verify this real API before coding" instructions because the exact helper/field name must be read from the live code, not guessed; each names what to grep and what to do. Every code step has complete code.

**Type consistency:** `CaptionItem.hook` (Task 1) ← stored by `ingest_captions` (Task 3) ← read by crosspost `cap.get("hook")` (Task 5). `Post.variant_key`/`variant_hook` (Task 1) ← stamped by crosspost (Task 5) ← read by digest (Task 6). `overlay.burn_hook_only(base, out, hook, *, width, height, font)` (Task 2) ← called by crosspost (Task 5) + integration (Task 7), same signature. `cfg.creative_variation` (Task 4) ← gated in crosspost (Task 5). Consistent.

**Known integration risk (flagged, not hidden):** Task 5's media-path wiring depends on how the poster currently reads the clip file (`Post.media_urls` vs `clip.media_url`/`ensure_clip_media`). The implementer MUST verify the real upload seam (grep named in Task 5) and route the per-account variant file through it; the plan shows the `media_urls` approach but instructs adjustment if the poster reads a different field. This is the one place the plan cannot be 100% literal without reading `post/`'s current body at implementation time.
