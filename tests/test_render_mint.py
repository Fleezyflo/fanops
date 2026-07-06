# tests/test_render_mint.py — slice 2 (burn on approval): the crosspost MINT records only the per-account
# INTENT (Post.variant_hook + variant_key) under creative_variation; it NO LONGER runs ffmpeg or mints a
# Render. render_id stays None, media_urls [] (review serves the MASTER clip; the Render materializes when the
# operator APPROVES — see tests/test_render_on_approval.py). Empty persona hook falls back to the shared m.hook
# (still recorded as the intent — never a textless ship). cv OFF / hookless -> no variant_hook, byte-identical.
# account_render_spec (the content-addressed id + cut decision, shared by approval AND re-burn) is unchanged.
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _mock_burn(mocker):
    # a spy that ALSO writes the out file — so a test can assert the mint DID NOT call it (the burn now
    # belongs to approval), and the approval tests can rely on a written file.
    def burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"V"); return True
    return mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)

def _seed_clip(led, cfg, *, m_hook=None, surfaces=("a/instagram",), batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _run(cfg):
    led = Ledger.load(cfg)
    return crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")


# ---- mint records the per-account INTENT, defers the render (no ffmpeg, no Render) ----
def test_mint_records_variant_hooks_and_defers_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); burn = _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                         {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="shared hook",
               surfaces=("a/instagram", "b/instagram")); led.save()
    led = _run(cfg)
    assert led.renders == {}                                          # nothing rendered at the mint
    assert burn.call_count == 0                                       # the mint ran NO ffmpeg (burn deferred to approval)
    posts = {p.account: p for p in led.posts.values()}
    assert posts["a"].variant_hook == "shared hook" and posts["b"].variant_hook == "shared hook"
    assert all(p.render_id is None and p.media_urls == [] for p in posts.values())
    assert posts["a"].variant_key == "a|instagram"                 # the surface intent (surface_key) is recorded

def test_mint_same_hook_records_both_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"],
                          "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="one hook",
               surfaces=("a/instagram", "a/tiktok")); led.save()
    led = _run(cfg)
    assert led.renders == {}
    assert all(p.variant_hook == "one hook" and p.render_id is None for p in led.posts.values())

def test_mint_empty_persona_hook_records_shared_fallback(tmp_path, monkeypatch, mocker):
    # @b has NO persona hook but the moment has a shared m.hook -> @b's variant intent is the shared hook
    # (recorded as variant_hook, burned at approval — never a silent textless base ship).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="SHARED",
               surfaces=("b/instagram",)); led.save()
    led = _run(cfg)
    assert led.renders == {}
    p = next(iter(led.posts.values()))
    assert p.variant_hook == "SHARED" and p.render_id is None and p.media_urls == []


# ---- default-safe: cv OFF and hookless record NO variant intent (byte-identical) ----
def test_cv_off_mints_no_renders(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); _mock_burn(mocker)   # M3d: default flipped ON — pin OFF to prove the OFF path still mints nothing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="h", surfaces=("a/instagram",)); led.save()
    led = _run(cfg)
    assert led.renders == {}
    p = next(iter(led.posts.values()))
    assert p.render_id is None and p.media_urls == [] and p.variant_hook is None

def test_hookless_moment_mints_no_render(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1"); _mock_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook=None, surfaces=("a/instagram",)); led.save()
    led = _run(cfg)
    assert led.renders == {}
    p = next(iter(led.posts.values()))
    assert p.render_id is None and p.media_urls == [] and p.variant_hook is None


# ---- account_render_spec: the SINGLE source of the render id + cut decision (approval == reburn) ----
def test_account_render_spec_bare_for_default_tagged_for_override(tmp_path):
    from fanops.crosspost import account_render_spec
    from fanops.ids import child_id
    from fanops.bands import band_for
    from fanops.models import Clip, Fmt
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16)
    class _A:                                                            # duck-typed account
        def __init__(self, **kw): self.__dict__.update(kw)
    rid0, cut0, prof0, top0 = account_render_spec(cfg, clip=clip, hook="H", acct=None)
    assert cut0 is False and prof0 == cfg.clip_profile                   # None acct -> global defaults, no cut
    assert rid0 == child_id("render", "clip_1", "H")                     # bare-hook id (byte-identical to shared)
    rid1, cut1, prof1, _ = account_render_spec(cfg, clip=clip, hook="H", acct=_A(clip_profile="short"))
    b = band_for("short")
    assert cut1 is True and prof1 == "short"                             # override -> wants a cut
    assert rid1 == child_id("render", "clip_1", f"H\x1fband:{b.lo:g}-{b.hi:g}") and rid1 != rid0   # band-tagged, distinct


def test_account_render_spec_shared_focus_contract_same_id(tmp_path):
    # CULM-7 pin: two accounts with the SAME (clip, hook, band, frame) compute the SAME render id -> ONE
    # render. Today smart-framing focus is SOURCE-derived (not per-account), so the shared id is correct. IF
    # per-account focus is ever added, this test MUST change (append a focus token) or the two accounts
    # silently COLLIDE on one render id.
    from fanops.crosspost import account_render_spec
    from fanops.accounts import Account
    from fanops.models import Clip, Fmt
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16)
    a = Account(handle="a", account_id="1", platforms=["instagram"])
    b = Account(handle="b", account_id="2", platforms=["instagram"])
    rid_a, *_ = account_render_spec(cfg, clip=clip, hook="watch this", acct=a)
    rid_b, *_ = account_render_spec(cfg, clip=clip, hook="watch this", acct=b)
    assert rid_a == rid_b                                  # same spec -> one render id (shared source-derived focus)
