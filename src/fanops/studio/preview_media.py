"""Lock-free preview burn for Review WYSIWYG — renders to the SAME content-addressed path approve uses."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.crosspost import render_account_file, account_render_spec


def preview_media_path(cfg: Config, led: Ledger, post_id: str) -> str | None:
    post = led.posts.get(post_id)
    if post is None:
        return None
    if post.render_id:
        r = led.renders.get(post.render_id)
        if r and r.path and Path(r.path).exists():
            return r.path
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return None
    hook = (post.variant_hook or "").strip()
    if hook and cfg.creative_variation:
        acct = next((a for a in Accounts.load(cfg).accounts if a.handle == post.account), None)
        mom = led.moments.get(clip.parent_id)
        src = led.sources.get(mom.parent_id) if mom is not None else None
        try:
            rid, *_ = account_render_spec(cfg, clip=clip, hook=hook, acct=acct)
            vpath = cfg.render_path(src.batch_id if src else None, src.id if src else None, rid, clip.aspect)
            if Path(vpath).exists() and Path(vpath).stat().st_size > 0:
                return vpath
        except Exception:
            pass
        try:
            plan = render_account_file(led, cfg, post=post, acct=acct, target_clip=clip, src=src, caller="preview")
            if plan.vpath and Path(plan.vpath).exists():
                return plan.vpath
        except Exception:
            pass
    if post.media_urls:
        raw = post.media_urls[0]
        if raw.startswith("file://"):
            lp = raw[7:]
            if Path(lp).exists():
                return lp
        elif not raw.startswith(("http://", "https://")) and Path(raw).exists():
            return raw
    return clip.path if clip.path and Path(clip.path).exists() else None
