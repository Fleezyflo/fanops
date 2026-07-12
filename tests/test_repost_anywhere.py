# tests/test_repost_anywhere.py — U8: "Repost posted clips anywhere". Pull an already-shipped clip and
# post it again on the SAME account (existing repost_post, epoch id) OR on ONE / ALL OTHER accounts. The
# cross-account path is a thin composer (repost_to_other_accounts) over the existing crosspost_to_account
# mint verb — one awaiting_approval post per target surface, approval gate intact, no auto-schedule, no
# fourth Post() construction site. Per-target honesty mirrors crosspost_all_to_account: ok=True when ANY
# target minted-or-already-exists, ok=False only when EVERY target skipped.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Moment, Source, Fmt
from fanops.studio import actions


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _accounts(cfg, spec):
    """spec: {handle: [platform, ...]}. Writes an active accounts.json (mirrors the batch-test helper)."""
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "0", "platforms": plats, "status": "active"} for h, plats in spec.items()]}))


def _seed_published(cfg, *, pid="p0", clip="clip_0", account="a0", platform=Platform.instagram,
                    batch_id=None, when="2026-06-01T00:00:00Z"):
    """A shipped post on (account, platform) for `clip`, with a real on-disk render so the aspect-correct
    crosspost mint (which refuses a gc-swept render at mint) can reuse it."""
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{clip}.mp4").write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        if not led.sources.get("s1"):
            led.add_source(Source(id="s1", source_path="/show.mp4", language="en", batch_id=batch_id))
        if not led.moments.get("m1"):
            led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r"))
        if clip not in led.clips:
            led.add_clip(Clip(id=clip, parent_id="m1", path=str(cdir / f"{clip}.mp4"),
                              aspect=Fmt.r9x16, state=ClipState.published))
        led.add_post(Post(id=pid, parent_id=clip, account=account, account_id="0", platform=platform,
                          caption="fire", state=PostState.published, scheduled_time=when,
                          public_url=f"https://insta/{pid}", batch_id=batch_id))


# ── composer: multi-mint across picked accounts ─────────────────────────────────────────────────────
def test_repost_to_other_accounts_mints_on_each_target(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1", "a2"])
    assert r.ok and r.detail["outcome"] == "repost_anywhere"
    by_surface = {ln["surface"]: ln for ln in r.detail["lines"]}
    assert by_surface["a1/instagram"]["status"] == "minted"
    assert by_surface["a2/instagram"]["status"] == "minted"
    led = Ledger.load(cfg)
    minted = [p for p in led.posts.values() if p.state is PostState.awaiting_approval]
    assert {p.account for p in minted} == {"a1", "a2"}          # two new awaiting posts, correct surfaces
    assert all(p.parent_id == "clip_0" for p in minted)         # same clip
    assert led.posts["p0"].state is PostState.published         # source published post untouched


def test_repost_to_other_accounts_born_awaiting_no_schedule(tmp_path):
    # acceptance #5: minted reposts are awaiting_approval, scheduled_time=None (approval gate intact, no auto-schedule).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1"])
    np = [p for p in Ledger.load(cfg).posts.values() if p.account == "a1"][0]
    assert np.state is PostState.awaiting_approval and np.scheduled_time is None


def test_repost_to_other_accounts_idempotent_reclick(tmp_path):
    # acceptance #2: re-click reports already_exists and mints nothing new (per-(clip,surface) setdefault).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1", "a2"])
    before = len(Ledger.load(cfg).posts)
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1", "a2"])
    assert r.ok
    assert all(ln["status"] == "already_exists" for ln in r.detail["lines"])
    assert len(Ledger.load(cfg).posts) == before               # nothing new minted


def test_repost_all_others_skips_self(tmp_path):
    # acceptance #3: all_others over a 3-account fixture mints 2 (skips the source account).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    r = actions.repost_to_other_accounts(cfg, "p0", all_others=True)
    assert r.ok
    minted = {ln["surface"] for ln in r.detail["lines"] if ln["status"] == "minted"}
    assert minted == {"a1/instagram", "a2/instagram"}
    assert not any(ln["surface"] == "a0/instagram" for ln in r.detail["lines"])   # never re-mints on self
    accts = {p.account for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval}
    assert accts == {"a1", "a2"}


def test_repost_partial_success_ineligible_target_skipped(tmp_path):
    # acceptance #4: a target without the source platform surfaces a skip line; eligible targets still mint.
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["tiktok"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", platform=Platform.instagram)
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1", "a2"])
    assert r.ok                                                 # partial success is still ok=True
    by_surface = {ln["surface"]: ln for ln in r.detail["lines"]}
    assert by_surface["a1/instagram"]["status"] == "minted"
    a2 = next(ln for ln in r.detail["lines"] if ln["surface"].startswith("a2"))
    assert a2["status"] == "skipped" and a2["error"]           # carries the verb's inline error
    accts = {p.account for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval}
    assert accts == {"a1"}                                     # only the eligible target minted


def test_repost_all_skipped_is_not_ok(tmp_path):
    # mirror crosspost_all_to_account honesty: EVERY target skipped -> ok=False (nothing minted).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a2": ["tiktok"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", platform=Platform.instagram)
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a2"])
    assert not r.ok
    assert not any(p.state is PostState.awaiting_approval for p in Ledger.load(cfg).posts.values())


def test_repost_to_other_accounts_dedups_and_ignores_source(tmp_path):
    # duplicate targets collapse; the source account passed in the list is dropped (never re-mints on self).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1", "a1", "a0"])
    assert r.ok
    assert [ln["surface"] for ln in r.detail["lines"]] == ["a1/instagram"]   # one line, source dropped


def test_repost_to_other_accounts_carries_batch_id(tmp_path):
    # the composer's detail carries the source batch_id so the banner's Review link can scope to the batch.
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", batch_id="bx")
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=["a1"])
    assert r.detail["batch_id"] == "bx"


def test_repost_to_other_accounts_unknown_post_errors(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    r = actions.repost_to_other_accounts(cfg, "nope", target_accounts=["a1"])
    assert not r.ok and "no such post" in r.error


def test_repost_to_other_accounts_no_targets_errors(tmp_path):
    # empty pick + not all_others -> nothing to do, ok=False (no silent no-op success).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    r = actions.repost_to_other_accounts(cfg, "p0", target_accounts=[])
    assert not r.ok


# ── repost_post detail carries batch_id (display-only; behavior unchanged) ──────────────────────────
def test_repost_post_detail_exposes_batch_id(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", batch_id="bx")
    r = actions.repost_post(cfg, "p0")
    assert r.ok and r.detail["batch_id"] == "bx"               # exposed for the Review link (was already on the Post)


# ── route: /posts/repost-others/<post_id> ───────────────────────────────────────────────────────────
def test_repost_others_route_mints_and_preserves_batch(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", batch_id="bx")
    resp = _client(cfg).post("/posts/repost-others/p0?batch=bx",
                             data={"target_accounts": ["a1", "a2"]})
    assert resp.status_code == 200
    accts = {p.account for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval}
    assert accts == {"a1", "a2"}
    # the re-rendered panel keeps the ?batch= scope on the row action forms (D2 scope-bleed contract)
    assert b"batch=bx" in resp.data


def test_repost_others_route_all_others(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    resp = _client(cfg).post("/posts/repost-others/p0", data={"all_others": "1"})
    assert resp.status_code == 200
    accts = {p.account for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval}
    assert accts == {"a1", "a2"}


# ── UI: the one-menu row control + honest result banner ─────────────────────────────────────────────
def test_posted_row_menu_offers_three_choices(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    html = _client(cfg).get("/posted").data.decode()
    assert "/posts/repost/p0" in html                          # same-account (existing repost_post path)
    assert "/posts/repost-others/p0" in html                   # cross-account (new composer route)
    assert 'name="target_accounts"' in html                    # the pick-accounts checkbox list
    assert 'name="all_others"' in html                         # the all-other-accounts submit
    assert "a1" in html                                        # the OTHER active account is offered as a target


def test_posted_row_menu_summary_is_ghost(tmp_path):
    # MOL-51: the row menu is an infrequent utility -> tertiary .ghost summary (the demoted tier).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    html = _client(cfg).get("/posted").data.decode()
    import re as _re
    m = _re.search(r'<summary[^>]*>Post again[^<]*</summary>', html)
    assert m, "row action menu summary (Post again) not rendered"
    assert "ghost" in m.group(0), f"menu summary must be tertiary .ghost, got: {m.group(0)}"


def test_posted_per_row_crosspost_text_form_removed(tmp_path):
    # the per-row manual target_account/platform Crosspost text form is REPLACED by the menu (not left behind).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    html = _client(cfg).get("/posted").data.decode()
    assert 'placeholder="target @handle"' not in html          # the old per-row crosspost input is gone
    assert "posted-crosspost-one" not in html                  # its container class removed


def test_posted_bulk_crosspost_all_footer_kept(tmp_path):
    # explicitly KEEP the all-clips backfill footer (a different operator action, out of U8 scope).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0")
    html = _client(cfg).get("/posted").data.decode()
    assert "Crosspost all clips" in html and "posted-crosspost-all" in html


def test_cross_account_banner_lists_each_target_and_review_link(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"], "a1": ["instagram"], "a2": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", batch_id="bx")
    resp = _client(cfg).post("/posts/repost-others/p0?batch=bx",
                             data={"target_accounts": ["a1", "a2"]})
    html = resp.data.decode()
    assert "a1/instagram" in html and "a2/instagram" in html   # per-target line rendered
    assert "minted" in html.lower()                            # the status word
    assert "/review" in html and "batch=bx" in html            # a Review link scoped to the batch


def test_same_account_banner_links_to_review(tmp_path):
    # same-account repost banner points the operator at Review (where the fresh awaiting post lands).
    cfg = Config(root=tmp_path)
    _accounts(cfg, {"a0": ["instagram"]})
    _seed_published(cfg, pid="p0", clip="clip_0", account="a0", batch_id="bx")
    resp = _client(cfg).post("/posts/repost/p0")
    html = resp.data.decode()
    assert "/review" in html                                   # a Review link on the same-account repost result


# ── regression: the same-account branch keys on post_id+source_id, NOT source_id alone ──────────────
def _render_outcome(cfg, detail):
    """Render _publish_outcome.html against a hand-built ok ActionResult (pure template routing check)."""
    from fanops.studio.app import create_app
    from fanops.studio.actions_common import ActionResult
    app = create_app(cfg)
    with app.test_request_context("/"):
        from flask import render_template
        return render_template("_publish_outcome.html", result=ActionResult(ok=True, detail=detail))


def test_source_only_detail_does_not_hit_repost_banner(tmp_path):
    # resume/retire/promote_source_studio return {"source_id": ...} WITHOUT post_id — they must NOT render the
    # U8 same-account repost copy (which was the bug when the branch keyed on source_id alone).
    cfg = Config(root=tmp_path); _accounts(cfg, {"a0": ["instagram"]})
    html = _render_outcome(cfg, {"source_id": "s1"})
    assert "Reposted to Review" not in html                    # source-lifecycle result, not a repost
    assert "Done." in html                                     # falls through to the generic ok copy


def test_randomize_detail_not_hijacked_by_repost_branch(tmp_path):
    # randomize_account_schedule returns {"rescheduled", "handle", "source_id"} (no outcome). On the base this
    # already rendered the generic "Done." (it has no `outcome`, so it never reaches the inner rescheduled copy);
    # the U8 same-account branch (post_id+source_id) must NOT hijack it — behavior stays byte-identical to base.
    cfg = Config(root=tmp_path); _accounts(cfg, {"a0": ["instagram"]})
    html = _render_outcome(cfg, {"rescheduled": 3, "handle": "a0", "source_id": "s1"})
    assert "Reposted to Review" not in html                    # NOT hijacked by the repost branch
    assert "Done." in html                                     # same generic-ok copy as the pre-U8 base
