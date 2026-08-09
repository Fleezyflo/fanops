# MOL-824: Post.product_type → Post.post_type with SCHEMA_VERSION 11→12 key migration.
# P5: a row persisted under the old key keeps its declaration after load. Second migration is a no-op.
# P4: GraphInsightsClient.list_posts reads "post_type" with no getattr default (missed rename must raise).
import json
import pytest
import fanops.models as models_mod
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION, _migrate_v12_rename_post_type
from fanops.models import Post, Platform, PostState, ImportedMedia
from fanops.post.metrics import GraphInsightsClient


def _write_legacy_json(cfg, raw):
    cfg.legacy_ledger_json_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.legacy_ledger_json_path.write_text(json.dumps(raw))
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()


def test_schema_version_is_twelve():
    assert SCHEMA_VERSION == 12


def test_post_has_post_type_not_product_type():
    assert "post_type" in Post.model_fields
    assert "product_type" not in Post.model_fields
    assert "product_type" in ImportedMedia.model_fields


def test_post_comment_has_no_meta_vocab():
    text = open(models_mod.__file__).read()
    start = text.index("class Post(BaseModel)")
    end = text.index("class ImportedMedia(BaseModel)")
    post_body = text[start:end]
    assert "AD|FEED|STORY|REELS" not in post_body
    assert "post|story" in post_body
    assert "_POSTIZ_POST_TYPES" in post_body
    im_body = text[end:end + 800]
    assert "AD|FEED|STORY|REELS" in im_body


def test_migrate_v12_renames_product_type_key():
    raw = {"posts": {
        "p1": {"id": "p1", "product_type": "post", "caption": "x"},
        "p2": {"id": "p2", "post_type": "story", "product_type": "REELS"},
        "p3": {"id": "p3", "caption": "no key"},
        "torn": "not-a-dict",
    }, "imported_media": {"M1": {"media_id": "M1", "product_type": "REELS"}}}
    out = _migrate_v12_rename_post_type(raw)
    assert out["posts"]["p1"]["post_type"] == "post" and "product_type" not in out["posts"]["p1"]
    assert out["posts"]["p2"]["post_type"] == "story" and "product_type" not in out["posts"]["p2"]
    assert "post_type" not in out["posts"]["p3"]
    assert out["imported_media"]["M1"]["product_type"] == "REELS"
    out2 = _migrate_v12_rename_post_type(out)
    assert out2["posts"] == out["posts"]


def test_v11_ledger_preserves_declaration_on_load(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 11,
           "sources": {}, "moments": {}, "clips": {},
           "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "a", "account_id": "1",
                            "platform": "instagram", "caption": "x", "state": "queued",
                            "product_type": "post"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}, "renders": {},
           "imported_media": {"M1": {"media_id": "M1", "product_type": "REELS"}}}
    _write_legacy_json(cfg, raw)
    led = Ledger.load(cfg)
    assert led.posts["p1"].post_type == "post"
    assert "product_type" not in Post.model_fields
    assert led.imported_media["M1"].product_type == "REELS"
    with Ledger.transaction(cfg):
        pass
    saved = Ledger.load(cfg)._to_doc()
    assert saved["schema_version"] == 12
    assert saved["posts"]["p1"].get("post_type") == "post"
    assert "product_type" not in saved["posts"]["p1"]
    assert saved["imported_media"]["M1"]["product_type"] == "REELS"


def test_list_posts_requires_post_type_attribute(tmp_path):
    cfg = Config(root=tmp_path)
    class Bare:
        media_id = "M1"
        submission_id = "s1"
        account = "a"
        cut_seconds = 10.0
    with pytest.raises(AttributeError):
        GraphInsightsClient(cfg, posts=[Bare()], insights_fn=lambda mid, pt: {"reach": 1}).list_posts()


def test_list_posts_forwards_post_type(tmp_path):
    cfg = Config(root=tmp_path)
    post = Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.published, media_id="M1", submission_id="s1",
                post_type="post", public_url="https://www.instagram.com/reel/p1/", cut_seconds=10.0)
    seen = []
    def spy(mid, pt):
        seen.append((mid, pt)); return {"reach": 3}
    rows = GraphInsightsClient(cfg, posts=[post], insights_fn=spy).list_posts()
    assert seen == [("M1", "post")]
    assert rows[0]["metrics"]["reach"] == 3
