# tests/test_render_model.py — Stage A of the per-account Render foundation: the Render entity is a
# first-class, content-addressable per-account artifact under the shared Clip. Render owns the per-account
# file + hook; Post.render_id is the single authoritative pointer. SCHEMA v5->v6 injects the top-level
# `renders` map (additive, byte-for-byte the v1->v2 stitch_plans / v4->v5 batches injection — NO row
# backfill, the ledger is NEVER wiped). Old ledgers load with renders={} and render_id None.
import json
from fanops.config import Config
from fanops.models import Render, RenderState, Post, Platform
from fanops.ledger import Ledger, SCHEMA_VERSION


def _write(cfg, raw):
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))


def test_schema_version_at_least_six():
    # renders landed at v6; the version only climbs. Pin the floor, not a magic literal, so a later
    # additive map (v10 imported_media, ...) doesn't false-fail this render-model test.
    assert SCHEMA_VERSION >= 6


# ---- the Render model: per-account artifact, content-addressed, lifecycle state ----
def test_render_model_defaults():
    r = Render(id="render_x", clip_id="clip_1", account="@a", surface_key="@a|instagram", path="/clips/x.mp4")
    assert r.hook_text is None and r.media_url is None and r.batch_id is None and r.source_id is None
    assert r.state is RenderState.rendered

def test_render_model_full():
    r = Render(id="render_x", clip_id="clip_1", account="@a", surface_key="@a|instagram",
               hook_text="watch his face", path="/clips/b/s/render_x.9:16.mp4",
               media_url="https://cdn/x.mp4", state=RenderState.published, batch_id="batch_1", source_id="src_1")
    assert r.hook_text == "watch his face" and r.state is RenderState.published and r.batch_id == "batch_1"


# ---- Post.render_id is the single authoritative pointer (additive, default None) ----
def test_post_render_id_defaults_none():
    p = Post(id="p1", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram, caption="c")
    assert p.render_id is None


# ---- Ledger.renders collection + add/get ----
def test_ledger_add_and_get_render(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_render(Render(id="render_x", clip_id="clip_1", account="@a", surface_key="@a|instagram", path="/x.mp4"))
    assert led.get_render("render_x") is not None and led.get_render("render_x").clip_id == "clip_1"
    assert led.get_render("nope") is None

def test_add_render_is_setdefault(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_render(Render(id="render_x", clip_id="clip_1", account="@a", surface_key="k", path="/x.mp4"))
    led.add_render(Render(id="render_x", clip_id="clip_2", account="@b", surface_key="k", path="/y.mp4"))
    assert led.get_render("render_x").clip_id == "clip_1"   # first write wins (mirrors add_clip/add_post)


# ---- save/load round-trip ----
def test_render_round_trips_through_save_load(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_render(Render(id="render_x", clip_id="clip_1", account="@a", surface_key="@a|instagram",
                              hook_text="H", path="/clips/x.mp4", batch_id="b1", source_id="s1"))
    on_disk = json.loads(cfg.ledger_path.read_text())
    assert on_disk["schema_version"] == SCHEMA_VERSION and "renders" in on_disk
    led2 = Ledger.load(cfg)
    r = led2.get_render("render_x")
    assert r is not None and r.hook_text == "H" and r.batch_id == "b1" and r.source_id == "s1"


# ---- migration v5 -> v6: inject renders={}, NO row lost, render_id rides the pydantic default ----
def test_migration_v5_to_v6_injects_renders_and_keeps_rows(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 5,
           "sources": {}, "moments": {}, "clips": {},
           "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "@a", "account_id": "1",
                            "platform": "instagram", "caption": "x", "state": "awaiting_approval"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.renders == {}                                  # injected
    assert set(led.posts) == {"p1"} and led.posts["p1"].render_id is None   # row survives, render_id defaults
    with Ledger.transaction(cfg):                            # save re-stamps the current schema version
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
    # idempotent reload
    led2 = Ledger.load(cfg)
    assert led2.renders == {} and set(led2.posts) == {"p1"}


def test_render_state_is_reserved_not_driven():
    # CULM-9 (decided: RESERVE): a Render is BORN `rendered` and NO driver advances it. This pins the
    # decision — the members are kept (views_results._SHIPPABLE_RENDER reads them by name) but the lifecycle
    # is undriven. If an advancer/GC is ever wired, this test must change to assert the new arc.
    assert Render(id="r", clip_id="c", account="@a", surface_key="@a|instagram", path="/v.mp4").state \
        is RenderState.rendered                                              # born rendered, default
    assert [s.value for s in RenderState] == ["rendered", "queued", "published", "analyzed", "retired"]
