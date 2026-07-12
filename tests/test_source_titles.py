# tests/test_source_titles.py — U1: pipeline source titles stamped at first moment-pick ingest.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, SourceState, MomentDecision, MomentPick, source_display_title)
from fanops.moments import ingest_moments, request_moments, _sanitize_source_title
from fanops.agentstep import response_path, latest_request_id
from fanops.responder import screen_model_text


def _mp(s, e, reason="r"):
    return MomentPick(start=s, end=e, reason=reason)


def _src(led, cfg, dur=60.0, *, path="/videos/interview.mp4"):
    led.add_source(Source(id="src_1", source_path=path, state=SourceState.signalled, duration=dur,
                          language="en", transcript=[{"start": 0, "end": 8, "text": "hello"}],
                          signal_peaks=[], meta={"transcribed": True}))


def _write_dec(cfg, key, source_id, *, picks, source_title=None, screen=True):
    rid = latest_request_id(cfg, "moments", key)
    dec = MomentDecision(source_id=source_id, request_id=rid, picks=picks,
                         source_title=source_title)
    if screen:
        dec = screen_model_text(dec)
    response_path(cfg, "moments", key).write_text(dec.model_dump_json())


def _seed_multi_pick_persona_accounts(cfg, handles):
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": str(i + 1), "platforms": ["instagram"], "status": "active",
         "persona": f"voice {h}", "content_focus": ["punchlines"],
         "selection_scope": "credibility_first", "hook_angle": "curiosity",
         "hashtag_corpus": [f"#tag{i}"]} for i, h in enumerate(handles)]}))
    return Accounts.load(cfg)


def test_stamp_once_from_first_decision(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    _write_dec(cfg, "src_1", "src_1", picks=[_mp(0, 8, "win")], source_title="Studio freestyle session")
    led = ingest_moments(led, cfg, "src_1")
    assert led.sources["src_1"].title == "Studio freestyle session"


def test_second_account_does_not_overwrite(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_multi_pick_persona_accounts(cfg, ["a", "b"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    _write_dec(cfg, "src_1.a", "src_1", picks=[_mp(0, 8)], source_title="First account title")
    _write_dec(cfg, "src_1.b", "src_1", picks=[_mp(20, 28)], source_title="Second account title")
    led = ingest_moments(led, cfg, "src_1")
    assert led.sources["src_1"].title == "First account title"


def test_absent_field_tolerated(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    _write_dec(cfg, "src_1", "src_1", picks=[_mp(0, 8)], source_title=None, screen=False)
    led = ingest_moments(led, cfg, "src_1")
    assert led.sources["src_1"].title is None


def test_sanitize_word_cap_and_char_cap():
    raw = "one two three four five six seven eight nine ten " + "x" * 100 + " — dashy"
    out = _sanitize_source_title(raw)
    assert out is not None
    assert len(out.split()) <= 8
    assert len(out) <= 80
    assert "—" not in out


def test_stamp_before_empty_picks(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    _write_dec(cfg, "src_1", "src_1", picks=[], source_title="Empty picks but titled")
    led = ingest_moments(led, cfg, "src_1")
    assert led.sources["src_1"].title == "Empty picks but titled"
    assert led.sources["src_1"].state is SourceState.moments_empty


def test_source_display_title_fallback():
    assert source_display_title(Source(id="x", source_path="/foo/bar clip.mp4")) == "bar clip"
    assert source_display_title(Source(id="fallback_id", source_path="")) == "fallback_id"
    titled = Source(id="x", source_path="/a.mp4", title="Custom Title")
    assert source_display_title(titled) == "Custom Title"


def test_dotted_path_stamps(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_multi_pick_persona_accounts(cfg, ["zeta", "alpha"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    _write_dec(cfg, "src_1.zeta", "src_1", picks=[_mp(0, 8)], source_title="Zeta title")
    _write_dec(cfg, "src_1.alpha", "src_1", picks=[_mp(20, 28)], source_title="Alpha title")
    led = ingest_moments(led, cfg, "src_1")
    assert led.sources["src_1"].title == "Alpha title"
