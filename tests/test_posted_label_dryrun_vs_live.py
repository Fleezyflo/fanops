"""The Posted tab never shows a dryrun row — a dryrun post never becomes `published`.

dryrun-boundary M3 (PRD Finding #1): the five legacy tests that once lived here hand-CONSTRUCTED
`Post(state=published, public_url="dryrun://...")` — the phantom-published-dryrun signature — to pin the
`_classify_channel` dryrun:// scheme branch. M1 made that state unproducible (a dryrun post halts `queued`,
never enters distribution) and M3 DELETED the dryrun:// branch itself; those tests are gone with it. The
`dryrun://`→`dryrun` label survives only as the incidental non-http fall-through, pinned now by
`test_classify_channel_still_labels_unknown_url_dryrun` in test_dryrun_scaffolding_gone.py — not here, where
it would have falsely implied the pipeline can still ship a `published` dryrun row.

What REMAINS is the POSITIVE contract: run a real dryrun post through the real publish path and prove the
boundary keeps it out of the Posted library entirely. This is the operator's verbatim complaint answered
structurally — 'the system says posted when nothing is posted' — by making the row unconstructable, not by
labeling it after the fact."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import iso_z
from fanops.studio.views_results import posted_library

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)


def _seed_accounts(cfg: Config) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"}]}))


def _seed_clip(led: Ledger) -> Clip:
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080,
                          duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    return clip


def test_dryrun_never_reaches_posted_via_publish_due(tmp_path, monkeypatch):
    """dryrun-boundary M1 — the POSITIVE contract that REPLACES the dryrun://-in-Posted lie.
    A dryrun (not-live) post is built + approved + scheduled, then run through the REAL publish path
    (publish_due). The boundary keeps it `queued` — it never enters distribution, never becomes a
    `published` row, and therefore NEVER appears in the Posted library. This test FAILS if the
    boundary regresses."""
    from fanops.post.run import publish_due
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)          # dryrun (not live)
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    # an approved (queued), DUE dryrun post with no fabricated distribution artifacts
    led.add_post(Post(id="p_dry", parent_id=clip.id, account="@a", account_id="ia",
                      platform=Platform.instagram, caption="c", state=PostState.queued,
                      scheduled_time="2020-01-01T00:00:00Z", media_urls=["file:///clip_1_9x16.mp4"]))
    led.save()

    summary = publish_due(cfg)

    post = Ledger.load(cfg).posts["p_dry"]
    assert post.state is PostState.queued                      # boundary: stays queued, never published
    assert summary["published"] == 0 and summary.get("not_distributed", 0) >= 1
    rows = posted_library(Ledger.load(cfg), cfg)
    assert [r for r in rows if getattr(r, "post_id", None) == "p_dry"] == []   # NOT in the Posted library
