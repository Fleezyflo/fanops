# S09: embed surface editor in focus deck — edit caption / re-burn / regenerate without leaving focus.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

FUTURE = "2099-06-06T12:00:00Z"

def _accounts(cfg, handle="a"):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))

def _seed(cfg, *, pid="p1", caption="await caption", hook="SCROLL HOOK", handle="a"):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.captioned))
    led.add_post(Post(id=pid, parent_id="clip_1", account=handle,
                      account_id="ig1", platform=Platform.instagram, caption=caption,
                      state=PostState.awaiting_approval, scheduled_time=FUTURE))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _focus_url(**extra):
    q = "account=@a&view=account&focus=1&fi=0"
    for k, v in extra.items():
        if v is not None: q += f"&{k}={v}"
    return f"/review?{q}"

def test_focus_card_renders_surface_editor(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get(_focus_url()).data.decode()
    assert "details.surface-editor" in html or 'class="surface-editor"' in html
    assert 'id="focus-editor"' in html
    assert 'id="edit-p1"' in html
    assert 'id="caption-p1"' in html
    assert "<textarea" in html

def test_focus_caption_save_preserves_fi(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    c = _client(cfg)
    html = c.post("/caption/p1?account=@a&view=account&focus=1&fi=0",
                  data={"caption": "edited in focus"}).data.decode()
    assert "edited in focus" in html
    assert "fi=0" in html
    assert Ledger.load(cfg).posts["p1"].caption == "edited in focus"
    assert "review-focus" in html

def test_focus_reburn_preserves_scope(tmp_path, mocker):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    rendered = Clip(id="clip_1", parent_id="m1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), rendered))
    html = _client(cfg).post("/reburn-hook/p1?account=@a&view=account&focus=1&fi=0",
                             data={"hook": "NEW HOOK TEXT"}).data.decode()
    assert 'id="review-focus"' in html
    assert "fi=0" in html
    assert Ledger.load(cfg).moments["m1"].hook == "NEW HOOK TEXT"

def test_focus_editor_keyboard_hint(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get(_focus_url()).data.decode()
    assert "E edit" in html

def test_card_list_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/review?view=list&account=@a").data.decode()
    assert 'id="focus-editor"' not in html
    assert 'hx-post="/caption/p1?account=' not in html
    assert 'hx-post="/caption/p1"' in html or "do_caption" in html
