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


def _parse_niche(raw: str) -> list[str]:
    """Split the niche form value on commas and newlines ONLY. Whitespace is not a separator — an entry
    with an inner space stays one token and is refused at the store by tag_defect (no silent join/split)."""
    out: list[str] = []
    for part in (raw or "").replace(",", "\n").splitlines():
        s = part.strip()
        if s:
            out.append(s)
    return out


def preview_compose(cfg: Config, form) -> ActionResult:
    """LIVE TRANSLATION — given the in-progress (UNSAVED) editor form, return what the persona compiles to
    (core.compose_breakdown): the exact casting/hook/caption directives + cut + lead tags, decomposed to the
    lever, with override-shadow + no-op flags. Builds a TRANSIENT Persona from the form — it NEVER calls the
    persisting writers, so personas.json is untouched. An existing persona's curated corpus (not a form field)
    is merged in by `id` so the lead tags are accurate. A bad lever value -> a clean one-line error, never a
    500. `form` is a Werkzeug MultiDict (or any object with .get/.getlist). The five clean levers only:
    voice, content_focus, selection_scope, hook_angle (+ the saved corpus); the cut is DERIVED from content_focus."""
    try:
        from fanops.personas import CONTENT_FOCUS, SELECTION_SCOPE_LEVELS, HOOK_ANGLES

        def _enum(value, allowed, label):
            v = (value or "").strip()
            if v and v not in allowed: raise ValueError(f"unknown {label}: {v}")
            return v or None

        pid = (form.get("id") or "").strip()
        corpus: list = []
        if pid:                                          # an existing persona keeps its curated corpus (not a form field)
            try:
                saved = core.Personas.load(cfg).get(pid)
                if saved is not None: corpus = list(saved.hashtag_corpus)
            except Exception:
                corpus = []
        focus = [c for c in form.getlist("content_focus") if c]
        for c in focus:
            if c not in CONTENT_FOCUS: raise ValueError(f"unknown content_focus: {c}")
        per = core.Persona(
            id=(pid or "preview"), voice=form.get("voice", ""), hashtag_corpus=corpus,
            content_focus=focus, selection_scope=_enum(form.get("selection_scope"), SELECTION_SCOPE_LEVELS, "selection_scope"),
            hook_angle=_enum(form.get("hook_angle"), HOOK_ANGLES, "hook_angle"))
    except ValueError as exc:
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not compose: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=core.compose_breakdown(cfg, per))


def create_persona(cfg: Config, name: str, voice: str = "",
                   content_focus=None, selection_scope: str = "", hook_angle: str = "") -> ActionResult:
    """Create a NEW persona from the five clean levers (voice + content_focus/selection_scope/hook_angle; the corpus is
    curated on the card, genre via Research). Validates a non-blank name + each lever value at the A1 write
    boundary; a duplicate id / unknown lever / blank name -> a clean one-line error, never a 500. The cut
    (length) is DERIVED from content_focus."""
    try:
        pid = core.add_persona(cfg, name=name, voice=voice,
                               content_focus=content_focus, selection_scope=selection_scope, hook_angle=hook_angle)
    except ValueError as exc:                            # blank name / unknown lean or lever / duplicate id
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not create persona: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"created": pid})


def edit_persona(cfg: Config, pid: str, name: str = "", voice: str = "",
                 content_focus=None, selection_scope: str = "", hook_angle: str = "") -> ActionResult:
    """Save edits to a persona's five clean levers (name/voice + content_focus/selection_scope/hook_angle). The edit
    form is AUTHORITATIVE: an unchecked/blank lever CLEARS it. The cut (length) is DERIVED from content_focus,
    so there is no length/framing knob. Unknown id / unknown lever / blank name -> a clean one-line error."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.update_persona(cfg, pid, name=name, voice=voice,
                            content_focus=(content_focus or []), selection_scope=selection_scope, hook_angle=hook_angle)
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


def set_niche(cfg: Config, pid: str, niche: str = "") -> ActionResult:
    """Save the persona's DECLARED niche terms — `Persona.niche`, the list the next measurement pass
    searches on, and therefore the operator's most direct lever over which hashtags get discovered and
    measured for this persona. Parse is comma/newline only; store validation refuses defective entries."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.update_persona(cfg, pid, niche=_parse_niche(niche))
    except KeyError:
        return ActionResult(ok=False, error=f"no such persona: {pid}")
    except ValueError as exc:
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not save the niche: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"persona": pid, "niche": _parse_niche(niche)})


def run_migration(cfg: Config) -> ActionResult:
    """One-click: lift every account's inline persona string into a first-class Persona and link it
    (idempotent). The bridge from the brief-seeded persona strings to editable, connectable records."""
    try:
        out = core.migrate_from_accounts(cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"migration failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=out)
