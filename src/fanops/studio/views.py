"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.errors import fail_open
from fanops.ledger import Ledger
# Facade re-exports: the names consumers reach via `fanops.studio.views` / `views.X` (templates / app.py /
# tests). Dead re-exports (no facade consumer AND no internal use here) were trimmed — every trimmed symbol
# still lives in its home submodule (views_common/_review/_results); this is just the public views surface.
# F401-silenced because each name is re-exported, not referenced within this file.
from fanops.studio import views_common   # module alias for build_system_strip's health/banner delegates (D13b)
from fanops.studio.views_common import (IMMINENT_THRESHOLD_MINUTES, GRID_PAGE_SIZE, paginate, TERM_DEFS, term_def, accounts_in, _imminent, suggest_time, lineage_maps, clip_source_of, source_universe_for_clips, account_color_hue)  # noqa: F401
from fanops.studio.views_review import (SurfacePost, ReviewCard, ProvChip, provenance_chips, _surface, source_choices, _empty_cell_reason, review_matrix, account_lanes, _STATE_TO_BUCKET, review_buckets, review_counts, review_progress, source_universe, account_pivot_rows, review_feed_rows, review_awaiting_by_account_led, review_scope_bucket_counts, group_review_by_source, surface_for_post, group_review_by_batch, awaiting_moment_count, review_awaiting_by_account)  # noqa: F401
from fanops.studio.views_results import (ScheduleRow, ScheduleLanes, LiftRow, publish_readiness, explain_suggested_time, schedule_rows, schedule_lanes, due_publish_plan, DuePublishPlan, schedule_cockpit, ScheduleCockpit, inflight_watch, InflightWatchRow, ScheduleChip, DayCell, CalendarMonth, schedule_calendar_month, schedule_bucket_split, PostedRow, posted_library, posted_archive_rows, posted_batch_rollup, lineage_stats, account_median_deltas, metric_peaks, bar_pct, group_posted_by_day, lift_rows, whats_working_panel, DimInsightRow, classify_post_delivery, failure_rollup, operator_error, failure_label, tag_exposure)  # noqa: F401
from fanops.studio.views_live import (LiveMediaRow, live_library, live_library_scope)  # noqa: F401  # MOL-27: the "viewed there, not authored here" Live library read-model (imported_media only, disjoint from Posted)
from fanops.studio.views_library import (STAGES, library_catalog, source_pipeline_map, source_progress)  # noqa: F401
from fanops.studio.views_home import (HomeStatus, HomeBatch, SpineStage, WorkflowSpine, home_status, home_batches, home_accounts_panel, home_source_gallery, home_week_calendar, account_work_counts, review_handoff, review_nav_params, zero_post_clips, metrics_stale_hint, build_spine, load_account_stats)  # noqa: F401
from fanops.studio.views_golive import (GoLiveChannel, ChannelReadiness, GoLiveAccount, GoLiveStatus, AccountOnboardingCard, golive_accounts, golive_demoted_accounts, golive_status, channel_readiness, onboarding_account_cards, _blocker_priority)  # noqa: F401
from fanops.studio.views_run import (pipeline_status, errored_sources, run_next_step, asset_catalog, pending_stitches, pending_stitch_drafts, review_candidates, publish_queue, gate_rows)  # noqa: F401


def led_for_request(cfg: Config) -> Ledger:
    """One Ledger.load per Studio HTTP request. Outside a request (CLI/tests): load normally."""
    with fail_open("studio.views.led_for_request"):
        from flask import g, has_request_context
        if has_request_context():
            led = getattr(g, "fanops_ledger", None)
            if led is None:
                led = Ledger.load(cfg)
                g.fanops_ledger = led
            return led
    return Ledger.load(cfg)


@dataclass
class PersonaCard:
    """A2: one first-class Persona for the Personas page — editable fields + linked accounts.
    Posted hashtags are the source lock, not `corpus`. NO secret."""
    id: str
    name: str
    voice: str
    corpus: list                       # leftover Persona.hashtag_corpus field; not the caption menu
    niche: list                        # declared territory (identity lever; not caption tags)
    linked_handles: list               # accounts whose persona_id points at this persona
    discovery_roots: list = field(default_factory=list)  # unused; vocab expander is deleted
    reach_tags: list = field(default_factory=list)   # leftover cache overlay on corpus; not membership
    reach_means: dict = field(default_factory=dict)  # leftover {corpus tag -> media_count}
    # Lever engine: the per-characteristic levers + the COMPOSED instruction the pipeline will read
    # ("what the AI will read") — so the operator sees their config's exact downstream effect on the card.
    content_focus: Optional[str] = None              # MOL-523: free-text editorial focus (was the token multi-select)
    cut_policy: list = field(default_factory=list)    # MOL-523: the moment-kind tokens that DERIVE cut length+framing
    selection_scope: Optional[str] = None
    hook_angle: Optional[str] = None
    intensity: Optional[str] = None
    clip_profile: Optional[str] = None
    framing: Optional[str] = None
    instruction: str = ""              # the COMPILED casting directive (the headline "AI reads ->")
    # TRANSPARENCY facts (length band + lead tags) derived from the REAL resolvers — so the operator sees, on
    # the card, exactly what the config produces.
    length_band: str = ""
    lead_tags: list = field(default_factory=list)
    # M3 DIRECTIVE ENGINE: the COMPILED per-dimension directive the LLM actually reads (so the operator sees
    # exactly what each lever produces). (M3e: the freeform OVERRIDE text fields were retired with the levers.)
    hook_text: str = ""
    caption_text: str = ""
    # M4: the LEVER MANIFEST — per editable lever {key,label,channels,value,produces,source,health}, derived
    # from the registry + the SAME resolvers the pipeline runs (no-drift). The drawer renders it as a health row.
    lever_manifest: list = field(default_factory=list)
    # S05: drawer-only effective-persona read projection (fail-open defaults).
    account_provenance: list = field(default_factory=list)   # [{handle, fields:[{name, value, source}]}]
    lever_detail: list = field(default_factory=list)         # [{key, label, value, catalog_does, option_effect, produces, health, crosswalk_note}]
    corpus_tags: list = field(default_factory=list)          # unused; templates do not render corpus chips
    corpus_refreshed_at: str = ""                            # unused; Layer B derive is deleted


def _account_provenance(cfg: Config, persona, handles: list) -> list:
    """Per linked account, mirror accounts._hydrate_from_personas field sourcing for the drawer provenance table."""
    if not handles:
        return []
    try:
        from fanops.accounts import Account
        from fanops.models import validate_account_handle
        from fanops.personas import resolved_cut_spec
        raw = json.loads(cfg.accounts_path.read_text(encoding="utf-8"))
        rows = {}
        for r in raw.get("accounts", []):
            if not isinstance(r, dict): continue
            try: rows[validate_account_handle(r.get("handle") or "")] = r
            except ValueError: continue
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", getattr(persona, "id", "-"), "provenance_error", err=str(exc)[:160])
        return []
    _prof, _fr = resolved_cut_spec(persona)
    out: list = []
    for h in handles:
        row = rows.get(h) or rows.get("@" + h.lstrip("@"))
        if not row: continue
        acc = Account(**row)
        fields: list = []
        if (persona.voice or "").strip():
            fields.append({"name": "voice", "value": persona.voice.strip(), "source": "persona"})
        else:
            fields.append({"name": "voice", "value": acc.persona or "", "source": "account"})
        fields.append({"name": "hashtag_corpus", "value": list(persona.hashtag_corpus), "source": "persona"})
        # MOL-523: content_focus is free TEXT; the token list that derives the cut moved to cut_policy.
        fields.append({"name": "content_focus", "value": persona.content_focus or "", "source": "persona"})
        fields.append({"name": "cut_policy", "value": list(persona.cut_policy or []), "source": "persona"})
        fields.append({"name": "intensity", "value": persona.intensity or "", "source": "persona"})
        fields.append({"name": "selection_scope", "value": persona.selection_scope or "", "source": "persona"})
        fields.append({"name": "hook_angle", "value": persona.hook_angle or "", "source": "persona"})
        if _prof:
            fields.append({"name": "clip_profile", "value": _prof, "source": "persona"})
        else:
            fields.append({"name": "clip_profile", "value": acc.clip_profile or "", "source": "account"})
        if _fr:
            fields.append({"name": "framing", "value": _fr, "source": "persona"})
        else:
            fields.append({"name": "framing", "value": acc.framing or "", "source": "account"})
        out.append({"handle": h, "fields": fields})
    return out


def _lever_detail_rows(cfg: Config, persona, manifest_rows: list, catalog: list, effects: dict) -> list:
    """Join manifest rows with lever_catalog does + _LEVER_EFFECTS option effects + optional archetype crosswalk."""
    xnote = ""
    try:
        from fanops.lever_docs import archetype_crosswalk_rows
        xw = next((r for r in archetype_crosswalk_rows() if r["id"] == persona.id), None)
        if xw:
            foc = ", ".join(xw["content_focus"]) if xw["content_focus"] else "—"
            xnote = f"{xw['name']}: {foc} · {xw['hook_angle']} · {xw['selection_scope']} · {xw['intensity']}"
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", getattr(persona, "id", "-"), "crosswalk_error", err=str(exc)[:160])
        xnote = ""
    cat_by = {lv["key"]: lv for lv in (catalog or [])}
    out: list = []
    for i, row in enumerate(manifest_rows or []):
        try:
            key = row.get("key") or ""
            cat = cat_by.get(key, {})
            val = row.get("value")
            eff_map = (effects or {}).get(key) or {}
            if isinstance(val, list):    # MOL-523: the multi-value lever is cut_policy now, not content_focus
                parts = [eff_map[v] for v in val if v in eff_map]
                opt_eff = " · ".join(parts) if parts else "—"
            elif isinstance(val, str) and val.strip():
                opt_eff = eff_map.get(val.strip()) or "—"
            else:
                opt_eff = "—"
            produces = row.get("produces")
            if isinstance(produces, list):
                prod = " ".join(str(x) for x in produces if x) or "—"
            else:
                prod = produces or "—"
            out.append({"key": key, "label": row.get("label") or key, "value": val,
                          "catalog_does": cat.get("does") or "—", "option_effect": opt_eff,
                          "produces": prod, "health": row.get("health") or "—",
                          "crosswalk_note": xnote if i == 0 else ""})
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("personas", getattr(persona, "id", "-"), "lever_detail_error",
                           key=row.get("key", ""), err=str(exc)[:160])
            out.append({"key": row.get("key", ""), "label": row.get("label", ""), "value": "—",
                          "catalog_does": "—", "option_effect": "—", "produces": "—", "health": "—",
                          "crosswalk_note": ""})
    return out


@dataclass
class PersonaAccountLink:
    """A2: one account row for the Personas "connect" section — its current persona link (or None), so
    the operator can connect/disconnect each account to a persona from a dropdown."""
    handle: str
    persona_id: Optional[str]


@dataclass
class PersonasPage:
    personas: list                     # PersonaCard
    accounts: list                     # PersonaAccountLink


def personas_page(cfg: Config, *, led: Optional[Ledger] = None) -> "PersonasPage":
    """The Personas-page read-model: every persona as a card (linked account handles + levers)
    + every account's current persona link (connect dropdown). Posted hashtags are the source lock.
    Fail-open: a corrupt personas.json / accounts.json -> an EMPTY page (the surface never 500s),
    mirroring golive_accounts. `led` is accepted for call-compat; the surface reads no ledger."""
    try:
        from fanops.personas import (Personas, compose_persona_instruction, persona_facts,   # lazy: personas imports accounts (in migrate) -> avoid a load cycle
                                     hook_directive, caption_directive, resolved_cut_spec, manifest)
        reg = Personas.load(cfg)
        accts = Accounts.load(cfg).accounts
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", "-", "read_error", err=str(exc)[:160])
        return PersonasPage(personas=[], accounts=[])
    by_pid: dict = {}
    for a in accts:
        if getattr(a, "persona_id", None):
            by_pid.setdefault(a.persona_id, []).append(a.handle)
    # Surface each corpus BIGGEST FIRST (`ranked_tags` is size-first — MOL-692) and flag the tags that
    # actually carry a measurement. A corpus tag should always be measured — it can only have entered by
    # being measured — but a cache entry can expire between derivations, so the ★ still gates on a present
    # measurement rather than assuming it.
    from fanops.hashtags import load_measurements, ranked_tags, _norm, tag_size
    cache = load_measurements(cfg)
    store = ranked_tags(cache) or None
    rank = {t: i for i, t in enumerate(store or [])}
    means = {t: tag_size(r) for t, r in cache.items() if tag_size(r) is not None}
    def _ranked(corpus):
        return sorted((_norm(t) for t in corpus), key=lambda n: rank.get(n, 10 ** 6))
    from fanops.personas import lever_catalog
    _cat = lever_catalog()
    _fx = {lv["key"]: {o["value"]: o["effect"] for o in lv["options"]} for lv in _cat}
    cards: list = []
    for p in reg.all():
        facts = persona_facts(cfg, p)
        mf = manifest(cfg, p)
        try:
            acct_prov = _account_provenance(cfg, p, by_pid.get(p.id, []))
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("personas", p.id, "provenance_error", err=str(exc)[:160])
            acct_prov = []
        lev_detail = _lever_detail_rows(cfg, p, mf, _cat, _fx)
        cards.append(PersonaCard(id=p.id, name=p.name, voice=p.voice,
                         corpus=_ranked(p.hashtag_corpus), niche=list(p.niche),
                         discovery_roots=[],
                         linked_handles=by_pid.get(p.id, []),
                         reach_tags=[_norm(t) for t in p.hashtag_corpus if _norm(t) in means],
                         reach_means={_norm(t): means[_norm(t)] for t in p.hashtag_corpus if _norm(t) in means},
                         content_focus=p.content_focus, cut_policy=list(p.cut_policy or []),
                         selection_scope=p.selection_scope, hook_angle=p.hook_angle,
                         intensity=p.intensity,
                         clip_profile=resolved_cut_spec(p)[0], framing=facts["framing"],
                         instruction=compose_persona_instruction(p),
                         length_band=facts["length_band"], lead_tags=facts["lead_tags"],
                         hook_text=str(hook_directive(p)), caption_text=caption_directive(p),
                         lever_manifest=mf, account_provenance=acct_prov, lever_detail=lev_detail))
    links = [PersonaAccountLink(handle=a.handle, persona_id=getattr(a, "persona_id", None)) for a in accts]
    return PersonasPage(personas=cards, accounts=links)


def _publish_mode_label(cfg: Config) -> str:
    """Thin delegate to cfg.effective_publish_mode (UI-LIE-FIX root: the truth lives on Config so
    every caller — display, hx-confirm, friendly error — reads the SAME source). Kept as the
    historical helper name for the call sites that already use it."""
    return cfg.effective_publish_mode()


def _postiz_down_on_helper_raise(cfg: Config) -> dict:
    """Outer except for postiz_health_for_banner: show unknown if a channel routes to postiz OR the
    route-check failed; hide only when we can prove no channel routes to postiz. Lives here so we
    never re-touch publish-hot views_common for a strip-only raise shield (MOL-963 R2d)."""
    unknown = {"show": True, "danger": False, "status": None,
               "hint": "Postiz health unknown (read failed)"}
    try:
        from fanops.accounts import load_accounts_safe
        accounts, err = load_accounts_safe(cfg)
        if err:
            return {**unknown, "hint": "Postiz health unknown (route check failed)"}
        for a in accounts.active():
            for plat in a.platforms:
                if accounts.effective_provider(a.handle, plat) == "postiz":
                    return unknown
        return {"show": False, "danger": False, "status": None, "hint": ""}
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("system_strip", "-", "postiz_route_check_error", err=str(exc)[:160])
        return {**unknown, "hint": "Postiz health unknown (route check failed)"}


def build_system_strip(cfg: Config) -> dict:
    """Global system strip read-model: LIVE/DRYRUN mode + blocked gate count + failed-post alert. Health dots lazy-load via htmx."""
    from fanops.log import get_logger                     # a strip sub-read failure is RECORDED, never a silently-zeroed badge
    from fanops.health import SnapshotFreshness, read_strip_metrics
    strip_metrics_unknown = False
    try:
        sr = read_strip_metrics(cfg)
        if sr.freshness is SnapshotFreshness.FRESH and isinstance(sr.data, dict):
            m = sr.data
            blocked = int(m.get("blocked_gates") or 0)
            recoverable = int(m.get("recoverable_sources") or 0)
            errored_first_id = m.get("errored_first_id")
            failed = int(m.get("failed") or 0)
        else:
            # Missing/stale/unreadable → unknown, never calm zero. Do not Ledger.load on the strip path.
            strip_metrics_unknown = True
            blocked = None; recoverable = 0; errored_first_id = None; failed = 0
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "pipeline_status_error", err=str(exc)[:160])
        strip_metrics_unknown = True
        blocked = None; recoverable = 0; errored_first_id = None; failed = 0
    errored = recoverable
    # Leg 2 (Insight): the one external gate — a persisted breadcrumb means Graph media-insights was refused
    # for lack of instagram_manage_insights, so IG performance is frozen at its last snapshot until granted.
    try:
        from fanops.meta_graph import insights_blocked_signal
        insights_blocked = insights_blocked_signal(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "insights_blocked_error", err=str(exc)[:160])
        insights_blocked = False
    try:
        from fanops.health_model import half_live_state
        hl = half_live_state(cfg)
        half_live, half_live_hint = hl.is_half_live, (hl.hint or "")
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "half_live_error", err=str(exc)[:160])
        half_live, half_live_hint = True, "readiness unavailable — not confirmed LIVE"
    # D13b: Postiz-down banner — snapshot-only (deps_health.json).
    # Helper raise → unknown when a channel routes to postiz OR the route-check itself failed; else hide.
    try:
        postiz_down = views_common.postiz_health_for_banner(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "postiz_down_error", err=str(exc)[:160])
        postiz_down = _postiz_down_on_helper_raise(cfg)
    return {"is_live": cfg.is_live, "mode": _publish_mode_label(cfg), "blocked_gates": blocked,
            "strip_metrics_unknown": strip_metrics_unknown,
            "recoverable_sources": recoverable, "failed": failed, "insights_blocked": insights_blocked,
            "errored_sources": errored, "errored_first_id": errored_first_id,
            "half_live": half_live, "half_live_hint": half_live_hint,
            "postiz_down": postiz_down}




def resolve_account_handle(raw: str, cfg: Config) -> str:
    """Map ?account= to the canonical ledger/accounts handle (@-agnostic)."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    from fanops.models import validate_account_handle
    try:
        bare = validate_account_handle(raw)
    except ValueError:
        bare = raw.lstrip("@").lower()
    with fail_open("studio.views.resolve_account_handle"):
        for a in Accounts.load(cfg).active():
            if a.handle == bare:
                return a.handle
    return raw  # unknown handle — preserve operator input for empty-state copy


def schedule_auto_ship(cfg: Config) -> bool:
    """Live + daemon alive — Schedule is read-only; posts ship on the clock."""
    if not cfg.is_live:
        return False
    dh = daemon_health(cfg)
    return bool(dh and dh.get("verdict") == "alive")

def daemon_health(cfg: Config) -> Optional[dict]:
    """Fail-open liveness of the launchd PIPELINE DRIVER for the Home banner. Returns daemon.status()'s
    verdict dict (loaded/pid/last_exit/heartbeat_age_s/verdict), or None when it can't be judged — non-darwin,
    launchctl absent, or any error — so Home never 500s and a non-mac dev box shows no false alarm. The
    detection already exists in daemon.status(); this only SURFACES it where the operator looks. Lazy import
    keeps the launchd/subprocess dependency off the core view path; htmx-loaded on-demand (mirrors
    /golive/health) so it never runs a subprocess on the spine's every-surface home_status read.

    Enriched with `interval`/`pending_gates` so the banner can frame a NOT-INSTALLED driver as OPT-IN
    rather than a fault; gates are always answered by the LLM, so there is no AI on/off to disclose."""
    with fail_open("studio.views.daemon_health"):
        from fanops import daemon
        from fanops import pipeline
        interval = daemon.installed_interval(cfg) or 600
        rep = daemon.status(cfg, interval=interval)
        pending_gates = None                                   # never let a torn agent_io dir 500 the banner
        with fail_open("studio.views.daemon_health.pending_gates"):
            pending_gates = pipeline.pending_gate_count(cfg)   # need-aware truth: claude runs ONLY to answer these
        siblings = daemon.sibling_agents_status()
        from fanops.pipeline_run import run_status_line
        out = {**rep, "interval": interval,
               "pending_gates": pending_gates, "siblings": siblings,
               "responder": daemon.resolve_responder(cfg)}
        run_line = run_status_line(cfg)
        if run_line != "run=idle":
            out["run_line"] = run_line
        return out
    return None


def daemon_health_strip(cfg: Config) -> Optional[dict]:
    """Home daemon partial — snapshot facts + live heartbeat/activity via project_daemon_strip."""
    from fanops.health import SnapshotFreshness, read_daemon_strip_snapshot
    from fanops.health_model import heartbeat_stale, project_daemon_strip, daemon_progress
    from fanops import pipeline
    from fanops.pipeline_run import run_status_line
    sr = read_daemon_strip_snapshot(cfg)
    run_line = run_status_line(cfg)
    alive_mid, _, _ = daemon_progress(cfg)
    live_activity = bool(alive_mid) or bool(run_line and run_line != "run=idle")
    if sr.freshness in (SnapshotFreshness.MISSING, SnapshotFreshness.UNREADABLE) and not live_activity:
        return {"verdict": "unknown", "installed": False, "loaded": False,
                "hint": f"daemon strip snapshot {sr.freshness.value}"}
    snap = dict(sr.data) if isinstance(sr.data, dict) else {}
    pending_gates = None
    with fail_open("studio.views.daemon_health_strip.pending_gates"):
        pending_gates = pipeline.pending_gate_count(cfg)
    age, stale, _iv = heartbeat_stale(cfg, interval=snap.get("interval") or 600)
    return project_daemon_strip(
        snap, age=age, stale=stale, pending_gates=pending_gates,
        run_line=run_line, alive_mid=alive_mid)
