"""control.load_guidance — the ONE validated reader for context.md (the brand brief injected into
every moment + caption prompt, the #1 output lever). The old readers (_guidance in moments.py +
caption.py) returned "" SILENTLY when the file was absent — a silent failure on the most important
input. This contract is fail-OPEN but LOUD: missing/empty/oversize each degrade visibly (a logged
warning), never crash an autonomous run. Mirrors config.tuning()'s fail-open-with-warning style."""
import logging
from fanops.config import Config
from fanops.control import load_guidance, _MAX_GUIDANCE_BYTES


def _cfg(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    return cfg


def test_present_nonempty_returns_verbatim_no_warning(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    cfg.context_path.write_text("BRAND: confident, bilingual. Pick the bars.")
    with caplog.at_level(logging.WARNING):
        out = load_guidance(cfg)
    assert out == "BRAND: confident, bilingual. Pick the bars."
    assert not caplog.records                                  # a healthy brief logs nothing


def test_missing_file_is_loud_not_silent(tmp_path, caplog):
    cfg = _cfg(tmp_path)                                       # no context.md written
    with caplog.at_level(logging.WARNING):
        out = load_guidance(cfg)
    assert out == ""                                           # degraded, fail-open
    assert any("context.md" in r.getMessage() for r in caplog.records)   # but LOUD


def test_empty_or_whitespace_is_loud(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    cfg.context_path.write_text("   \n\t  \n")
    with caplog.at_level(logging.WARNING):
        out = load_guidance(cfg)
    assert out == ""
    assert any("context.md" in r.getMessage() for r in caplog.records)


def test_oversize_is_bounded_and_warned(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    big = "x" * (_MAX_GUIDANCE_BYTES + 5000)
    cfg.context_path.write_text(big)
    with caplog.at_level(logging.WARNING):
        out = load_guidance(cfg)
    assert len(out.encode("utf-8")) <= _MAX_GUIDANCE_BYTES     # injected text is bounded
    assert big.startswith(out)                                 # it's a prefix, not garbled
    assert any("context.md" in r.getMessage() for r in caplog.records)


def test_unreadable_never_crashes(tmp_path, caplog, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.context_path.write_text("BRAND: x")
    monkeypatch.setattr(type(cfg.context_path), "read_text",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with caplog.at_level(logging.WARNING):
        out = load_guidance(cfg)                               # must not raise
    assert out == ""
    assert any("context.md" in r.getMessage() for r in caplog.records)
