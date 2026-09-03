# src/fanops/source_tags_walk.py
"""Unattended source-lock walker and Safari client iteration."""
from __future__ import annotations
from datetime import datetime, timezone

from fanops.ig_hashtag_scrape import ScrapeUnavailable
from fanops.ig_web_scrape import open_web_session
from fanops.log import get_logger
from fanops.source_tags_shortlist import (_prose, _transcript_file_prose, _transcript_json_path)
from fanops.source_tags_sidecar import (_has_catalog, _in_progress, _researched,
                                        load_source_tag_locks)


def _call_opener(opener, cfg, user=None):
    """Call opener(cfg, user=...) when walking, else opener(cfg). Test openers take user=."""
    if user is not None:
        return opener(cfg, user=user)
    return opener(cfg)


def _remember_dead_dump(cfg, client, exc) -> None:
    """Freeze this identity so the next source does not re-hit a rejected dump.

    Skip when the Safari XHR gate already froze this user on this request."""
    user = getattr(client, "_fanops_scrape_user", None)
    if not isinstance(user, str) or not user:
        return
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob, _persist_cooldown
    now = datetime.now(timezone.utc)
    if _is_frozen(_account_rec(_load_cooldown_blob(cfg), user), now):
        return
    _persist_cooldown(cfg, now, reason=type(exc).__name__, user=user)


def _iter_lock_clients(cfg, *, client, open_client_fn, now=None):
    """Yield clients to try for this lock. Empty picker → stop (no opener(cfg) fallthrough)."""
    if client is not None:
        yield client
        return
    from fanops.fanops_hashtags import mark_safari_tick_slot, safari_tick_slot_claimed
    if safari_tick_slot_claimed():
        return
    opener = open_client_fn or open_web_session
    from fanops.ig_web_scrape import _lock_web_users
    now = now or datetime.now(timezone.utc)
    marked = False
    for user in _lock_web_users(cfg, now):
        try:
            cli = _call_opener(opener, cfg, user=user)
        except ScrapeUnavailable:
            continue
        if cli is not None:
            if not getattr(cli, "_fanops_scrape_user", None):
                setattr(cli, "_fanops_scrape_user", user)
            if not marked:
                mark_safari_tick_slot("lock")
                marked = True
            yield cli


def _lock_identity(cli) -> str | None:
    user = getattr(cli, "_fanops_scrape_user", None)
    return user if isinstance(user, str) and user else None


def _advance_lock_client(cfg, current, spare, already):
    """Next client with remaining try_cap room, or None."""
    from fanops.fanops_hashtags import _user_attempt_room
    cand = current
    while cand is not None:
        user = _lock_identity(cand)
        if _user_attempt_room(cfg, user, already=already.get(user or "", 0)) > 0:
            return cand
        cand = next(spare, None)
    return None


def _charge_lock_tag(cfg, cli, already) -> None:
    """Count this tag against try_cap. Wire spend is charged per live XHR in IgWebSession._json."""
    key = _lock_identity(cli) or ""
    already[key] = already.get(key, 0) + 1


def _state_value(source) -> str:
    state = getattr(source, "state", None)
    return str(getattr(state, "value", state) or "")


def lock_ready_sources(cfg, *, client=None, research_fn=None, open_client_fn=None,
                       resolve_fn=None, measure_fn=None) -> None:
    """After produce, before reduce. One source per real lock attempt. Never raises.

    Walk newest created_at first (same comparator as produce_source_ids).
    Missing whisper JSON on a produce-eligible source logs no_transcript and continues
    (must not starve a later ready source). Graph quota does not skip a source —
    leftover scrape-complete rows stamp; unfinished scrape waits for a Safari seat.
    A source that cannot progress (no seat) does not consume the one-attempt slot.
    After the first no_scrape, later sources get a closed opener (no Safari) so
    leftover-complete rows can still cache-stamp. Re-opening Safari per unfinished
    source is what logged the accounts out.
    Does not hydrate from used tags — that path must not mass-stamp researched_at.
    """
    from fanops.source_tags_scrape import ensure_source_lock
    log = get_logger(cfg)
    try:
        from fanops.ledger import Ledger
        led = Ledger.load(cfg)
        table = load_source_tag_locks(cfg)
        opener = open_client_fn

        def _closed(_cfg, user=None, **_k):
            raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")

        sources = list(led.sources.values())
        sources.sort(key=lambda s: (getattr(s, "created_at", None) or "",
                                    str(getattr(s, "id", "") or "")), reverse=True)
        for source in sources:
            if getattr(source, "origin_kind", "native") == "third_party":
                continue
            sid = str(getattr(source, "id", "") or "")
            rec = table.get(sid) if isinstance(table.get(sid), dict) else {}
            if not sid or (_researched(table, sid) and not _in_progress(rec)):
                continue
            jp = _transcript_json_path(cfg, source)
            has_json = bool(jp and jp.exists())
            if not has_json:
                if _state_value(source) in ("pending", "discovered", "retired"):
                    continue
                log("source_tags", sid, "no_transcript", level="error")
                continue
            excerpt = _transcript_file_prose(cfg, source) or _prose(source)
            walked = False
            try:
                walked = bool(ensure_source_lock(cfg, source, excerpt=excerpt, client=client,
                                                 research_fn=research_fn, open_client_fn=opener,
                                                 resolve_fn=resolve_fn, measure_fn=measure_fn))
            except Exception as exc:
                log("source_tags", sid, "error", level="error",
                    err=f"{type(exc).__name__}: {exc}"[:160])
                return
            table = load_source_tag_locks(cfg)
            rec = table.get(sid) if isinstance(table.get(sid), dict) else {}
            if walked or (_researched(table, sid) and _has_catalog(rec) and not _in_progress(rec)):
                return
            opener = _closed
    except Exception as exc:
        log("source_tags", "-", "error", level="error",
            err=f"{type(exc).__name__}: {exc}"[:160])
