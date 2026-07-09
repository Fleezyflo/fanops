# tests/test_config.py
import json
import logging
import pytest
from fanops.config import Config

def _tuning_cfg(tmp_path, obj):
    cfg = Config(root=tmp_path)
    cfg.tuning_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tuning_path.write_text(json.dumps(obj))
    return cfg

def test_tuning_drops_uncompilable_regex_keeps_good(tmp_path, caplog):
    # A single bad override regex must not nuke the whole override (it used to fall back to ALL
    # defaults at the consumer); tuning() drops only the bad entry, keeps the good ones, warns.
    cfg = _tuning_cfg(tmp_path, {"offbrand_en": ["\\bpls\\b", "(unclosed"]})
    with caplog.at_level(logging.WARNING):
        t = cfg.tuning()
    assert t["offbrand_en"] == ["\\bpls\\b"]
    assert any("offbrand_en" in r.getMessage() for r in caplog.records)

def test_tuning_drops_nonnumeric_lift_weight(tmp_path, caplog):
    # a non-numeric weight value would crash track.lift_score arithmetic — drop it, keep numerics.
    cfg = _tuning_cfg(tmp_path, {"lift_weights": {"saves": 4.0, "bad": "lots"}})
    with caplog.at_level(logging.WARNING):
        t = cfg.tuning()
    assert t["lift_weights"] == {"saves": 4.0}
    assert any("lift_weights" in r.getMessage() for r in caplog.records)

def test_tuning_passes_clean_overrides_unchanged(tmp_path):
    cfg = _tuning_cfg(tmp_path, {"offbrand_en": ["\\bpls\\b"], "lift_weights": {"saves": 5}})
    t = cfg.tuning()
    assert t["offbrand_en"] == ["\\bpls\\b"] and t["lift_weights"] == {"saves": 5}

def test_llm_model_per_gate_defaults(monkeypatch, tmp_path):
    # V2 M1/F1 PIN stays; the TIER is now PER-GATE. The `moments` gate is the CREATIVE VISION hook
    # AUTHOR (it sees source frames + writes the on-screen retention hook) -> opus. `captions` (hashtags
    # only) stays mechanical -> sonnet.
    monkeypatch.delenv("FANOPS_LLM_MODEL", raising=False)
    c = Config(root=tmp_path)
    assert c.llm_model_for("moments") == "opus"                 # Phase 1: vision hook author
    assert c.llm_model_for("captions") == "sonnet"
    assert c.llm_model_for("unknown_kind") == "sonnet"          # default-safe for any new gate

def test_llm_model_global_override_forces_all_gates(monkeypatch, tmp_path):
    # FANOPS_LLM_MODEL forces ONE model for EVERY gate — operator escape hatch / a FULL id for repro.
    monkeypatch.setenv("FANOPS_LLM_MODEL", "claude-opus-4-x")
    c = Config(root=tmp_path)
    assert c.llm_model_for("moments") == "claude-opus-4-x"      # creative gate forced
    assert c.llm_model_for("captions") == "claude-opus-4-x"     # mechanical gate forced up

def test_llm_model_blank_override_falls_back_to_per_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_LLM_MODEL", "   ")               # whitespace-only -> per-gate defaults
    c = Config(root=tmp_path)
    assert c.llm_model_for("moments") == "opus" and c.llm_model_for("captions") == "sonnet"

def test_hook_router_default_off(monkeypatch, tmp_path):
    # M2 structural-hooks router: opt-in, default OFF (observe-only annotation when on; non-regression)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False)
    assert Config(root=tmp_path).hook_router is False

def test_hook_router_opt_in(monkeypatch, tmp_path):
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FANOPS_HOOK_ROUTER", on)
        assert Config(root=tmp_path).hook_router is True


def test_impact_cut_default_off(monkeypatch, tmp_path):
    # M4 structural-hooks: impact-cut producer is a per-format gate, default OFF (non-regression)
    monkeypatch.delenv("FANOPS_IMPACT_CUT", raising=False)
    assert Config(root=tmp_path).impact_cut is False

def test_impact_cut_opt_in(monkeypatch, tmp_path):
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FANOPS_IMPACT_CUT", on)
        assert Config(root=tmp_path).impact_cut is True

def test_dirs(tmp_path):
    c = Config(root=tmp_path)
    assert c.inbox == tmp_path / "MohFlow-FanOps" / "01_inbox"
    assert c.agent_io == tmp_path / "MohFlow-FanOps" / "04_agent_io"
    assert c.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.sqlite"
    assert c.legacy_ledger_json_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"
    assert c.reports == tmp_path / "MohFlow-FanOps" / "07_reports"

def test_poster_default_dryrun(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert Config(root=tmp_path).poster_backend == "dryrun"

def test_poster_env_and_key_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "  abc123\n")    # surrounding ws only
    c = Config(root=tmp_path)
    assert c.poster_backend == "zernio" and c.zernio_api_key == "abc123"

def test_poster_backend_known_values_pass_through(monkeypatch, tmp_path):
    for v in ("dryrun", "postiz", "zernio"):
        monkeypatch.setenv("FANOPS_POSTER", v)
        assert Config(root=tmp_path).poster_backend == v

def test_poster_backend_unknown_falls_back_to_dryrun(monkeypatch, tmp_path):
    # W4: a typo'd backend must resolve to dryrun — get_poster falls back to DryRunPoster for any
    # unrecognized value, so a typo would otherwise show a LIVE banner while posting NOTHING.
    monkeypatch.setenv("FANOPS_POSTER", "positz")        # typo of "postiz"
    c = Config(root=tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    assert c.poster_backend == "dryrun"
    assert c.is_live_backend is False                    # so the banner shows dryrun, never a false LIVE

def test_poster_backend_trims_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "  postiz\n")    # a .env value can carry surrounding ws
    assert Config(root=tmp_path).poster_backend == "postiz"

def test_clip_profile_defaults_talk(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_CLIP_PROFILE", raising=False)
    assert Config(root=tmp_path).clip_profile == "talk"     # unset -> talk band, today's behavior

def test_clip_profile_env_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_CLIP_PROFILE", "  song\n")   # a .env value can carry surrounding ws
    assert Config(root=tmp_path).clip_profile == "song"

def test_responder_defaults_manual_when_unset(monkeypatch, tmp_path):
    # NO haphazard claude: with FANOPS_RESPONDER unset, the responder is manual regardless of whether
    # `claude` is on PATH — presence never auto-enables the LLM (that fired claude on every run/tick).
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    assert Config(root=tmp_path).responder_mode == "manual"

def test_responder_manual_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    assert Config(root=tmp_path).responder_mode == "manual"

def test_responder_llm_only_on_explicit_optin(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    assert Config(root=tmp_path).responder_mode == "llm"        # explicit opt-in still works

def test_is_live_backend_requires_backend_and_key(monkeypatch, tmp_path):
    # Stage-6 audit: the "live backend + key" guard gates the learning passes and reconcile at
    # three sites — one property is its single home so the definition of "live" can't drift.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    assert Config(root=tmp_path).is_live_backend is False        # dryrun: never live, key or not
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False        # live backend but NO key
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    assert Config(root=tmp_path).is_live_backend is True

def test_is_live_backend_postiz_uses_postiz_key(monkeypatch, tmp_path):
    # M2: a Postiz deployment is live on POSTIZ_API_KEY. The redefinition that unfreezes the learning
    # loop on Postiz. dryrun/zernio truth tables stay byte-identical (above).
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False        # postiz but NO postiz key
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    assert Config(root=tmp_path).is_live_backend is True         # postiz + postiz key → live

def test_responder_mode_unknown_falls_back_to_manual(monkeypatch, tmp_path, caplog):
    # Contract: responder_mode ∈ {llm, manual}. A typo (FANOPS_RESPONDER=llmm) slipped through verbatim,
    # and get_responder only matches =="llm" -> the operator THINKS they enabled the AI but silently gets
    # manual with no signal. Validate + warn + fall back to manual, mirroring poster_backend's guard.
    monkeypatch.setenv("FANOPS_RESPONDER", "llmm")               # typo of "llm"
    with caplog.at_level(logging.WARNING):
        mode = Config(root=tmp_path).responder_mode
    assert mode == "manual"                                      # unknown never resolves to a bogus mode
    assert any("FANOPS_RESPONDER" in r.getMessage() for r in caplog.records)

def test_is_live_backend_logs_when_registry_unreadable(monkeypatch, tmp_path, caplog):
    # high: is_live + no global creds falls through to per-channel readiness; a TORN accounts.json
    # (load_accounts_safe err) froze the learn/reconcile passes by returning False with NO signal — the
    # operator saw learning frozen and no reason. Keep the fail-safe False, but log WHY.
    monkeypatch.setenv("FANOPS_LIVE", "1")                       # operator intends live
    monkeypatch.delenv("FANOPS_POSTER", raising=False)          # dryrun global -> no backend creds -> fall through
    monkeypatch.setattr("fanops.accounts.load_accounts_safe", lambda cfg: (None, "corrupt accounts.json"))
    with caplog.at_level(logging.WARNING):
        live = Config(root=tmp_path).is_live_backend
    assert live is False                                        # still fail-safe (not provably live)
    assert any("account" in r.getMessage().lower() for r in caplog.records)

def test_effective_publish_mode_logs_on_accounts_error(monkeypatch, tmp_path, caplog):
    # The publish-mode LABEL fail-opens to 'live' when accounts can't be read — silently, so a corrupt
    # registry showed a confident 'live' with no hint the read failed. Keep the fail-open label, log the lie.
    monkeypatch.setenv("FANOPS_LIVE", "1")
    def boom(cfg): raise RuntimeError("corrupt")
    monkeypatch.setattr("fanops.accounts.Accounts.load", boom)
    with caplog.at_level(logging.WARNING):
        mode = Config(root=tmp_path).effective_publish_mode()
    assert mode == "live"                                       # fail-open label preserved
    assert any("account" in r.getMessage().lower() for r in caplog.records)

def test_burn_subs_defaults_off_and_respects_env(monkeypatch, tmp_path):
    # DEFAULT OFF (opt-in): burn_subs only adds the TRANSCRIPT captions on top of the retention hook;
    # captioning the audio is redundant + transcription-dependent, so it ships only when asked.
    monkeypatch.delenv("FANOPS_BURN_SUBS", raising=False)
    assert Config(root=tmp_path).burn_subs is False           # default OFF (unset)
    monkeypatch.setenv("FANOPS_BURN_SUBS", "")
    assert Config(root=tmp_path).burn_subs is False           # blank stays OFF
    monkeypatch.setenv("FANOPS_BURN_SUBS", "maybe")
    assert Config(root=tmp_path).burn_subs is False           # anything not an on-word stays OFF
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    assert Config(root=tmp_path).burn_subs is True
    monkeypatch.setenv("FANOPS_BURN_SUBS", "on")
    assert Config(root=tmp_path).burn_subs is True

def test_isolate_vocals_defaults_on_and_respects_env(monkeypatch, tmp_path):
    # DEFAULT ON: stripping the beat (Demucs) before Whisper is the music-transcription fix; it
    # fails open to raw audio when demucs is absent, so ON is safe. Only off-words disable it.
    # (conftest forces it OFF for hermeticity, so delenv to read the true default.)
    monkeypatch.delenv("FANOPS_ISOLATE_VOCALS", raising=False)
    assert Config(root=tmp_path).isolate_vocals is True
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    assert Config(root=tmp_path).isolate_vocals is False
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "off")
    assert Config(root=tmp_path).isolate_vocals is False
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")
    assert Config(root=tmp_path).isolate_vocals is True

def test_asr_model_defaults_medium_and_respects_env(monkeypatch, tmp_path):
    # Default "medium" — fast enough to transcribe a long (~26min) source within the whisper timeout on
    # CPU. Override pins large-v3 (max accuracy) on a fast host.
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    assert Config(root=tmp_path).asr_model == "medium"
    monkeypatch.setenv("FANOPS_ASR_MODEL", " large-v3 ")
    assert Config(root=tmp_path).asr_model == "large-v3"

def test_asr_model_for_scales_with_source_duration(monkeypatch, tmp_path):
    # UNAWARE-CONFIG FIX: with no operator pin the ASR model scales with SOURCE LENGTH — a short source
    # affords the most accurate model (large-v3, cheap on little audio); a long (or unknown) source stays
    # on the faster default (medium) so a long transcription lands under transcribe._WHISPER_TIMEOUT.
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    cfg = Config(root=tmp_path)
    assert cfg.asr_model_for(60) == "large-v3"        # 1-min source -> accuracy is free
    assert cfg.asr_model_for(3600) == "medium"        # 1-hour source -> stay fast/safe
    assert cfg.asr_model_for(None) == "medium"        # unknown duration -> the safe long default

def test_asr_model_for_honors_operator_pin_over_duration(monkeypatch, tmp_path):
    # An explicit FANOPS_ASR_MODEL is the operator's call and wins verbatim, regardless of duration.
    monkeypatch.setenv("FANOPS_ASR_MODEL", "small")
    cfg = Config(root=tmp_path)
    assert cfg.asr_model_for(60) == "small" and cfg.asr_model_for(3600) == "small"

def test_asr_language_defaults_en_ar_and_respects_env(monkeypatch, tmp_path):
    # Default "en,ar" — pins BOTH candidates (the runner detects per segment, handling mixed EN+AR in
    # one source). An operator can pin a single language e.g. "ar" for the ~3x decode speedup.
    monkeypatch.delenv("FANOPS_ASR_LANGUAGE", raising=False)
    assert Config(root=tmp_path).asr_language == "en,ar"
    monkeypatch.setenv("FANOPS_ASR_LANGUAGE", "ar")
    assert Config(root=tmp_path).asr_language == "ar"

def test_subtitle_font_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_SUBTITLE_FONT", raising=False)
    assert Config(root=tmp_path).subtitle_font == "Arial Unicode MS"
    monkeypatch.setenv("FANOPS_SUBTITLE_FONT", "X")
    assert Config(root=tmp_path).subtitle_font == "X"


def test_no_creative_variation_property(tmp_path):
    from fanops.config import Config
    assert not hasattr(Config(root=tmp_path), "creative_variation")


def test_variant_learning_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_LEARNING", "FANOPS_VARIANT_MIN_POSTS", "FANOPS_VARIANT_MIN_GAP"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_learning is False
    assert c.variant_min_posts == 3
    assert c.variant_min_gap == 10.0


def test_variant_learning_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_MIN_POSTS", "5")
    monkeypatch.setenv("FANOPS_VARIANT_MIN_GAP", "25")
    c = Config(root=tmp_path)
    assert c.variant_learning is True and c.variant_min_posts == 5 and c.variant_min_gap == 25.0


def test_variant_transfer_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_TRANSFER", "FANOPS_VARIANT_TRANSFER_MIN_DONORS",
              "FANOPS_VARIANT_TRANSFER_MAX_HOOKS"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_transfer is False
    assert c.variant_transfer_min_donors == 2
    assert c.variant_transfer_max_hooks == 2


def test_variant_transfer_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "yes")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "3")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    c = Config(root=tmp_path)
    assert c.variant_transfer is True
    assert c.variant_transfer_min_donors == 3
    assert c.variant_transfer_max_hooks == 1


def test_variant_transfer_bad_ints_raise_at_construct(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "notanint")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)


def test_config_has_review_dir(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert cfg.review == cfg.base / "00_review"        # the discovery review folder
    # approved subfolder convention (used by intake) is review/approved
    assert (cfg.review / "approved").name == "approved"


def test_variant_amplify_defaults_off(monkeypatch, tmp_path):
    from fanops.config import Config
    for k in ("FANOPS_VARIANT_AMPLIFY", "FANOPS_VARIANT_AMPLIFY_MIN_POSTS",
              "FANOPS_VARIANT_AMPLIFY_MIN_GAP", "FANOPS_VARIANT_AMPLIFY_MIN_STREAK"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_amplify is False
    assert c.variant_amplify_min_posts == 8
    assert c.variant_amplify_min_gap == 25.0
    assert c.variant_amplify_min_streak == 3


def test_variant_amplify_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "12")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "40")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "5")
    c = Config(root=tmp_path)
    assert c.variant_amplify is True
    assert c.variant_amplify_min_posts == 12
    assert c.variant_amplify_min_gap == 40.0
    assert c.variant_amplify_min_streak == 5


def test_variant_amplify_bad_env_raises_at_construct(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "nope")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)


def test_variant_ucb_defaults_off_and_sqrt2(monkeypatch, tmp_path):
    from fanops.config import Config
    import math
    for k in ("FANOPS_VARIANT_UCB", "FANOPS_VARIANT_UCB_C"):
        monkeypatch.delenv(k, raising=False)
    c = Config(root=tmp_path)
    assert c.variant_ucb is False                      # default OFF -> v2 greedy stays the allocator
    assert c.variant_ucb_c == math.sqrt(2)             # UCB1 standard exploration weight

def test_variant_ucb_env_overrides(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "0.5")
    c = Config(root=tmp_path)
    assert c.variant_ucb is True and c.variant_ucb_c == 0.5

def test_variant_ucb_c_bad_raises_at_construct(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "abc")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)


def test_publish_lead_minutes_default_zero(monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)
    assert Config().publish_lead_minutes == 0

def test_publish_lead_minutes_reads_env(monkeypatch):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "120")
    assert Config().publish_lead_minutes == 120

def test_publish_lead_minutes_non_int_raises_at_construct(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "not-a-number")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)

def test_publish_lead_minutes_negative_clamps_to_zero(monkeypatch):
    # A negative lead would shift the anchor BEFORE base and could invert the editable window;
    # unlike the other int knobs, this one MUST guard negatives (mirrors variant_ucb_c).
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "-30")
    assert Config().publish_lead_minutes == 0

def test_aware_reframe_flag_default_off_and_env_on(tmp_path, monkeypatch):
    # Theme 2: the upper-third crop bias is OPT-IN (mirrors burn_subs). Default OFF -> today's
    # centered reframe; only the explicit on-words enable it.
    monkeypatch.delenv("FANOPS_AWARE_REFRAME", raising=False)
    assert Config(root=tmp_path).aware_reframe is False
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    assert Config(root=tmp_path).aware_reframe is True
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "off")
    assert Config(root=tmp_path).aware_reframe is False

def test_require_full_objective_default_off_and_opt_in(monkeypatch, tmp_path):
    # T4 opt-in: block amplify on a DEGRADED-lift winner (a partial objective). Default OFF (learning
    # stays conservative); only explicit on-words enable. Mirrors burn_subs.
    monkeypatch.delenv("FANOPS_REQUIRE_FULL_OBJECTIVE", raising=False)
    assert Config(root=tmp_path).require_full_objective is False
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FANOPS_REQUIRE_FULL_OBJECTIVE", on)
        assert Config(root=tmp_path).require_full_objective is True

def test_gc_keep_days_config(monkeypatch, tmp_path):
    # content-lifecycle Phase 3: FANOPS_GC_KEEP_DAYS sets the gc retention window; DEFAULT 30; clamped >= 1;
    # non-int -> default. Mirrors publish_lead_minutes.
    monkeypatch.delenv("FANOPS_GC_KEEP_DAYS", raising=False)
    assert Config(root=tmp_path).gc_keep_days == 30                      # default
    monkeypatch.setenv("FANOPS_GC_KEEP_DAYS", "90")
    assert Config(root=tmp_path).gc_keep_days == 90
def test_gc_keep_days_bad_int_raises_at_construct(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_GC_KEEP_DAYS", "garbage")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)

def test_concurrent_sources_default_off_and_opt_in(tmp_path, monkeypatch):
    # Parallel per-source pipeline is OPT-IN (mirrors burn_subs): default OFF -> the byte-identical
    # sequential path; only the explicit on-words enable it. Anything else stays OFF.
    monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    assert Config(root=tmp_path).concurrent_sources is False
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", on)
        assert Config(root=tmp_path).concurrent_sources is True
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "off")
    assert Config(root=tmp_path).concurrent_sources is False
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "")
    assert Config(root=tmp_path).concurrent_sources is False

def test_concurrent_workers_default_and_clamp(tmp_path, monkeypatch):
    # Pool size: default 4; a bad int falls back to 4; clamped >= 1 (a pool of 0 would hang — a
    # deadlock-guard violation). Mirrors the publish_lead_minutes / variant_ucb_c clamp shape.
    monkeypatch.delenv("FANOPS_CONCURRENT_WORKERS", raising=False)
    assert Config(root=tmp_path).concurrent_workers == 4                 # default
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "8")
    assert Config(root=tmp_path).concurrent_workers == 8                 # honored
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "1")
    assert Config(root=tmp_path).concurrent_workers == 1                 # minimum
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "0")
    assert Config(root=tmp_path).concurrent_workers == 1                 # clamped up from 0 (no hang)
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "-3")
    assert Config(root=tmp_path).concurrent_workers == 1                 # clamped up from negative
def test_concurrent_workers_bad_int_raises_at_construct(tmp_path, monkeypatch):
    from pydantic import ValidationError
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "notanint")
    with pytest.raises(ValidationError):
        Config(root=tmp_path)


def test_settings_live_reread_after_env_mutation(tmp_path, monkeypatch):
    # MOL-292: go-live dual-write must be visible on the next Config(), not a cached import-time read.
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    assert Config(root=tmp_path).is_live is False
    monkeypatch.setenv("FANOPS_LIVE", "1")
    assert Config(root=tmp_path).is_live is True


def test_settings_bad_numeric_names_field_in_validation_error(tmp_path, monkeypatch):
    from pydantic import ValidationError
    monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "four")
    with pytest.raises(ValidationError) as ei:
        Config(root=tmp_path)
    assert "FANOPS_CONCURRENT_WORKERS" in str(ei.value)
