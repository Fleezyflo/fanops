# tests/test_mol151_p10_captions.py — MOL-151 (P10): captions are owner × platform.
# scoped_caption_surfaces (the (clip × account) AccountSelection scoper) is DELETED; the pipeline now
# scopes a clip's caption REQUEST to the moment owner's surfaces via the SAME affinity gate crosspost
# enforces (affinity_admits) — so caption-scope can never drift from post-minting. A cast (owner=A)
# moment authors captions for A × {A's platforms} only, never B; an uncast/OFF moment still fans to all.
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, ClipState, MomentState
from fanops.accounts import Accounts
from fanops.pipeline import _stage_render_and_caption, _aspects_for
from fanops.log import get_logger
from fanops.agentstep import request_path


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, *, aid="1", platforms=("instagram", "youtube")):
    return {"handle": handle, "account_id": aid, "platforms": list(platforms), "status": "active"}

def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)


def test_captions_scoped_to_owner_platforms(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("a"), _acct("b", aid="2")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, duration=20.0, language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="1-5", start=1.0, end=5.0, reason="r",
                          transcript_excerpt="they slept on me", state=MomentState.decided, affinities=["a"]))
    accts = Accounts.load(cfg)
    _fake_ffmpeg(mocker)
    led = _stage_render_and_caption(led, cfg, accts, _aspects_for(accts), get_logger(cfg))
    # the render loop produced a captioned/caption-requested clip for the owner moment
    cids = [c.id for c in led.clips.values()
            if c.parent_id == "mom_1" and c.state is ClipState.captions_requested]
    assert cids, "the owner moment produced no captions_requested clip"
    for cid in cids:
        payload = json.loads(request_path(cfg, "captions", cid).read_text())
        accts_req = {s["surface"].split("/")[0] for s in payload["surfaces"]}
        plats_req = {s["surface"].split("/")[1] for s in payload["surfaces"]}
        assert accts_req == {"a"}                       # owner ONLY — no @b surface (clip × account scoping dead)
        assert plats_req == {"instagram", "youtube"}    # per-platform survives: A × {its platforms}


def test_scoped_caption_surfaces_gone():
    import pytest
    import fanops.casting as casting
    assert not hasattr(casting, "scoped_caption_surfaces")   # the symbol is deleted from the module
    with pytest.raises(ImportError):
        from fanops.casting import scoped_caption_surfaces   # noqa: F401
