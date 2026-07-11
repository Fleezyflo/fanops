"""Adding a pipeline state? Classify it TERMINAL or add an EXITS entry here — this is the liveness contract."""
from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from enum import Enum

import pytest

from fanops.models import ClipState, MomentState, PostState, RenderState, SourceState

ExitRef = tuple[str, str, str]  # (module, symbol, note)

RENDER_STATE_EXEMPT = True

TERMINAL: dict[type[Enum], frozenset[Enum]] = {
    SourceState: frozenset((
        SourceState.retired,
        SourceState.discovered,
        SourceState.moments_decided,
    )),
    MomentState: frozenset((MomentState.retired, MomentState.error)),
    ClipState: frozenset((
        ClipState.published,
        ClipState.analyzed,
        ClipState.retired,
        ClipState.error,
    )),
    PostState: frozenset((
        PostState.published,
        PostState.analyzed,
        PostState.rejected,
        PostState.retired,
    )),
}

EXITS: dict[type[Enum], dict[Enum, list[ExitRef]]] = {
    SourceState: {
        SourceState.catalogued: [
            ("fanops.transcribe", "transcribe_source", "catalogued -> transcribed"),
            ("fanops.pipeline", "resume_source", "error/moments_empty rewind"),
            ("fanops.pipeline", "reconcile_source_progress", "artifact auto-resume at advance entry"),
            ("fanops.artifacts", "adopt_warm_artifacts", "warm artifact adoption"),
            ("fanops.artifacts", "infer_resume_stage", "artifact-inferred resume stage"),
        ],
        SourceState.transcribed: [
            ("fanops.signals", "detect_signals", "transcribed -> signalled"),
            ("fanops.pipeline", "resume_source", "error/moments_empty rewind"),
            ("fanops.pipeline", "reconcile_source_progress", "artifact auto-resume at advance entry"),
            ("fanops.artifacts", "infer_resume_stage", "artifact-inferred resume stage"),
        ],
        SourceState.signalled: [
            ("fanops.moments", "request_moments", "signalled -> moments_requested"),
            ("fanops.responder", "LlmResponder.answer_pending", "agent gate answer loop"),
            ("fanops.responder", "_GATE_DETERMINISTIC_MAX", "3x deterministic -> SourceState.error"),
            ("fanops.prompts", "_MAX_TARGET_PICKS", "pick ceiling on signalled only"),
        ],
        SourceState.moments_requested: [
            ("fanops.moments", "ingest_moments", "moments_requested -> picks_decided/moments_decided"),
        ],
        SourceState.picks_decided: [
            ("fanops.moments", "request_moment_hooks", "open per-pick hook gates"),
            ("fanops.moments", "ingest_moment_hooks", "picks_decided -> moments_decided"),
        ],
        SourceState.error: [
            ("fanops.pipeline", "resume_source", "error -> catalogued/transcribed"),
            ("fanops.pipeline", "reconcile_source_progress", "transient error auto-resume"),
            ("fanops.pipeline", "_force_reset_to_catalogued", "T0 force rewind to catalogued"),
            ("fanops.artifacts", "infer_resume_stage", "artifact-inferred resume stage"),
        ],
        SourceState.moments_empty: [
            ("fanops.pipeline", "resume_source", "moments_empty -> catalogued/transcribed"),
        ],
    },
    MomentState: {
        MomentState.picked: [
            ("fanops.moments", "ingest_moment_hooks", "picked -> decided"),
        ],
        MomentState.decided: [
            ("fanops.clip", "render_aspects_for", "decided -> clipped + rendered clips"),
            ("fanops.pipeline", "_quarantine", "render failure -> error"),
        ],
        MomentState.clipped: [
            ("fanops.clip", "render_aspects_for", "re-render additional aspects"),
            ("fanops.pipeline", "_quarantine", "render failure -> error"),
        ],
    },
    ClipState: {
        ClipState.held: [
            ("fanops.studio.actions", "release_held_clip", "held -> captioned (Studio unhold)"),
        ],
        ClipState.stitch_draft: [
            ("fanops.studio.actions_approve", "release_stitches", "stitch_draft -> captioned"),
        ],
        ClipState.rendered: [
            ("fanops.caption", "request_captions", "rendered -> captions_requested"),
            ("fanops.pipeline", "_stage_render_and_caption", "decided-moment render+caption request"),
        ],
        ClipState.captions_requested: [
            ("fanops.caption", "ingest_captions", "captions_requested -> captioned/held"),
            ("fanops.pipeline", "_stage_ingest_captions", "advance caption ingest stage"),
        ],
        ClipState.captioned: [
            ("fanops.crosspost", "crosspost_clips", "captioned -> queued posts"),
            ("fanops.pipeline", "_stage_crosspost", "advance crosspost stage"),
        ],
        ClipState.queued: [
            ("fanops.post.run", "publish_due", "queued -> submitting/submitted"),
        ],
    },
    PostState: {
        PostState.failed: [
            ("fanops.reconcile", "reconcile_posts", "poll + rest failed posts"),
            ("fanops.studio.actions", "recover_posts", "Studio manual recovery"),
        ],
        PostState.error: [
            ("fanops.reconcile", "reconcile_posts", "poll + rest errored posts"),
            ("fanops.studio.actions", "recover_posts", "Studio manual recovery"),
        ],
        PostState.needs_reconcile: [
            ("fanops.reconcile", "reconcile_posts", "ambiguous publish poll"),
            ("fanops.studio.actions", "recover_posts", "Studio manual recovery"),
            ("fanops.cli", "cmd_resolve", "CLI manual terminal override"),
        ],
        PostState.awaiting_approval: [
            ("fanops.ledger", "Ledger.approve_post", "awaiting_approval -> queued"),
            ("fanops.ledger", "Ledger.reject_post", "awaiting_approval -> rejected"),
            ("fanops.ledger", "Ledger.unapprove_post", "queued -> awaiting_approval"),
        ],
        PostState.queued: [
            ("fanops.post.run", "publish_due", "queued -> submitting"),
            ("fanops.reconcile", "reconcile_due", "pre-publish reconcile pass"),
        ],
        PostState.submitting: [
            ("fanops.reconcile", "heal_stranded_submitting", "stuck submitting heal"),
            ("fanops.reconcile", "reconcile_due", "poll stranded submit"),
        ],
        PostState.submitted: [
            ("fanops.reconcile", "reconcile_posts", "submitted -> published/needs_reconcile"),
            ("fanops.reconcile", "reconcile_due", "due reconcile pass"),
        ],
    },
}

GLOBAL_UNWEDGERS: list[ExitRef] = [
    ("fanops.pipeline_status", "heal_corrupt_gates", "advance txn entry corrupt-gate quarantine"),
    ("fanops.pipeline", "reconcile_source_progress", "artifact auto-resume at advance entry"),
]

GATE_CEILINGS: dict[str, list[ExitRef]] = {
    "moments": [("fanops.responder", "LlmResponder._terminate_gate_source", "3x deterministic -> SourceState.error")],
    "moment_hooks": [("fanops.moments", "ingest_moment_hooks", "fail-open degrade after gate lands")],
    "captions": [("fanops.caption", "ingest_captions", "fail-open / held degrade")],
}

CLI_VERBS: dict[str, str] = {
    "retry-source": "SourceState.error / moments_empty / --force terminal rewind",
    "unhold": "ClipState.held",
    "resolve": "PostState manual terminal override",
    "reconcile": "PostState.submitting / needs_reconcile",
    "promote-source": "SourceState.discovered",
}

_CONTRACT_ENUMS = (SourceState, MomentState, ClipState, PostState)


def _resolve(mod: str, sym: str):
    obj = importlib.import_module(mod)
    for part in sym.split("."):
        obj = getattr(obj, part)
    return obj


def _all_exit_refs() -> list[ExitRef]:
    refs: list[ExitRef] = list(GLOBAL_UNWEDGERS)
    for gate_refs in GATE_CEILINGS.values():
        refs.extend(gate_refs)
    for enum_exits in EXITS.values():
        for state_refs in enum_exits.values():
            refs.extend(state_refs)
    return refs


@pytest.mark.parametrize("enum_cls", _CONTRACT_ENUMS)
def test_every_enum_member_classified(enum_cls: type[Enum]) -> None:
    terminal = TERMINAL[enum_cls]
    exits = EXITS[enum_cls]
    members = set(enum_cls)
    covered = terminal | set(exits.keys())
    assert covered == members, f"{enum_cls.__name__}: missing={members - covered} extra={covered - members}"
    assert terminal.isdisjoint(exits.keys()), f"{enum_cls.__name__}: terminal states must not have EXITS"


def test_render_state_exempt() -> None:
    assert RENDER_STATE_EXEMPT
    assert RenderState not in TERMINAL
    assert RenderState not in EXITS
    pytest.skip("CULM-9: RenderState reserved, no advancer")


@pytest.mark.parametrize("mod,sym,_note", _all_exit_refs(), ids=[f"{m}:{s}" for m, s, _ in _all_exit_refs()])
def test_exit_symbols_importable(mod: str, sym: str, _note: str) -> None:
    _resolve(mod, sym)


def test_global_unwedgers_wired() -> None:
    for mod, sym, _note in GLOBAL_UNWEDGERS:
        _resolve(mod, sym)
    from fanops.pipeline import advance
    assert "heal_corrupt_gates" in inspect.getsource(advance)


def test_cli_recovery_verbs_registered() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "fanops.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = proc.stdout
    for verb in CLI_VERBS:
        assert verb in help_text, f"CLI verb {verb!r} missing from fanops --help"
