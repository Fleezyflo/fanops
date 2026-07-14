#!/usr/bin/env python3
"""Layer-1 fixture generator: CHARACTERIZE the legacy framing resolver from its own commit.

Layer 1 records ONLY what is directly observable at the legacy resolver boundary, or at deliberately
instrumented stub boundaries:

  * the scenario id and its controlled inputs (the stub returns / raises),
  * the invoked function NAMES and their ORDER,
  * the call ARGUMENTS,
  * each stub's outcome, as {"kind": "return"|"raise"} — distinct, never conflated,
  * the final 3-TUPLE the legacy resolver returned,
  * exceptions that ACTUALLY ESCAPED it (type only, never the message).

It NEVER derives a semantic cause from a None, a [], a centered output, or the absence of a call, and
it NEVER records attempt states, applicability, required-for-centering, failure events, negative
results, degraded strategies, or event chains. Those are INVENTIONS OF THE NEW DESIGN. Recording them
under a legacy SHA would present authored expectations as observed history — the design certifying
itself. They live in Layer 2 (tests/fixtures/framing_contract_expectations.json), which says plainly
that it is authored against the spec.

ONE deliberate exclusion: the new resolver threads a `_trace=` kwarg the legacy code has no concept of.
It is the instrumentation channel itself, so comparing it would be circular. It is DROPPED from the
recorded arguments, and the replay asserts equality of every OTHER argument. This is stated here rather
than left implicit.

PROVENANCE. The active working tree is NEVER moved backward — this repo has a dozen-plus worktrees and
more than one session on the primary tree, so a `git checkout <legacy-sha>` there is visible to all of
them. The legacy code is read from a SEPARATE detached worktree:

    git worktree add --detach /tmp/legacy <legacy_sha>
    python scripts/gen_framing_vectors.py --legacy-checkout /tmp/legacy --legacy-sha <legacy_sha> --update
    git worktree remove /tmp/legacy

The generator REFUSES unless: the legacy checkout is CLEAN; its HEAD EQUALS the expected SHA; the
expected legacy function qualname EXISTS there (else it would silently characterize the NEW code); the
generator's own COMMITTED sha256 matches its file on disk; and `--update` authorizes the overwrite.

CI validates the provenance and the checksum and NEVER regenerates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

VECTOR_SCHEMA_VERSION = 1
LEGACY_QUALNAME = "clip._resolve_framing"
_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _REPO / "tests" / "fixtures" / "framing_routing_vectors.json"

# The framing seams the resolver calls. Stubbing exactly these makes every scenario deterministic with
# no cv2, no ffmpeg and no media — and makes the CALL SEQUENCE itself observable.
_SEAMS = ("_framing_runtime_or_raise", "detect_window", "classify_window",
          "speaker_track", "subject_focus", "motion_saliency")

CT_MULTI, CT_SINGLE = "multi-speaker-talk", "single-speaker-talk"
CT_MUSIC, CT_SILENT, CT_NOPEOPLE = "music", "silent", "no-people"

_STATS = {"fps": 4.0, "frames": [[[0.5, 0.5, 0.3, 0.42, 0.9]]]}
_FOCUS = [0.61, 0.44, 0.30, 0.38]                       # a 4-tuple: subject lock (zooms)
_SAL = [0.61, 0.44]                                     # a 2-tuple: motion saliency (pans, never zooms)
_TRACK = [[0.0, 5.0, 0.30, 0.50, 0.28, 0.40], [5.0, 10.0, 0.70, 0.50, 0.28, 0.40]]


def _ret(v):
    return {"kind": "return", "value": v}


def _raise(exc_type):
    return {"kind": "raise", "exc_type": exc_type}


def _sc(sid, *, ct, detect=_STATS, track=None, focus=None, sal=None, smart=True,
        runtime=None, overrides=None):
    stubs = {"_framing_runtime_or_raise": runtime or _ret("<rt>"),
             "detect_window": _ret(detect), "classify_window": _ret(ct),
             "speaker_track": _ret(track), "subject_focus": _ret(focus), "motion_saliency": _ret(sal)}
    stubs.update(overrides or {})
    return {"id": sid, "smart_framing": smart, "window": [1.0, 11.0],
            "src": {"id": "src_fixture", "width": 1920, "height": 1080}, "stubs": stubs}


# The matrix. Every branch of the legacy routing, plus every way an exception escapes it.
SCENARIOS = [
    _sc("smart_framing_off", ct=CT_SINGLE, focus=_FOCUS, smart=False),
    _sc("preflight_refuses", ct=CT_SINGLE, runtime=_raise("ToolchainMissingError")),
    # CT_MULTI: track wins; a failed track falls through to SINGLE and gets NO saliency.
    _sc("ct_multi_track", ct=CT_MULTI, track=_TRACK),
    _sc("ct_multi_no_track_then_focus", ct=CT_MULTI, track=None, focus=_FOCUS),
    _sc("ct_multi_no_track_no_focus", ct=CT_MULTI, track=None, focus=None, sal=_SAL),
    # CT_SINGLE: subject_focus only — saliency is NOT applicable, even when it would have succeeded.
    _sc("ct_single_focus", ct=CT_SINGLE, focus=_FOCUS),
    _sc("ct_single_no_focus", ct=CT_SINGLE, focus=None, sal=_SAL),
    # CT_MUSIC / CT_SILENT: subject_focus, then saliency.
    _sc("ct_music_focus", ct=CT_MUSIC, focus=_FOCUS),
    _sc("ct_music_saliency", ct=CT_MUSIC, focus=None, sal=_SAL),        # <- the D9 vector: ct MUST stay None
    _sc("ct_music_centered", ct=CT_MUSIC, focus=None, sal=None),
    _sc("ct_silent_focus", ct=CT_SILENT, focus=_FOCUS),
    _sc("ct_silent_saliency", ct=CT_SILENT, focus=None, sal=_SAL),      # <- the D9 vector again
    # CT_NOPEOPLE: saliency only — subject_focus is NOT applicable.
    _sc("ct_nopeople_saliency", ct=CT_NOPEOPLE, sal=_SAL),              # <- the D9 vector again
    _sc("ct_nopeople_centered", ct=CT_NOPEOPLE, sal=None),
    # DETECTION FAILED (stats=None). classify_window MANUFACTURES no-people from it. The legacy resolver
    # still runs saliency and can still return a focus. The new resolver must return the SAME TUPLE (only
    # its diagnostic outcome differs) — short-circuiting here would change the render fingerprint of every
    # affected clip and make the daemon re-render it. These two vectors are what pin that.
    _sc("detect_failed_saliency", ct=CT_NOPEOPLE, detect=None, sal=_SAL),
    _sc("detect_failed_centered", ct=CT_NOPEOPLE, detect=None, sal=None),
    # Exceptions that ACTUALLY escape the legacy resolver (D1: stage_lock raises through detect_window;
    # subject_focus and motion_saliency call extract_frames_grid outside their try).
    _sc("detect_window_stage_busy", ct=CT_SINGLE, overrides={"detect_window": _raise("StageBusyError")}),
    _sc("detect_window_oserror", ct=CT_SINGLE, overrides={"detect_window": _raise("OSError")}),
    _sc("speaker_track_raises", ct=CT_MULTI, overrides={"speaker_track": _raise("RuntimeError")}),
    _sc("subject_focus_raises", ct=CT_SINGLE, overrides={"subject_focus": _raise("RuntimeError")}),
    _sc("motion_saliency_raises", ct=CT_NOPEOPLE, overrides={"motion_saliency": _raise("RuntimeError")}),
]

_EXC = {"ToolchainMissingError": "fanops.errors", "StageBusyError": "fanops.errors",
        "RuntimeError": "builtins", "OSError": "builtins"}


def _exc_for(name: str) -> BaseException:
    mod = __import__(_EXC[name], fromlist=["x"])
    return getattr(mod, name)(f"fixture: {name}")


def _jsonable(v):
    """JSON-safe rendering of a recorded call argument. The opaque framing runtime becomes a marker —
    its IDENTITY across calls is the observable (one detector, threaded), not its contents."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return "<rt>" if getattr(v, "__class__", None).__name__ in ("_FramingRuntime", "str") else "<obj>"


def run_scenario(scenario: dict, *, resolve, framing_mod, cfg_cls) -> dict:
    """Drive ONE scenario against whichever resolver was handed in, recording only what is observable.

    Shared by the generator (legacy tree) and the replay test (new tree) — one driver, so a difference
    can never be an artifact of two different harnesses."""
    calls: list = []
    saved = {name: getattr(framing_mod, name) for name in _SEAMS}

    def _mk(name, spec):
        def _stub(*args, **kw):
            rec = {k: _jsonable(v) for k, v in kw.items() if k != "_trace"}   # `_trace` is the new channel itself
            calls.append({"fn": name, "args": rec})
            if spec["kind"] == "raise":
                raise _exc_for(spec["exc_type"])
            v = spec["value"]
            if name == "speaker_track" and v is not None:
                return [tuple(s) for s in v]
            if name in ("subject_focus", "motion_saliency") and v is not None:
                return tuple(v)
            return v
        return _stub

    class _Src:
        pass
    src = _Src()
    for k, v in scenario["src"].items():
        setattr(src, k, v)
    src.source_path = "/fixture/none.mp4"

    os.environ["FANOPS_SMART_FRAMING"] = "1" if scenario["smart_framing"] else "0"
    try:
        for name in _SEAMS:
            setattr(framing_mod, name, _mk(name, scenario["stubs"][name]))
        cs, ce = scenario["window"]
        try:
            out = resolve(cfg_cls(root=Path(os.environ["FANOPS_FIXTURE_ROOT"])), src, cs, ce)
            result = {"kind": "return", "value": _jsonable(list(out))}
        except BaseException as exc:                    # noqa: BLE001 — the ESCAPE is the observation
            result = {"kind": "raise", "exc_type": type(exc).__name__}
    finally:
        for name, fn in saved.items():
            setattr(framing_mod, name, fn)
    return {"id": scenario["id"], "calls": calls, "result": result}


def _emit_child() -> int:
    """Run inside the LEGACY checkout (fresh interpreter, its src/ first on sys.path). Prints JSON."""
    import tempfile
    from fanops import clip, framing
    from fanops.config import Config
    os.environ["FANOPS_FIXTURE_ROOT"] = tempfile.mkdtemp(prefix="fixt_")
    out = [run_scenario(s, resolve=clip._resolve_framing, framing_mod=framing, cfg_cls=Config)
           for s in SCENARIOS]
    sys.stdout.write(json.dumps(out))
    return 0


def _git(*args, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


def _git_bytes(*args, cwd: Path) -> bytes:
    """Raw stdout — NEVER stripped. A blob's trailing newline is part of the blob, so hashing a
    stripped copy compares a different byte string and the self-check would always refuse."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True).stdout


def fixture_checksum(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "fixture_checksum"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-vectors", action="store_true", help=argparse.SUPPRESS)   # internal: the child
    ap.add_argument("--legacy-checkout", type=Path)
    ap.add_argument("--legacy-sha")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--update", action="store_true", help="authorize overwriting an existing fixture")
    a = ap.parse_args()

    if a.emit_vectors:
        return _emit_child()
    if not (a.legacy_checkout and a.legacy_sha):
        ap.error("--legacy-checkout and --legacy-sha are required")

    me = Path(__file__).resolve()
    disk = hashlib.sha256(me.read_bytes()).hexdigest()
    try:                                              # the generator must itself be COMMITTED and unmodified
        committed = _git_bytes("cat-file", "blob", f"HEAD:scripts/{me.name}", cwd=_REPO)
        if hashlib.sha256(committed).hexdigest() != disk:
            print("REFUSED: the generator on disk differs from its committed blob. Commit it first —\n"
                  "         a fixture generated by an uncommitted generator has no provenance.", file=sys.stderr)
            return 2
    except subprocess.CalledProcessError:
        print(f"REFUSED: scripts/{me.name} is not committed. Commit the generator BEFORE the fixture.",
              file=sys.stderr)
        return 2

    lc = a.legacy_checkout.resolve()
    if _git("status", "--porcelain", cwd=lc):
        print(f"REFUSED: legacy checkout {lc} is DIRTY — it would not characterize {a.legacy_sha}.", file=sys.stderr)
        return 2
    head = _git("rev-parse", "HEAD", cwd=lc)
    if not head.startswith(a.legacy_sha) and not a.legacy_sha.startswith(head[:7]):
        print(f"REFUSED: legacy checkout HEAD is {head}, expected {a.legacy_sha}.", file=sys.stderr)
        return 2
    legacy_clip = lc / "src" / "fanops" / "clip.py"
    if "def _resolve_framing" not in legacy_clip.read_text():
        print(f"REFUSED: {LEGACY_QUALNAME} does not exist in {lc} — this would characterize the WRONG code.",
              file=sys.stderr)
        return 2
    if a.out.exists() and not a.update:
        print(f"REFUSED: {a.out} exists. Pass --update to authorize the overwrite.", file=sys.stderr)
        return 2

    env = dict(os.environ, PYTHONPATH=str(lc / "src"))
    child = subprocess.run([sys.executable, str(me), "--emit-vectors"], cwd=str(lc), env=env,
                           check=True, capture_output=True, text=True)
    observed = json.loads(child.stdout)

    doc = {
        "vector_schema_version": VECTOR_SCHEMA_VERSION,
        "layer": 1,
        "what_this_is": ("OBSERVED legacy behaviour ONLY: invoked functions, their order, their arguments, "
                         "the stub outcomes, the returned 3-tuple, and the exceptions that actually escaped. "
                         "No attempt states, no applicability, no failure/negative events, no degraded lists, "
                         "no event chains — those are inventions of the new design and live in Layer 2."),
        "excluded_argument": "_trace (the new instrumentation channel; comparing it would be circular)",
        "legacy_source_commit_sha": head,
        "legacy_function_qualname": LEGACY_QUALNAME,
        "generator_commit_sha": _git("rev-parse", "HEAD", cwd=_REPO),
        "generator_file_sha256": disk,
        "python_version": ".".join(str(x) for x in sys.version_info[:2]),
        "scenarios": [{**s, "observed": next(o for o in observed if o["id"] == s["id"])} for s in SCENARIOS],
    }
    doc["fixture_checksum"] = fixture_checksum(doc)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {a.out} — {len(SCENARIOS)} scenarios characterized from {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
