# tests/test_product_closure.py
# MOL-792: one-shot legacy IG product_type closure — None|REELS → "post", unexpected untouched,
# idempotent (second run zero bytes), non-IG skipped, zero network.
from __future__ import annotations

import ast
import json
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Platform, Post, PostState
from fanops.product_closure import close_legacy_product_type


def _post(pid, *, plat=Platform.instagram, product_type=None):
    return Post(id=pid, parent_id="c1", account="a", account_id="1", platform=plat,
                caption="c", state=PostState.published, public_url=f"https://ig/{pid}",
                product_type=product_type)


def _seed(cfg: Config, posts: list[Post]) -> None:
    led = Ledger(cfg)
    for p in posts:
        led.add_post(p)
    led.save()


def _dump_posts(cfg: Config) -> dict:
    return {k: v.model_dump() for k, v in Ledger.load(cfg).posts.items()}


def test_none_closes_to_post(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p_none", product_type=None)])
    counts = close_legacy_product_type(cfg)
    assert counts == {"closed": 1, "already_service": 0, "unexpected": 0, "skipped_non_ig": 0}
    assert Ledger.load(cfg).posts["p_none"].product_type == "post"


def test_reels_closes_to_post(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p_reels", product_type="REELS")])
    counts = close_legacy_product_type(cfg)
    assert counts["closed"] == 1
    assert Ledger.load(cfg).posts["p_reels"].product_type == "post"


def test_second_run_zero_writes(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p1", product_type=None), _post("p2", product_type="REELS"),
                _post("p3", product_type="post")])
    close_legacy_product_type(cfg)
    before = _dump_posts(cfg)
    counts = close_legacy_product_type(cfg)
    assert counts["closed"] == 0
    assert counts["already_service"] == 3
    assert _dump_posts(cfg) == before


def test_feed_unexpected_untouched_and_logged(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p_feed", product_type="FEED")])
    counts = close_legacy_product_type(cfg)
    assert counts["unexpected"] == 1 and counts["closed"] == 0
    assert Ledger.load(cfg).posts["p_feed"].product_type == "FEED"
    lines = [json.loads(ln) for ln in cfg.log_path.read_text().splitlines() if ln.strip()]
    hit = [r for r in lines if r.get("outcome") == "unexpected_product_type"
           and r.get("unit_id") == "p_feed" and r.get("stage") == "product_closure"]
    assert len(hit) == 1 and hit[0].get("value") == "FEED"


def test_tiktok_untouched(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p_tt", plat=Platform.tiktok, product_type=None)])
    before = _dump_posts(cfg)
    counts = close_legacy_product_type(cfg)
    assert counts["skipped_non_ig"] == 1 and counts["closed"] == 0
    assert _dump_posts(cfg) == before
    assert Ledger.load(cfg).posts["p_tt"].product_type is None


def test_non_ig_skipped(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_post("p_yt", plat=Platform.youtube, product_type="REELS"),
                _post("p_ig", product_type="REELS")])
    counts = close_legacy_product_type(cfg)
    assert counts["skipped_non_ig"] == 1 and counts["closed"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p_yt"].product_type == "REELS"
    assert led.posts["p_ig"].product_type == "post"


def test_zero_network_imports():
    src = Path(__file__).resolve().parents[1] / "src" / "fanops" / "product_closure.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".", 1)[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".", 1)[0])
    assert "requests" not in mods
    assert "meta_graph" not in mods
    assert "urllib" not in mods
    assert "http" not in mods
