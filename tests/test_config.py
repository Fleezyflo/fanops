# tests/test_config.py
import json
import logging
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

def test_llm_model_defaults_to_opus(monkeypatch, tmp_path):
    # V2 M1/F1: the creative brain (claude -p) was UNPINNED — output quality drifted with the CLI
    # default. Pin it via config. Default "opus" = the judgment tier for the creative calls; an alias
    # the `claude` CLI resolves to latest-of-tier (operator pins a full id for bit-stable repro).
    monkeypatch.delenv("FANOPS_LLM_MODEL", raising=False)
    assert Config(root=tmp_path).llm_model == "opus"

def test_llm_model_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_LLM_MODEL", "sonnet")
    assert Config(root=tmp_path).llm_model == "sonnet"

def test_llm_model_blank_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_LLM_MODEL", "   ")            # whitespace-only -> default (mirror clip_profile)
    assert Config(root=tmp_path).llm_model == "opus"

def test_hook_editor_defaults_on(monkeypatch, tmp_path):
    # Phase C2: the feed-aware hook editor closes the weakest link (template clustering); it must be
    # ON by default, not gated on the operator remembering a flag. It is fail-open + idempotent.
    monkeypatch.delenv("FANOPS_HOOK_EDITOR", raising=False)
    assert Config(root=tmp_path).hook_editor is True

def test_hook_editor_explicit_off_disables(monkeypatch, tmp_path):
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("FANOPS_HOOK_EDITOR", off)
        assert Config(root=tmp_path).hook_editor is False

def test_hook_editor_explicit_on(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "1")
    assert Config(root=tmp_path).hook_editor is True

def test_hook_judge_defaults_on_when_unset(monkeypatch, tmp_path):
    # v2: the STRICT critic is the teeth and must be ON by default — the repair loop absorbs the
    # null-rate cost, so the critic no longer needs to be opt-in. Only explicit off-words disable it.
    monkeypatch.delenv("FANOPS_HOOK_JUDGE", raising=False)
    assert Config(root=tmp_path).hook_judge is True

def test_hook_judge_explicit_off_disables(monkeypatch, tmp_path):
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("FANOPS_HOOK_JUDGE", off)
        assert Config(root=tmp_path).hook_judge is False

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
    assert c.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"
    assert c.reports == tmp_path / "MohFlow-FanOps" / "07_reports"

def test_poster_default_dryrun(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert Config(root=tmp_path).poster_backend == "dryrun"

def test_poster_env_and_key_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "  abc123\n")   # surrounding ws only
    c = Config(root=tmp_path)
    assert c.poster_backend == "rest" and c.blotato_api_key == "abc123"

def test_poster_backend_known_values_pass_through(monkeypatch, tmp_path):
    for v in ("dryrun", "postiz", "rest", "mcp"):
        monkeypatch.setenv("FANOPS_POSTER", v)
        assert Config(root=tmp_path).poster_backend == v

def test_poster_backend_unknown_falls_back_to_dryrun(monkeypatch, tmp_path):
    # W4: a typo'd backend must resolve to dryrun — get_poster falls back to DryRunPoster for any
    # unrecognized value, so a typo would otherwise show a LIVE banner while posting NOTHING.
    monkeypatch.setenv("FANOPS_POSTER", "positz")        # typo of "postiz"
    c = Config(root=tmp_path)
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
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

def test_responder_defaults_manual(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    c = Config(root=tmp_path)
    assert c.responder_mode == "manual"

def test_is_live_backend_requires_backend_and_key(monkeypatch, tmp_path):
    # Stage-6 audit: the "live backend + key" guard gates the learning passes and reconcile at
    # three sites — one property is its single home so the definition of "live" can't drift.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    assert Config(root=tmp_path).is_live_backend is False        # dryrun: never live, key or not
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False        # live backend but NO key
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    assert Config(root=tmp_path).is_live_backend is True

def test_is_live_backend_postiz_uses_postiz_key(monkeypatch, tmp_path):
    # M2: a Postiz deployment is live on POSTIZ_API_KEY, NOT on a Blotato key — the redefinition that
    # unfreezes the learning loop on Postiz. dryrun/rest truth tables stay byte-identical (above).
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False        # postiz but NO postiz key
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    assert Config(root=tmp_path).is_live_backend is False        # a Blotato key must NOT make postiz live
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    assert Config(root=tmp_path).is_live_backend is True         # postiz + postiz key → live

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

def test_asr_model_defaults_large_v3_and_respects_env(monkeypatch, tmp_path):
    # The faster-whisper model — the proven music/rap accuracy winner over turbo. Default large-v3
    # (int8 makes it practical on CPU). Override picks a smaller fw model on a slow host.
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    assert Config(root=tmp_path).asr_model == "large-v3"
    monkeypatch.setenv("FANOPS_ASR_MODEL", " medium ")
    assert Config(root=tmp_path).asr_model == "medium"

def test_asr_language_defaults_auto_and_respects_env(monkeypatch, tmp_path):
    # "" = auto-detect (handles EN+AR per clip; proven equal to pinning, just slower). An operator
    # with a single-language account can pin e.g. "ar" for the ~3x decode speedup.
    monkeypatch.delenv("FANOPS_ASR_LANGUAGE", raising=False)
    assert Config(root=tmp_path).asr_language == ""
    monkeypatch.setenv("FANOPS_ASR_LANGUAGE", "ar")
    assert Config(root=tmp_path).asr_language == "ar"

def test_subtitle_font_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_SUBTITLE_FONT", raising=False)
    assert Config(root=tmp_path).subtitle_font == "Arial Unicode MS"
    monkeypatch.setenv("FANOPS_SUBTITLE_FONT", "X")
    assert Config(root=tmp_path).subtitle_font == "X"


def test_creative_variation_defaults_off_and_respects_env(tmp_path, monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_CREATIVE_VARIATION", raising=False)
    assert Config(root=tmp_path).creative_variation is False           # default OFF (opt-in)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "true")
    assert Config(root=tmp_path).creative_variation is True
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    assert Config(root=tmp_path).creative_variation is False


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


def test_variant_transfer_bad_ints_fall_back(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "notanint")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "")
    c = Config(root=tmp_path)
    assert c.variant_transfer_min_donors == 2          # bad int -> default, no crash
    assert c.variant_transfer_max_hooks == 2


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


def test_variant_amplify_bad_env_falls_back(monkeypatch, tmp_path):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "nope")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "nan-ish?")
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "x")
    c = Config(root=tmp_path)
    assert c.variant_amplify_min_posts == 8
    assert c.variant_amplify_min_gap == 25.0
    assert c.variant_amplify_min_streak == 3


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

def test_variant_ucb_c_bad_or_negative_falls_back(monkeypatch, tmp_path):
    from fanops.config import Config
    import math
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "abc")          # unparseable -> default
    assert Config(root=tmp_path).variant_ucb_c == math.sqrt(2)
    monkeypatch.setenv("FANOPS_VARIANT_UCB_C", "-1")           # negative -> default (no anti-exploration)
    assert Config(root=tmp_path).variant_ucb_c == math.sqrt(2)


def test_publish_lead_minutes_default_zero(monkeypatch):
    from fanops.config import Config
    monkeypatch.delenv("FANOPS_PUBLISH_LEAD_MINUTES", raising=False)
    assert Config().publish_lead_minutes == 0

def test_publish_lead_minutes_reads_env(monkeypatch):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "120")
    assert Config().publish_lead_minutes == 120

def test_publish_lead_minutes_non_int_falls_back_to_zero(monkeypatch):
    from fanops.config import Config
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "not-a-number")
    assert Config().publish_lead_minutes == 0

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

def test_hook_critic_advisory_default_on_and_opt_out(tmp_path, monkeypatch):
    # M2 / finding #3 (de-veto): advisory is DEFAULT ON — the critic no longer DELETES a raw hook on a
    # subjective call (it still runs+repairs+meters+logs). Opt-OUT shape (mirrors hook_judge): only an
    # explicit off-word restores the hard terminal veto.
    monkeypatch.delenv("FANOPS_HOOK_CRITIC_ADVISORY", raising=False)
    assert Config(root=tmp_path).hook_critic_advisory is True            # DEFAULT ON (raw output, no veto)
    monkeypatch.setenv("FANOPS_HOOK_CRITIC_ADVISORY", "0")
    assert Config(root=tmp_path).hook_critic_advisory is False           # explicit opt-out -> restore the veto
    monkeypatch.setenv("FANOPS_HOOK_CRITIC_ADVISORY", "on")
    assert Config(root=tmp_path).hook_critic_advisory is True
