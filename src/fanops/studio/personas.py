"""Studio "Personas" actions (A2): create / edit / delete a first-class Persona, curate its hashtag
corpus, and connect accounts to it — ENTIRELY in the browser (no personas.json / accounts.json
hand-edit). A thin operator-facing surface over the A1 core writers (fanops.personas) + the account
link writer (accounts.link_persona); every function returns an ActionResult (ok/error/detail) and
NEVER raises into a 500, so the htmx panel always renders an inline ✓/✗. Mirrors golive.py exactly:
validate at the boundary, translate ValueError/KeyError into a one-line error, return a clean result."""
from __future__ import annotations

from fanops.config import Config
from fanops import personas as core
from fanops.accounts import link_persona as _link_persona
from fanops.studio.actions import ActionResult


def _intake(genre: str = "", language: str = "", refs: str = "", notes: str = "") -> dict:
    """Build the persona intake dict from the form fields — only non-blank keys (so an empty intake is
    {} not a bag of empties). `refs` is a comma/space list of reference accounts -> a clean list; it
    seeds B3's per-persona hashtag research (genre/language/audience steer what tags to propose)."""
    out: dict = {}
    g = (genre or "").strip()
    lang = (language or "").strip()
    n = (notes or "").strip()
    ref_list = [r.strip() for r in (refs or "").replace(",", " ").split() if r.strip()]
    if g: out["genre"] = g
    if lang: out["language"] = lang
    if ref_list: out["reference_accounts"] = ref_list
    if n: out["notes"] = n
    return out


def create_persona(cfg: Config, name: str, voice: str = "", tag_lean: str = "",
                   genre: str = "", language: str = "", refs: str = "", notes: str = "",
                   content_focus=None, energy: str = "", hook_angle: str = "", hook_tone: str = "",
                   clip_profile: str = "", framing: str = "", casting_directive: str = "",
                   hook_directive: str = "", caption_directive: str = "", clip_count="") -> ActionResult:
    """Create a NEW persona from the intake form + the lever engine. Validates a non-blank name, a known
    tag_lean, and every lever value at the A1 write boundary; a duplicate id / bad lean / unknown lever /
    bad clip_count / blank name -> a clean one-line error, never a 500."""
    try:
        pid = core.add_persona(cfg, name=name, voice=voice, tag_lean=tag_lean,
                               intake=_intake(genre, language, refs, notes),
                               content_focus=content_focus, energy=energy, hook_angle=hook_angle,
                               hook_tone=hook_tone, clip_profile=clip_profile, framing=framing,
                               casting_directive=casting_directive, hook_directive=hook_directive,
                               caption_directive=caption_directive, clip_count=clip_count)
    except ValueError as exc:                            # blank name / unknown lean or lever / duplicate id
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not create persona: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"created": pid})


def edit_persona(cfg: Config, pid: str, name: str = "", voice: str = "", tag_lean: str = "",
                 genre: str = "", language: str = "", refs: str = "", notes: str = "",
                 content_focus=None, energy: str = "", hook_angle: str = "", hook_tone: str = "",
                 clip_profile: str = "", framing: str = "", brief: str = "", casting_directive: str = "",
                 hook_directive: str = "", caption_directive: str = "", clip_count="") -> ActionResult:
    """Save edits to a persona (name/voice/tag_lean + the full intake + every lever + the locked brief). The
    edit form is AUTHORITATIVE: an unchecked/blank lever CLEARS it, and the brief textarea is pre-filled so a
    Save preserves it (emptying it clears the lock). Unknown id / bad lean / unknown lever / blank name -> a
    clean one-line error."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.update_persona(cfg, pid, name=name, voice=voice, tag_lean=tag_lean,
                            intake=_intake(genre, language, refs, notes),
                            content_focus=(content_focus or []), energy=energy, hook_angle=hook_angle,
                            hook_tone=hook_tone, clip_profile=clip_profile, framing=framing, brief=brief,
                            casting_directive=casting_directive, hook_directive=hook_directive,
                            caption_directive=caption_directive, clip_count=clip_count)
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except ValueError as exc:                            # unknown lean or lever / blank name
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not save {pid}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"saved": pid})


def delete_persona(cfg: Config, pid: str) -> ActionResult:
    """Delete a persona. Accounts still linked keep the dangling id (load hydration falls open to their
    inline persona — never crashes). Unknown id / blank -> a clean one-line error."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.delete_persona(cfg, pid)
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not delete {pid}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"deleted": pid})


def add_corpus_tag(cfg: Config, pid: str, tag: str) -> ActionResult:
    """Add ONE hashtag to a persona's curated corpus (normalized, deduped, capped). Empty tag / corpus
    full / unknown id -> a clean one-line error (the cap is surfaced, never a silent drop)."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.add_corpus_tag(cfg, pid, tag)
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except ValueError as exc:                            # empty tag / corpus full
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not add tag: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"persona": pid, "added": tag})


def remove_corpus_tag(cfg: Config, pid: str, tag: str) -> ActionResult:
    """Remove ONE hashtag from a persona's corpus (normalization-insensitive). Unknown id / blank ->
    a clean one-line error; a tag not present is a clean no-op."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.remove_corpus_tag(cfg, pid, tag)
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not remove tag: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"persona": pid, "removed": tag})


def connect_account(cfg: Config, handle: str, persona_id: str) -> ActionResult:
    """Connect ONE account to a persona (set Account.persona_id). A BLANK persona_id DISCONNECTS (the
    account's inline persona/tag_lean stand again). A non-blank id is checked to EXIST at call time
    (BEST-EFFORT, not transactional — a concurrent delete between the check and the link could leave a
    dangling id; harmless, since load hydration falls open to the inline persona). Unknown handle ->
    a clean one-line error."""
    handle = (handle or "").strip()
    pid = (persona_id or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    if pid and core.Personas.load(cfg).get(pid) is None:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    try:
        _link_persona(cfg, handle, pid)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not connect {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"handle": handle, "persona_id": pid or None})


def recommend_tag(cfg: Config, pid: str, tag: str) -> ActionResult:
    """B2: fetch a hashtag's live Graph metrics so the operator can SEE its reach before adding it to a
    persona's corpus. Validates the persona exists + a non-blank tag; returns the metrics in detail (the
    panel shows engagement + an 'Add to corpus' button). Does NOT add — adding is a separate confirmed
    step (add_corpus_tag). A Graph miss / no creds / exhausted budget -> a clean one-line error, never 500."""
    pid = (pid or "").strip(); tag = (tag or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    if not tag:
        return ActionResult(ok=False, error="enter a hashtag to check")
    if core.Personas.load(cfg).get(pid) is None:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    from fanops.meta_graph import tag_metrics             # function-local so a missing Meta app never breaks import
    m = tag_metrics(cfg, tag)
    if not m.get("resolved"):
        return ActionResult(ok=False, error=m.get("error") or "could not fetch metrics for that tag")
    return ActionResult(ok=True, detail={"persona": pid, "tag": m["tag"],
                                         "engagement": m.get("engagement"), "recommend": True})


def research_corpus(cfg: Config, pid: str) -> ActionResult:
    """M3: LIVE discovery — propose the hashtags the category's currently-winning posts use that this
    persona doesn't yet carry (Graph co-occurrence harvest), each with its co-occurrence evidence; the
    panel renders them with one-click Add. FAIL-OPEN: no Meta creds / nothing fresh -> the offline
    research_corpus re-rank (wrapped as dicts inside core.discover_corpus). Unknown id -> a clean
    one-line error, never a 500."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        proposals = core.discover_corpus(cfg, pid)
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"research failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"persona": pid, "proposals": proposals})


def run_migration(cfg: Config) -> ActionResult:
    """One-click: lift every account's inline persona string into a first-class Persona and link it
    (idempotent). The bridge from the brief-seeded persona strings to editable, connectable records."""
    try:
        out = core.migrate_from_accounts(cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"migration failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=out)
