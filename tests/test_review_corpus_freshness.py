# MOL-688: Review caption-vs-corpus freshness signal — badge only when caption predates corpus derive.
from fanops.config import Config
from fanops.models import Post, Platform, PostState
from fanops.studio.views_review import _caption_corpus_stale


def _post(**kw):
    base = dict(id="p1", parent_id="c1", account="a", platform=Platform.instagram,
                caption="x", state=PostState.awaiting_approval)
    base.update(kw)
    return Post(**base)


def test_caption_before_corpus_derive_is_stale(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text('{"personas":[{"id":"craft","name":"C","voice":"x","niche":["hiphop"],'
                                '"hashtag_corpus":["#bars"],'
                                '"hashtag_corpus_meta":{"#bars":{"measured_at":"2026-07-29T12:00:00Z","from":"#hiphop"}}}]}')
    stale = _post(created_at="2026-07-28T12:00:00Z")
    fresh = _post(created_at="2026-07-29T13:00:00Z")
    edited = _post(created_at="2026-07-28T12:00:00Z", edited_at="2026-07-29T13:00:00Z")
    assert _caption_corpus_stale(cfg, stale, "craft") is True
    assert _caption_corpus_stale(cfg, fresh, "craft") is False
    assert _caption_corpus_stale(cfg, edited, "craft") is False   # recaption/edit clears the signal
    assert _caption_corpus_stale(cfg, stale, None) is False
    assert _caption_corpus_stale(cfg, stale, "missing") is False
