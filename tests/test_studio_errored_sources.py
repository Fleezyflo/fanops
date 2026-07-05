# tests/test_studio_errored_sources.py
# MOL-123: an errored source must be LOUD in the cockpit. On 2026-07-05 a source sat in state=error with a
# fatal TimeoutExpired while the Studio strip read "idle" — the operator's only signal was absence. This
# surfaces the error state (strip count + Run-tab list with the reason) and a Resume button wired to the
# SAME stage-aware helper the CLI uses (pipeline.resume_source, MOL-121) — no parallel implementation.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.studio import views, actions


def _cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Config(root=tmp_path)


def _add_errored(cfg, sid="src_1", *, reason="TimeoutExpired: ffmpeg ... timed out after 600.0 seconds"):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path=f"/x/{sid}.mp4", state=SourceState.error,
                              error_reason=reason, batch_id="batch_1", origin_kind="native",
                              transcript=[{"start": 0, "end": 1, "text": "hi"}], meta={"transcribed": True}))


# ── system strip: errored-source count ──

def test_strip_reports_errored_source_count(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _add_errored(cfg, "src_1"); _add_errored(cfg, "src_2")
    strip = views.build_system_strip(cfg)
    assert strip["errored_sources"] == 2

def test_strip_clean_when_no_errored_sources(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="ok", source_path="/x/ok.mp4", state=SourceState.transcribed, origin_kind="native"))
    strip = views.build_system_strip(cfg)
    assert strip["errored_sources"] == 0             # healthy source -> no false alarm


# ── Run-tab list: id + full reason + batch ──

def test_pipeline_status_lists_errored_sources_with_reason(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _add_errored(cfg, "src_1", reason="TimeoutExpired: ffmpeg silencedetect timed out after 600.0 seconds")
    status = views.pipeline_status(cfg)
    errored = status["errored"]
    assert len(errored) == 1
    row = errored[0]
    assert row["id"] == "src_1"
    assert row["batch_id"] == "batch_1"
    assert "TimeoutExpired" in row["error_reason"] and "600.0 seconds" in row["error_reason"]   # full reason, not truncated

def test_pipeline_status_includes_moments_empty_as_recoverable(tmp_path, monkeypatch):
    # moments_empty is the documented re-runnable sibling — it belongs in the same recovery list.
    cfg = _cfg(tmp_path, monkeypatch)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="empty_1", source_path="/x/e.mp4", state=SourceState.moments_empty, origin_kind="native"))
    ids = [r["id"] for r in views.pipeline_status(cfg)["errored"]]
    assert "empty_1" in ids


# ── Resume action: shared helper, operator-clicked ──

def test_resume_action_calls_the_shared_helper(tmp_path, monkeypatch):
    # The Studio Resume must go through pipeline.resume_source (MOL-121), so behavior is identical to the
    # CLI: an errored source with a transcript resumes at 'transcribed', transcript preserved.
    cfg = _cfg(tmp_path, monkeypatch)
    _add_errored(cfg, "src_1")
    res = actions.resume_source_studio(cfg, "src_1")
    assert res.ok
    s = Ledger.load(cfg).sources["src_1"]
    assert s.state is SourceState.transcribed and s.transcript and s.error_reason is None

def test_resume_action_unknown_source_is_not_ok(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = actions.resume_source_studio(cfg, "nope")
    assert not res.ok


# ── route-level: the /run/resume endpoint re-renders the panel with the source recovered ──

def test_run_resume_route_recovers_and_rerenders(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("flask")
    cfg = _cfg(tmp_path, monkeypatch)
    _add_errored(cfg, "src_1")
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    client = app.test_client()
    # errored source is listed before resume
    assert b"src_1" in client.get("/run").data
    r = client.post("/run/resume", data={"source_id": "src_1"})
    assert r.status_code == 200                                  # htmx swaps 2xx only
    # the shared helper ran: source is now transcribed, transcript preserved
    s = Ledger.load(cfg).sources["src_1"]
    assert s.state is SourceState.transcribed and s.transcript
