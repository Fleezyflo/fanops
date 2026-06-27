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


def preview_compose(cfg: Config, form) -> ActionResult:
    """LIVE TRANSLATION — given the in-progress (UNSAVED) editor form, return what the persona compiles to
    (core.compose_breakdown): the exact casting/hook/caption directives + cut + lead tags, decomposed to the
    lever, with override-shadow + no-op flags. Builds a TRANSIENT Persona from the form — it NEVER calls the
    persisting writers, so personas.json is untouched. An existing persona's curated corpus (not a form field)
    is merged in by `id` so the lead tags are accurate. A bad lever value -> a clean one-line
    error, never a 500. `form` is a Werkzeug MultiDict (or any object with .get/.getlist)."""
    try:
        from fanops.bands import PROFILE_NAMES
        from fanops.config import FRAMING_NAMES
        from fanops.hashtags import TAG_LEANS
        from fanops.personas import CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES, HOOK_TONES

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
            id=(pid or "preview"), voice=form.get("voice", ""), brief=form.get("brief", ""),
            tag_lean=_enum(form.get("tag_lean"), TAG_LEANS, "tag_lean"), hashtag_corpus=corpus,
            content_focus=focus, energy=_enum(form.get("energy"), ENERGY_LEVELS, "energy"),
            hook_angle=_enum(form.get("hook_angle"), HOOK_ANGLES, "hook_angle"),
            hook_tone=_enum(form.get("hook_tone"), HOOK_TONES, "hook_tone"),
            clip_profile=_enum(form.get("clip_profile"), PROFILE_NAMES, "clip_profile"),
            framing=_enum(form.get("framing"), FRAMING_NAMES, "framing"),
            casting_directive=form.get("casting_directive", ""), hook_directive=form.get("hook_directive", ""),
            caption_directive=form.get("caption_directive", ""))
    except ValueError as exc:
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not compose: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=core.compose_breakdown(cfg, per))


def create_persona(cfg: Config, name: str, voice: str = "", tag_lean: str = "",
                   content_focus=None, energy: str = "", hook_angle: str = "", hook_tone: str = "",
                   clip_profile: str = "", framing: str = "", casting_directive: str = "",
                   hook_directive: str = "", caption_directive: str = "") -> ActionResult:
    """Create a NEW persona from the form + the lever engine. Validates a non-blank name, a known tag_lean,
    and every lever value at the A1 write boundary; a duplicate id / bad lean / unknown lever / blank name
    -> a clean one-line error, never a 500."""
    try:
        pid = core.add_persona(cfg, name=name, voice=voice, tag_lean=tag_lean,
                               content_focus=content_focus, energy=energy, hook_angle=hook_angle,
                               hook_tone=hook_tone, clip_profile=clip_profile, framing=framing,
                               casting_directive=casting_directive, hook_directive=hook_directive,
                               caption_directive=caption_directive)
    except ValueError as exc:                            # blank name / unknown lean or lever / duplicate id
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not create persona: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"created": pid})


def edit_persona(cfg: Config, pid: str, name: str = "", voice: str = "", tag_lean: str = "",
                 content_focus=None, energy: str = "", hook_angle: str = "", hook_tone: str = "",
                 clip_profile: str = "", framing: str = "", brief: str = "", casting_directive: str = "",
                 hook_directive: str = "", caption_directive: str = "") -> ActionResult:
    """Save edits to a persona (name/voice/tag_lean + every lever + the locked brief). The edit form is
    AUTHORITATIVE: an unchecked/blank lever CLEARS it, and the brief textarea is pre-filled so a Save
    preserves it (emptying it clears the lock). Unknown id / bad lean / unknown lever / blank name -> a
    clean one-line error."""
    pid = (pid or "").strip()
    if not pid:
        return ActionResult(ok=False, error="no persona selected")
    try:
        core.update_persona(cfg, pid, name=name, voice=voice, tag_lean=tag_lean,
                            content_focus=(content_focus or []), energy=energy, hook_angle=hook_angle,
                            hook_tone=hook_tone, clip_profile=clip_profile, framing=framing, brief=brief,
                            casting_directive=casting_directive, hook_directive=hook_directive,
                            caption_directive=caption_directive)
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
