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
                   genre: str = "", language: str = "", refs: str = "", notes: str = "") -> ActionResult:
    """Create a NEW persona from the intake form. Validates a non-blank name + a known tag_lean at the
    A1 write boundary; a duplicate id / bad lean / blank name -> a clean one-line error, never a 500."""
    try:
        pid = core.add_persona(cfg, name=name, voice=voice, tag_lean=tag_lean,
                               intake=_intake(genre, language, refs, notes))
    except ValueError as exc:                            # blank name / unknown lean / duplicate id
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not create persona: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"created": pid})


def edit_persona(cfg: Config, pid: str, name: str = "", voice: str = "", tag_lean: str = "",
                 genre: str = "", language: str = "", refs: str = "", notes: str = "") -> ActionResult:
    """Save edits to a persona (name/voice/tag_lean + the full intake). tag_lean="" clears the lean.
    Unknown id / bad lean / blank name -> a clean one-line error."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.update_persona(cfg, pid, name=name, voice=voice, tag_lean=tag_lean,
                            intake=_intake(genre, language, refs, notes))
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except ValueError as exc:                            # unknown lean / blank name
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


def run_migration(cfg: Config) -> ActionResult:
    """One-click: lift every account's inline persona string into a first-class Persona and link it
    (idempotent). The bridge from the brief-seeded persona strings to editable, connectable records."""
    try:
        out = core.migrate_from_accounts(cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"migration failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=out)
