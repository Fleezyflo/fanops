# tests/test_caption_scoping.py — M5: scope the caption REQUEST to the affinity-admitted surfaces.
# affinity_admits is the SHARED gate (crosspost + caption scoper read it, so they can't drift). The
# scoper returns the full surface set when casting is OFF or the moment is uncast (byte-identical), so OFF
# is a no-op. The composed test proves a casting-ON run loses NO cast-surface post; the swap-edge test
# proves a post-captioning re-cast degrades safely via the pre-existing crosspost `cap is None` net.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Batch, ClipState, MomentState, Platform, Fmt,
                           CaptionSet, CaptionItem)
from fanops.accounts import Accounts
from fanops.casting import affinity_admits, scoped_caption_surfaces
from fanops.caption import request_captions, ingest_captions
from fanops.crosspost import crosspost_clips
from fanops.agentstep import latest_request_id, response_path, request_path


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, *, persona="x", aid="1", platforms=("instagram", "youtube")):
    return {"handle": handle, "account_id": aid, "platforms": list(platforms), "status": "active", "persona": persona}

def _moment(parent="src_1", *, affinities=None):
    return Moment(id="mom_1", parent_id=parent, content_token="0-7", start=0, end=7, reason="r",
                  transcript_excerpt="they slept on me", state=MomentState.decided,
                  affinities=list(affinities) if affinities else [])

def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)


# ---- Task 1: affinity_admits — the shared gate (provably == the negation of the crosspost gate) ----
def test_affinity_admits_off_ignores_affinities(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    m = _moment(affinities=["@a"])
    assert affinity_admits(cfg, m, "@a") is True and affinity_admits(cfg, m, "@b") is True   # OFF -> admit all

def test_affinity_admits_on_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    assert affinity_admits(cfg, _moment(affinities=[]), "@b") is True       # uncast -> fan to all
    assert affinity_admits(cfg, None, "@a") is True                          # defensive: no moment -> admit
    assert affinity_admits(cfg, _moment(affinities=["@a"]), "@a") is True    # cast & member
    assert affinity_admits(cfg, _moment(affinities=["@a"]), "@b") is False   # cast & NOT member -> skip


# ---- Task 2 + 5: scoped_caption_surfaces (the filter the pipeline calls) ----
def test_scoped_caption_surfaces_scopes_when_cast(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    surfaces = Accounts.load(cfg).surfaces()
    scoped = scoped_caption_surfaces(cfg, _moment(affinities=["@a"]), surfaces)
    assert {acct for acct, _ in scoped} == {"@a"}                            # only the cast account's surfaces

def test_scoped_caption_surfaces_full_when_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    surfaces = Accounts.load(cfg).surfaces()
    scoped = scoped_caption_surfaces(cfg, _moment(affinities=["@a"]), surfaces)
    assert list(scoped) == [(s.account, s.platform) for s in surfaces]       # OFF -> byte-identical (all)

def test_scoped_caption_surfaces_full_when_uncast(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    surfaces = Accounts.load(cfg).surfaces()
    scoped = scoped_caption_surfaces(cfg, _moment(affinities=[]), surfaces)
    assert {acct for acct, _ in scoped} == {"@a", "@b"}                      # uncast -> all surfaces


# ---- Task 5b: composed casting-ON — scoped request THEN zero post loss for cast surfaces ----
def test_casting_on_scopes_request_and_loses_no_post(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, language="en"))
    led.add_moment(_moment(affinities=["@a"]))                               # cast to @a only
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.rendered))
    accts = Accounts.load(cfg)
    # the scoped request the pipeline would build (Task 3 wiring uses this exact call)
    led = request_captions(led, cfg, "clip_1", scoped_caption_surfaces(cfg, led.moments["mom_1"], accts.surfaces()),
                           accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert {s["surface"].split("/")[0] for s in payload["surfaces"]} == {"@a"}   # request SCOPED to @a
    # answer the request for @a's surfaces, ingest, then crosspost — every cast surface must mint a post
    rid = latest_request_id(cfg, "captions", "clip_1")
    items = [CaptionItem(surface=s["surface"], caption="impact.", hashtags=["#x"]) for s in payload["surfaces"]]
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=items).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"}                 # cast surfaces all posted (zero loss)
    assert len(led.posts) == 2                                              # @a/instagram + @a/youtube


# ---- Task 5c: swap edge — re-cast after captioning degrades safely (no crash, no silent drop) ----
def test_recast_after_caption_skips_uncaptioned_surface(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(_moment(affinities=["@b"]))                              # the SWAPPED-IN cast (was @a)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "a", "hashtags": []},   # captioned for the OLD cast set only
                          "@a/youtube": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    # @a skipped by the affinity gate (not in [@b]); @b admitted but uncaptioned -> the cap-is-None net skips it.
    assert led.posts == {}                                                  # safe degradation: no post, no crash
    assert "clip_1" in led.clips                                            # the clip survives intact
