"""U11 — Studio "Hashtags" actions: the operator's GLOBAL ban lane (add / remove a hashtag from the deny-list).
The ONLY mutation on the /hashtags page; everything else there is read-only re-surfacing (views_hashtags). A
thin operator-facing surface over the hashtags.add_ban/remove_ban core writers (flock'd atomic control-file
write); every function returns an ActionResult (ok/error/detail) and NEVER raises into a 500, so the htmx panel
always renders an inline ✓/✗. Mirrors studio/personas.py exactly: validate at the boundary, translate any error
into a one-line message, return a clean result. A banned tag is enforced as a hard filter in vet_hashtags (the
selection path) AND in refresh_persona_corpus (S12 auto-accept); ban beats pin."""
from __future__ import annotations

from fanops.config import Config
from fanops import hashtags as core
from fanops.log import get_logger
from fanops.studio.actions import ActionResult


def add_ban(cfg: Config, tag: str) -> ActionResult:
    """Add ONE hashtag to the global ban list (normalized). A blank tag -> a clean one-line error, never a 500.
    Idempotent (adding a present tag is a no-op). detail carries the normalized tag for the panel message."""
    h = core._norm(tag)
    if not h:
        return ActionResult(ok=False, error="enter a hashtag to ban")
    try:
        core.add_ban(cfg, h)
    except Exception as exc:                             # a lock/write failure must not 500 the panel — but leave a breadcrumb (mirrors actions_run)
        get_logger(cfg)("hashtags", "ban", "ban_add_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"could not ban {h}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"banned": h})


def remove_ban(cfg: Config, tag: str) -> ActionResult:
    """Remove ONE hashtag from the global ban list (normalized). A tag not present is a clean no-op success.
    A blank tag -> a one-line error. detail carries the normalized tag for the panel message."""
    h = core._norm(tag)
    if not h:
        return ActionResult(ok=False, error="enter a hashtag to unban")
    try:
        core.remove_ban(cfg, h)
    except Exception as exc:                             # leave a breadcrumb before degrading (mirrors actions_run) — not a silent swallow
        get_logger(cfg)("hashtags", "ban", "ban_remove_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"could not unban {h}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"unbanned": h})
