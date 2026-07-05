# tests/test_source_resume.py
# MOL-121: stage-aware source resume. `retry-source` must not discard a good transcript — an errored
# source whose transcript is intact resumes at `transcribed` (re-enters at signals, the stage that broke),
# not `catalogued` (a full re-transcribe of ~20 min of proven work). One helper owns the transition so the
# CLI verb and the Studio Resume button (MOL-123) call the SAME code, never a parallel implementation.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import resume_source
from fanops.cli import main


def _errored(cfg, *, transcript, transcribed):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="TimeoutExpired: ffmpeg ...",
                              transcript=transcript, meta={"transcribed": transcribed}))


def test_resume_keeps_transcript_and_re_enters_at_signals(tmp_path):
    # AUTO: transcript present -> resume at `transcribed`, transcript + flag preserved, error cleared.
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.transcribed
    assert s.error_reason is None
    assert s.transcript == [{"start": 0, "end": 1, "text": "hi"}]   # NOT discarded
    assert s.meta["transcribed"] is True                            # re-transcribe NOT forced


def test_resume_without_transcript_falls_back_to_full_retry(tmp_path):
    # No transcript (transcription itself never produced one) -> full retry: catalogued + flag cleared,
    # byte-identical to today's retry-source.
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=None, transcribed=True)               # flag set but no transcript = never really transcribed
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued
    assert s.meta["transcribed"] is False
    assert s.error_reason is None


def test_resume_empty_transcript_is_full_retry(tmp_path):
    # transcript == [] means "ran, no speech" — but an errored source with an empty transcript never
    # reached a usable transcribed state; treat it as full retry (there is nothing to preserve).
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[], transcribed=True)
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.meta["transcribed"] is False


def test_resume_refuses_a_healthy_source(tmp_path):
    # Guard: resume only acts on error / moments_empty. A healthy in-flight source must NEVER be rewound
    # (that would silently re-mint / duplicate work). resume_source returns False and leaves state intact.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided,
                              transcript=[{"start": 0, "end": 1, "text": "hi"}], meta={"transcribed": True}))
    with Ledger.transaction(cfg) as led:
        changed = resume_source(led, "s1")
        assert changed is False
    assert Ledger.load(cfg).sources["s1"].state is SourceState.moments_decided


def test_resume_moments_empty_full_retry(tmp_path):
    # moments_empty is the documented re-runnable sibling — resume it as a full retry (re-request moments).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_empty))
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1") is True
    assert Ledger.load(cfg).sources["s1"].state is SourceState.catalogued


# ── CLI surface: retry-source defaults to AUTO (stage-aware); --from-stage catalogued forces full retry ──

def test_cli_retry_source_auto_preserves_transcript(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    assert main(["retry-source", "s1"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.transcribed and s.transcript and s.meta["transcribed"] is True


def test_cli_retry_source_from_stage_catalogued_forces_full_retry(tmp_path, monkeypatch):
    # The legacy full-retry stays reachable explicitly, even with a transcript present.
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    assert main(["retry-source", "s1", "--from-stage", "catalogued"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.meta["transcribed"] is False


def test_cli_retry_source_unknown_id_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Config(root=tmp_path)
    assert main(["retry-source", "nope"]) == 2
    assert "no such source: nope" in capsys.readouterr().err
