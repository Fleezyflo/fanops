#!/usr/bin/env python3
"""Map changed src/tests .py paths -> scoped pytest files for scripts/check.sh.

Convention first (studio/, post/ subdirs, test_studio_* names), then a small override table
for modules whose tests use a different basename. Stdlib-only — safe to call from bash."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Modules with no tests/test_<stem>.py — pick the most focused existing test file.
_OVERRIDES: dict[str, tuple[str, ...]] = {
    "src/fanops/_fwrun.py": ("tests/test_fwrun.py",),
    "src/fanops/audit.py": ("tests/test_audit_trail.py",),
    "src/fanops/apply_common.py": ("tests/test_reframe_apply.py", "tests/test_overlay_reburn.py"),
    "src/fanops/clip_ffmpeg.py": ("tests/test_clip.py", "tests/test_smart_framing.py",
                                   "tests/test_impact_render.py", "tests/test_stitch_render.py"),
    "src/fanops/canary.py": ("tests/test_canary_tooling.py",),
    "src/fanops/canary_identity.py": ("tests/test_canary_tooling.py",),
    "src/fanops/canary_baseline.py": ("tests/test_canary_tooling.py",),
    "src/fanops/controlio.py": ("tests/test_controlio.py",),
    "src/fanops/config_introspect.py": ("tests/test_config_verb.py",),
    "src/fanops/cli.py": ("tests/test_cli_wipe.py", "tests/test_cli_retire_source.py", "tests/test_source_lifecycle.py",
                         "tests/test_cli.py", "tests/test_daemon_keeper.py"),
    "src/fanops/cli_run.py": ("tests/test_run_loop.py", "tests/test_operator_pause.py", "tests/test_escalation_exits.py",
                              "tests/test_responder.py", "tests/test_flock_violations_b04.py",
                              "tests/test_fanops_hashtags.py", "tests/test_self_adopt.py", "tests/test_studio_lifecycle.py"),
    "src/fanops/cli_dispatch.py": ("tests/test_cli.py", "tests/test_cli_wipe.py", "tests/test_cli_retire_source.py",
                                   "tests/test_source_lifecycle.py", "tests/test_daemon_keeper.py",
                                   "tests/test_studio_app.py", "tests/test_source_resume.py"),
    "src/fanops/cli_parser.py": ("tests/test_cli.py", "tests/test_canary_tooling.py"),
    "src/fanops/cutover_postiz.py": ("tests/test_cutover.py",),
    "src/fanops/daemon_studio.py": ("tests/test_daemon_studio.py", "tests/test_studio_lifecycle.py",
                                         "tests/test_studio_lifecycle_e2e.py"),
    "src/fanops/daemon_siblings.py": ("tests/test_daemon_keeper.py", "tests/test_daemon_sibling_readiness.py"),
    "src/fanops/daemon.py": ("tests/test_daemon_plist.py", "tests/test_daemon_keeper.py",
                             "tests/test_daemon_sibling_readiness.py", "tests/test_self_adopt.py",
                             "tests/test_daemon_readiness.py", "tests/test_daemon_studio.py"),
    "src/fanops/errors.py": ("tests/test_cli.py", "tests/test_swallow_ratchet.py"),
    "src/fanops/field_shape.py": ("tests/test_learn_doctor.py", "tests/test_health_model.py"),
    "src/fanops/fanops_account_stats.py": ("tests/test_home_rebuild.py",),
    "src/fanops/framing.py": ("tests/test_smart_framing.py",),
    "src/fanops/gate_keys.py": ("tests/test_pipeline_status.py",),
    "src/fanops/health_model.py": ("tests/test_health_model.py", "tests/test_machine_health_projectors.py"),
    "src/fanops/health_types.py": ("tests/test_health_model.py", "tests/test_machine_health_severity.py",
                                   "tests/test_escalation_exits.py", "tests/test_health_json.py"),
    "src/fanops/health_projectors.py": ("tests/test_machine_health_projectors.py", "tests/test_postiz_trust_boundary.py"),
    "src/fanops/health_probes.py": ("tests/test_health_model.py", "tests/test_doctor.py", "tests/test_daemon.py"),
    "src/fanops/ledger_sqlite.py": ("tests/test_ledger_sqlite_store.py",),
    "src/fanops/ledger_bridge.py": ("tests/test_ledger_json_to_sqlite_bridge.py",),
    "src/fanops/llm_errors.py": ("tests/test_llm.py", "tests/test_responder.py"),
    "src/fanops/llm_json.py": ("tests/test_llm.py",),
    "src/fanops/ledger.py": ("tests/test_ledger.py", "tests/test_ledger_store_interface.py",
                             "tests/test_ledger_backend_parity.py",),
    "src/fanops/media_probe.py": ("tests/test_ingest.py", "tests/test_discover.py",
                                  "tests/test_studio_upload.py"),
    "src/fanops/ingest_shard.py": ("tests/test_source_sharding.py",),
    "src/fanops/caption.py": ("tests/test_caption.py", "tests/test_caption_scoping.py",
                              "tests/test_variations_gate.py", "tests/test_persona_corpus.py",
                              "tests/test_hashtags.py", "tests/test_source_tags.py"),
    "src/fanops/caption_compose.py": ("tests/test_caption.py", "tests/test_postiz.py",
                                      "tests/test_zernio.py", "tests/test_dryrun.py",
                                      "tests/test_studio_views.py", "tests/test_source_tags.py"),
    "src/fanops/caption_ingest.py": ("tests/test_caption.py", "tests/test_moments.py",
                                     "tests/test_studio_regenerate.py"),
    "src/fanops/source_tags_sidecar.py": ("tests/test_source_tags.py", "tests/test_doctor.py",
                                           "tests/test_caption.py"),
    "src/fanops/source_tags_shortlist.py": ("tests/test_source_tags.py", "tests/test_pipeline.py"),
    "src/fanops/source_tags_scrape.py": ("tests/test_source_tags.py", "tests/test_fanops_hashtags.py"),
    "src/fanops/source_tags_walk.py": ("tests/test_source_tags.py", "tests/test_ig_web_scrape.py",
                                        "tests/test_fanops_hashtags.py"),
    "src/fanops/paths_rebase.py": ("tests/test_media_path_integrity.py",),
    "src/fanops/persona_research.py": ("tests/test_hashtag_layer_b_tombstone.py", "tests/test_persona_corpus.py"),
    "src/fanops/prompts_caption.py": ("tests/test_prompts.py", "tests/test_variations_gate.py",
                                      "tests/test_persona_corpus.py", "tests/test_no_ghosts.py"),
    "src/fanops/hashtags.py": ("tests/test_hashtags.py", "tests/test_source_tag_lock.py",
                              "tests/test_hashtag_layer_b_tombstone.py"),
    "src/fanops/fanops_hashtags.py": ("tests/test_fanops_hashtags.py",
                                     "tests/test_hashtag_layer_b_tombstone.py"),
    "src/fanops/hashtag_refresh.py": ("tests/test_fanops_hashtags.py",
                                      "tests/test_hashtag_layer_b_tombstone.py"),
    "src/fanops/hashtag_scrape_policy.py": ("tests/test_fanops_hashtags.py",
                                            "tests/test_ig_web_scrape.py",
                                            "tests/test_source_tags.py"),
    "src/fanops/ig_safari_shell.py": ("tests/test_ig_web_scrape.py",
                                      "tests/test_fanops_hashtags.py",
                                      "tests/test_source_tags.py"),
    "src/fanops/studio/views.py": ("tests/test_studio_views.py", "tests/test_studio_personas.py"),
    "src/fanops/persona_store.py": ("tests/test_persona_levers.py",),
    "src/fanops/produce.py": ("tests/test_publish_post.py",),
    "src/fanops/reconcile.py": ("tests/test_reconcile.py", "tests/test_ig_liveness_gate.py",
                                "tests/test_flock_violations_b04.py", "tests/test_imported_projection.py"),
    "src/fanops/reconcile_liveness.py": ("tests/test_reconcile.py", "tests/test_ig_liveness_gate.py",
                                         "tests/test_flock_violations_b04.py", "tests/test_imported_projection.py"),
    "src/fanops/reconcile_mirror.py": ("tests/test_reconcile.py", "tests/test_ig_liveness_gate.py",
                                         "tests/test_flock_violations_b04.py", "tests/test_imported_projection.py",
                                         "tests/test_zernio_metrics.py", "tests/test_channel_provider.py"),
    "src/fanops/reach_ranking.py": ("tests/test_p4_dim_bias.py", "tests/test_culmination_coverage.py"),
    "src/fanops/reframe_vf.py": ("tests/test_smart_framing.py", "tests/test_clip.py",
                                 "tests/test_reframe_s2_d1a.py", "tests/test_reframe_s3_d1b.py"),
    "src/fanops/render_fingerprint.py": ("tests/test_clip.py", "tests/test_reframe.py",
                                         "tests/test_reframe_apply.py", "tests/test_reframe_s2_d1a.py",
                                         "tests/test_reframe_s3_d1b.py"),
    "src/fanops/responder.py": ("tests/test_responder.py",),
    "src/fanops/responder_policy.py": ("tests/test_responder.py",),
    "src/fanops/visual_start.py": ("tests/test_visual_start.py", "tests/test_clip.py",
                                   "tests/test_reframe.py"),
    "src/fanops/window_math.py": ("tests/test_clip.py", "tests/test_reframe.py"),
    "src/fanops/settings.py": ("tests/test_config.py",),
    "src/fanops/timing_bias.py": ("tests/test_culmination_coverage.py",),
    "src/fanops/speech_trust.py": ("tests/test_speech_trust.py", "tests/test_e2e_transcript_assertion.py"),
    "src/fanops/transcribe_engine.py": ("tests/test_transcribe.py", "tests/test_transcribe_timeout.py",
                                        "tests/test_transcribe_stem_cache.py", "tests/test_transcribe_provenance.py",
                                        "tests/test_transcribe_legacy_duration.py", "tests/test_artifacts.py"),
    "src/fanops/transcribe.py": ("tests/test_transcribe.py", "tests/test_speech_trust.py"),
    "src/fanops/post/run.py": ("tests/test_post_run.py",),
    "src/fanops/post/publish_archive.py": ("tests/test_post_run.py", "tests/test_render_stage_e.py"),
    "src/fanops/post/publish_dryrun.py": ("tests/test_dryrun_boundary.py", "tests/test_post_run.py"),
    "src/fanops/post/publish_errors.py": ("tests/test_publish_transient_retry.py", "tests/test_post_run.py"),
    "src/fanops/post/publish_requeue.py": ("tests/test_publish_transient_network_mol125.py", "tests/test_post_run.py"),
    "src/fanops/post/metrics/__init__.py": ("tests/test_metrics.py", "tests/test_zernio_metrics.py"),
    "src/fanops/post/metrics/common.py": ("tests/test_metrics.py", "tests/test_graph_insights.py"),
    "src/fanops/post/metrics/postiz_read.py": ("tests/test_metrics.py", "tests/test_postiz.py"),
    "src/fanops/post/metrics/zernio_read.py": ("tests/test_zernio_metrics.py", "tests/test_zernio.py"),
    "src/fanops/post/zernio.py": ("tests/test_zernio.py", "tests/test_zernio_idempotency.py"),
    "src/fanops/post/postiz.py": ("tests/test_postiz.py", "tests/test_youtube_publish.py"),
    # zernio_outcome is the private Zernio create-result type; its behaviour is only meaningful as the
    # thing ZernioPoster maps onto the ledger, so it is covered where that mapping is proven.
    "src/fanops/post/zernio_outcome.py": ("tests/test_zernio_idempotency.py",),
    "src/fanops/studio/actions_approve.py": ("tests/test_studio_approval.py",),
    "src/fanops/studio/actions_edit.py": ("tests/test_studio_regenerate.py", "tests/test_studio_approve_hook.py", "tests/test_studio_review_preview.py"),
    "src/fanops/studio/actions_schedule.py": ("tests/test_studio_schedule_cockpit.py", "tests/test_studio_schedule_readiness.py"),
    "src/fanops/studio/actions_publish.py": ("tests/test_studio_publish_now.py", "tests/test_published_state_invariant.py",
                                             "tests/test_audit_trail.py", "tests/test_studio_loop_closure.py",
                                             "tests/test_reconcile_dryrun.py"),
    "src/fanops/studio/actions_crosspost.py": ("tests/test_studio_actions.py", "tests/test_repost_anywhere.py",
                                               "tests/test_mint_product_type.py", "tests/test_studio_publish_now.py"),
    "src/fanops/studio/actions_recover.py": ("tests/test_studio_actions.py", "tests/test_rearm_refusal.py",
                                             "tests/test_one_rearm.py", "tests/test_audit_trail.py",
                                             "tests/test_publish_transient_network_mol125.py"),
    "src/fanops/studio/actions_common.py": ("tests/test_studio_golive.py",),
    "src/fanops/studio/actions_run.py": ("tests/test_studio_run.py", "tests/test_studio_upload.py",
                                        "tests/test_upload_chunked.py", "tests/test_queue_gate.py"),
    "src/fanops/studio/actions_segments.py": ("tests/test_moments_segments.py",),
    "src/fanops/studio/actions_wipe.py": ("tests/test_studio_wipe.py",),
    "src/fanops/studio/app_routes_hashtags.py": ("tests/test_hashtag_page.py",),
    "src/fanops/studio/app_request.py": ("tests/test_fail_open_logging_mol67.py", "tests/test_review_lanes_view.py"),
    "src/fanops/studio/app_routes_home.py": ("tests/test_home_rebuild.py", "tests/test_studio_home_onboarding.py",
                                             "tests/test_studio_workflow_spine.py", "tests/test_daemon_readiness.py"),
    "src/fanops/studio/app_routes_media.py": ("tests/test_thumb_routes.py", "tests/test_studio_thumb.py",
                                              "tests/test_media_path_integrity.py", "tests/test_studio_render_serve.py"),
    "src/fanops/studio/app_routes_review.py": ("tests/test_studio_app.py",),
    "src/fanops/studio/app_routes_schedule.py": ("tests/test_studio_schedule_cockpit.py",),
    "src/fanops/studio/views_hashtags.py": ("tests/test_hashtag_page.py",),
    "src/fanops/studio/preview_media.py": ("tests/test_studio_gaps_closure.py",),
    "src/fanops/studio/thumb_media.py": ("tests/test_thumb_routes.py", "tests/test_studio_thumb.py"),
    "src/fanops/studio/views_common.py": ("tests/test_bulk_approve_spread.py",),
    "src/fanops/studio/views_library.py": ("tests/test_source_progress.py", "tests/test_studio_library.py"),
    "src/fanops/studio/views_live.py": ("tests/test_studio_live_library.py",),
    "src/fanops/studio/views_posted.py": ("tests/test_studio_views.py",),
    "src/fanops/studio/views_results.py": ("tests/test_studio_views.py",),
    "src/fanops/studio/views_schedule.py": ("tests/test_studio_views.py",),
    "src/fanops/studio/views_review.py": ("tests/test_studio_views.py",),
    "src/fanops/studio/views_home.py": ("tests/test_studio_views.py", "tests/test_studio_workflow_spine.py",
                                        "tests/test_attention_counts.py", "tests/test_studio_gaps_closure.py"),
    "src/fanops/studio/views_golive.py": ("tests/test_studio_golive.py", "tests/test_studio_views.py"),
    "src/fanops/studio/views_run.py": ("tests/test_studio_run.py", "tests/test_studio_views.py",
                                       "tests/test_studio_library.py", "tests/test_studio_gates.py",
                                       "tests/test_studio_stitches.py", "tests/test_studio_candidates.py",
                                       "tests/test_publish_queue.py", "tests/test_studio_errored_sources.py",
                                       "tests/test_source_lifecycle.py", "tests/test_run_activity.py",
                                       "tests/test_ui_publish_truth_root.py", "tests/test_ui_publish_mode_label_truth.py",
                                       "tests/test_responder.py"),
}


def _exists(rel: str) -> str | None:
    p = ROOT / rel
    return rel if p.is_file() else None


def _convention_candidates(src: str) -> list[str]:
    """Ordered candidate test paths for a changed src/fanops/... module."""
    p = Path(src)
    if len(p.parts) < 3 or p.parts[0] != "src" or p.parts[1] != "fanops":
        return []
    rel = Path(*p.parts[2:])  # fanops/...
    stem = rel.stem
    parts = rel.parts
    cands: list[str] = []
    if len(parts) == 1:
        cands.append(f"tests/test_{stem}.py")
    elif parts[0] == "studio":
        cands.append(f"tests/test_studio_{stem}.py")
        if stem.startswith("actions_"):
            cands.append(f"tests/test_{stem}.py")
        elif stem.startswith("app_routes_"):
            route = stem.removeprefix("app_routes_")
            cands.append(f"tests/test_studio_{route}.py")
        elif stem.startswith("views_"):
            view = stem.removeprefix("views_")
            cands.append(f"tests/test_studio_{view}.py")
    elif parts[0] == "post":
        cands.append(f"tests/test_post_{stem}.py")
        cands.append(f"tests/test_{stem}.py")
    out: list[str] = []
    for c in cands:
        hit = _exists(c)
        if hit and hit not in out:
            out.append(hit)
    return out


def resolve_tests(changed: list[str]) -> list[str]:
    """Return sorted unique pytest files to run for the given changed paths."""
    want: dict[str, None] = {}
    for f in changed:
        if f.startswith("tests/") and (ROOT / f).is_file():
            want[f] = None
            continue
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        hits = _convention_candidates(f)
        extra = [h for h in _OVERRIDES.get(f, ()) if _exists(h)]
        if not hits:
            hits = extra
        else:
            for h in extra:
                if h not in hits:
                    hits.append(h)
        for t in hits:
            want[t] = None
    return sorted(want)


def orphan_src_modules(changed: list[str]) -> list[str]:
    """Return changed src/fanops/*.py paths (excl __init__) with no scoped test mapping."""
    out: list[str] = []
    for f in changed:
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        if f.endswith("__init__.py"):
            continue
        if not resolve_tests([f]):
            out.append(f)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args and args[0] == "--orphans":
        for o in orphan_src_modules(args[1:]):
            print(o)
        return 0
    for t in resolve_tests(args):
        print(t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
