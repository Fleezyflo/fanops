# tests/test_machine_reopen_admission.py — T2.3: FANOPS_QUEUE_GATE is the system's ONE admission gate,
# but `adjust.amplify` re-opened an existing source by calling `request_moments` directly, so machine
# work walked past it and the tick's converge loop carried the re-open to completion in one pass. The
# guard parks a machine-origin re-open on `Source.meta["pending_reopen"]`; an operator releases it.
#
# FANOPS_QUEUE_GATE is in conftest's _LEAKY_ENV (stripped, so the code default ON applies). Every test
# below sets it EXPLICITLY anyway — the gate is the subject here, never an inherited ambient default.
import json

from fanops.adjust import amplify
from fanops.agentstep import gate_keys_for, latest_request_id, pending, request_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Moment, MomentState, Platform, Post, PostState, Source,
                           SourceState)
from fanops.moments import ingest_moments, request_moments
from fanops.studio import actions


def _source(led, sid="src_1"):
    led.add_source(Source(id=sid, source_path="/s.mp4", state=SourceState.moments_decided, duration=30.0,
                          transcript=[{"start": 14, "end": 18, "text": "they slept on me"}],
                          signal_peaks=[], meta={"transcribed": True}))
    return sid


def _analyzed_lineage(led, sid="src_1"):
    """A winner post hanging off `sid` — the input adjust.amplify re-opens a source from."""
    _source(led, sid)
    led.add_moment(Moment(id="mom_1", parent_id=sid, content_token="14-21", start=14, end=21,
                          reason="punchline", transcript_excerpt="they slept on me",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 400.0}, public_url="dryrun://1"))
    return sid


# ---- the firewall ----------------------------------------------------------------------------

def test_gate_on_machine_origin_parks_and_writes_nothing(tmp_path, monkeypatch):
    """THE firewall test. Gate ON + a machine origin: nothing reaches disk, no state moves, and the
    ask is recorded where an operator can find it."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _source(led)
    led = request_moments(led, cfg, sid, guidance="AMPLIFY: more like that", origin="amplify")
    # nothing was served: no gate key of any shape (bare OR per-account), so the converge loop that
    # reads pending gates has nothing to carry forward.
    assert gate_keys_for(cfg, "moments", sid) == []
    assert not request_path(cfg, "moments", sid).exists()
    assert pending(cfg, kind="moments") == []
    # the source did NOT advance — moments_requested is exactly the flip a parked request must not do.
    assert led.sources[sid].state is SourceState.moments_decided
    # the ask survives, with its provenance and the guidance it was carrying.
    parked = led.sources[sid].meta["pending_reopen"]
    assert parked["origin"] == "amplify"
    assert parked["guidance"] == "AMPLIFY: more like that"
    assert parked["requested_at"].endswith("Z")
    assert "reopen_parked" in cfg.log_path.read_text()


def test_park_is_last_write_wins(tmp_path, monkeypatch):
    """At most one parked re-open per source: a second ask replaces the first, it does not queue."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _source(led)
    led = request_moments(led, cfg, sid, guidance="first", origin="amplify")
    led = request_moments(led, cfg, sid, guidance="second", origin="variant_amplify")
    parked = led.sources[sid].meta["pending_reopen"]
    assert parked["guidance"] == "second" and parked["origin"] == "variant_amplify"


# ---- the two paths this ticket must leave untouched ------------------------------------------

def test_gate_on_operator_origin_is_untouched(tmp_path, monkeypatch):
    """The operator's own path. Gate ON, default origin: the request is written and the state flips,
    exactly as before the guard existed — the guard short-circuits on its first term."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _source(led)
    led = request_moments(led, cfg, sid, guidance="operator guidance")
    assert request_path(cfg, "moments", sid).exists()
    assert latest_request_id(cfg, "moments", sid) is not None
    assert led.sources[sid].state is SourceState.moments_requested
    assert "pending_reopen" not in led.sources[sid].meta


def test_gate_off_machine_origin_is_byte_identical(tmp_path, monkeypatch):
    """A gate-OFF deployment. Same machine origin, gate 0: the request is written and the state flips
    — nothing is parked, so FANOPS_QUEUE_GATE=0 keeps behaving exactly as it did."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _source(led)
    led = request_moments(led, cfg, sid, guidance="AMPLIFY: more like that", origin="amplify")
    payload = json.loads(request_path(cfg, "moments", sid).read_text())
    assert payload["guidance"] == "AMPLIFY: more like that"
    assert led.sources[sid].state is SourceState.moments_requested
    assert "pending_reopen" not in led.sources[sid].meta


# ---- the release ------------------------------------------------------------------------------

def test_operator_release_writes_the_parked_request(tmp_path, monkeypatch):
    """operator_release=True is the key that opens the guard without re-parking. Gate stays ON."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    sid = _source(led)
    led = request_moments(led, cfg, sid, guidance="AMPLIFY: more like that", origin="amplify")
    assert not request_path(cfg, "moments", sid).exists()
    led.sources[sid].meta.pop("pending_reopen", None)
    led = request_moments(led, cfg, sid, guidance="AMPLIFY: more like that",
                          origin="amplify", operator_release=True)
    payload = json.loads(request_path(cfg, "moments", sid).read_text())
    assert payload["guidance"] == "AMPLIFY: more like that"
    assert led.sources[sid].state is SourceState.moments_requested
    assert "pending_reopen" not in led.sources[sid].meta


def test_release_of_an_unparked_source_releases_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        sid = _source(led)
    res = actions.release_reopens(cfg, source_ids=[sid, "src_missing"])
    assert res.ok and res.detail["released"] == 0
    assert not request_path(cfg, "moments", sid).exists()
    assert Ledger.load(cfg).sources[sid].state is SourceState.moments_decided


# ---- the caller the whole ticket exists for ---------------------------------------------------

def test_amplify_under_gate_on_mints_zero_moment_requests(tmp_path, monkeypatch):
    """adjust.amplify is the back door. Under the gate it must produce NOTHING the converge loop can
    pick up: no gate for the next stage to answer, and an ingest pass mints no moment."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _analyzed_lineage(led)
    before = {m.id for m in led.moments.values()}
    led = amplify(led, cfg, ["p1"])
    assert gate_keys_for(cfg, "moments", sid) == []
    assert pending(cfg, kind="moments") == []
    assert led.sources[sid].state is SourceState.moments_decided
    assert led.sources[sid].meta["pending_reopen"]["origin"] == "amplify"
    led = ingest_moments(led, cfg, sid)          # the converge loop's very next stage
    assert {m.id for m in led.moments.values()} == before


def test_status_line_counts_parked_reopens(tmp_path, monkeypatch, capsys):
    """A park writes no request file and flips no state, so every other count on `fanops status` stays
    flat — a headless operator would never learn the work exists without this field."""
    from fanops.cli import main
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        sid = _source(led)
    assert main(["status"]) == 0
    assert "reopens_parked=0" in capsys.readouterr().out
    with Ledger.transaction(cfg) as led:
        request_moments(led, cfg, sid, guidance="g", origin="amplify")
    assert main(["status"]) == 0
    assert "reopens_parked=1" in capsys.readouterr().out

# ---- MOL-840: a park must not spend amplify budget --------------------------------------------

def _amplify_count_trace(fn, *a, **k):
    """MOL-757-style executed-line probe: every `amplify` line that names amplify_count."""
    import linecache
    import sys
    hit = []

    def tracer(frame, event, arg):
        if event == "line" and frame.f_code.co_name == "amplify":
            line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
            if "amplify_count" in line:
                hit.append(line.strip())
        return tracer

    sys.settrace(tracer)
    try:
        out = fn(*a, **k)
    finally:
        sys.settrace(None)
    return out, hit


def _load_amplify_at(sha: str, tmp_path):
    """Load amplify() from a historical tree without mutating the live fanops.adjust module."""
    import importlib.util
    import subprocess
    src = subprocess.check_output(["git", "show", f"{sha}:src/fanops/adjust.py"], text=True)
    path = tmp_path / f"adjust_{sha[:8]}.py"
    path.write_text(src)
    spec = importlib.util.spec_from_file_location(f"fanops_adjust_{sha[:8]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.amplify


def test_parked_ticks_leave_amplify_count_unchanged(tmp_path, monkeypatch):
    """Gate ON + machine origin: N consecutive parked ticks leave amplify_count untouched, and
    amplify still fires on tick N+1 (not silenced by a spent budget)."""
    from fanops.adjust import MAX_AMPLIFY_PER_SOURCE
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    sid = _analyzed_lineage(led)
    start = int(led.sources[sid].meta.get("amplify_count", 0))
    assert start == 0
    n = MAX_AMPLIFY_PER_SOURCE  # the old bug exhausted the budget in exactly this many parks
    for _ in range(n):
        led = amplify(led, cfg, ["p1"])
        assert int(led.sources[sid].meta.get("amplify_count", 0)) == start
        assert led.sources[sid].meta.get("pending_reopen", {}).get("origin") == "amplify"
    # tick N+1 still fires — parks again, budget still untouched
    led = amplify(led, cfg, ["p1"])
    assert int(led.sources[sid].meta.get("amplify_count", 0)) == start
    assert led.sources[sid].meta["pending_reopen"]["origin"] == "amplify"
    assert gate_keys_for(cfg, "moments", sid) == []


def test_gate_off_amplify_count_matches_fae546e5_executed_lines(tmp_path, monkeypatch):
    """Gate OFF (served path): amplify_count increments exactly as at fae546e5 — pinned by the same
    executed-line comparison MOL-757 used, not a weak end-state-only assertion."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    old_amplify = _load_amplify_at("fae546e5", tmp_path)

    def _run(amplify_fn, root):
        cfg = Config(root=root); led = Ledger.load(cfg)
        _analyzed_lineage(led)
        return _amplify_count_trace(amplify_fn, led, cfg, ["p1"])

    live_root = tmp_path / "live"; live_root.mkdir()
    old_root = tmp_path / "old"; old_root.mkdir()
    led_live, lines_live = _run(amplify, live_root)
    led_old, lines_old = _run(old_amplify, old_root)
    assert int(led_live.sources["src_1"].meta["amplify_count"]) == 1
    assert int(led_old.sources["src_1"].meta["amplify_count"]) == 1
    # Both must EXECUTE the read + the increment (fae546e5 serve path). A future edit that drops the
    # increment silently would keep count==1 only if something else wrote it — the line probe catches that.
    # Exact executed-line equality for amplify_count touches — the MOL-757 pin style.
    assert lines_live == lines_old
    assert any("used + 1" in L for L in lines_live)
    assert any("used =" in L for L in lines_live)
    # Served on both sides: request written, nothing parked.
    assert request_path(Config(root=live_root), "moments", "src_1").exists()
    assert request_path(Config(root=old_root), "moments", "src_1").exists()
    assert "pending_reopen" not in led_live.sources["src_1"].meta
    assert "pending_reopen" not in led_old.sources["src_1"].meta


def test_release_then_amplify_charges_budget_once(tmp_path, monkeypatch):
    """Park (no charge) → operator_release (mints the request, still no charge) → serve via amplify → charge 1."""
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    sid = _analyzed_lineage(led)
    led = amplify(led, cfg, ["p1"])
    assert int(led.sources[sid].meta.get("amplify_count", 0)) == 0
    assert led.sources[sid].meta.get("pending_reopen")
    parked = led.sources[sid].meta["pending_reopen"]
    led.sources[sid].meta.pop("pending_reopen", None)
    led = request_moments(led, cfg, sid, guidance=parked.get("guidance") or "",
                          origin=parked.get("origin") or "amplify", operator_release=True)
    assert "pending_reopen" not in led.sources[sid].meta
    assert int(led.sources[sid].meta.get("amplify_count", 0)) == 0
    assert request_path(cfg, "moments", sid).exists()

    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    request_path(cfg, "moments", sid).unlink(missing_ok=True)
    led.sources[sid] = led.sources[sid].model_copy(update={"state": SourceState.moments_decided})
    led = amplify(led, cfg, ["p1"])
    assert int(led.sources[sid].meta.get("amplify_count", 0)) == 1
    assert request_path(cfg, "moments", sid).exists()
    assert "pending_reopen" not in led.sources[sid].meta
