# tests/test_hashtag_vocab.py — MOL-644: LLM niche vocabulary expands Layer A search roots (not corpus).
from __future__ import annotations
import json
from fanops.config import Config
from fanops.personas import add_persona, Personas
from fanops.persona_research import persona_terms, _aligned_pool
from fanops.hashtags import load_measurements
from fanops.fanops_hashtags import refresh_store
from fanops import hashtag_vocab as hv
from hashtag_scrape_fakes import _FakeClient


def _persona(cfg, pid="craft", niche=None, voice="syrian rap craft"):
    add_persona(cfg, name=pid, voice=voice, niche=list(niche) if niche is not None else ["hiphop"], id=pid)
    return pid


def _link_active(cfg, pid, handle="markmakmouly"):
    return _link_active_many(cfg, {handle: pid})


def _link_active_many(cfg, by_handle):
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "platforms": ["instagram"], "status": "active", "persona_id": pid}
        for h, pid in by_handle.items()]}))
    return Accounts.load(cfg)


def _replying(terms):
    """A claude_json stand-in that records every prompt it is asked and answers with fixed terms."""
    seen: list[str] = []

    def _fake(prompt, schema, **kw):
        seen.append(prompt); return {"terms": list(terms)}
    return _fake, seen


def test_persona_terms_ignores_durable_vocab(tmp_path):
    """MOL-719: durable LLM vocab NO LONGER widens the search roots. 46 of 72 live generated terms did
    not exist on Instagram at all and 106 of 107 corpus admissions attributed to an operator niche root,
    so the declared niche is the whole root set — with or without `cfg`."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"], voice="believe in yourself punchlines")
    hv.write_vocab(cfg, {pid: {"terms": ["syrianrap", "barsoverbeats", "Believe", "#fyp", ""],
                                "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"}})
    per = Personas.load(cfg).get(pid)
    assert persona_terms(per) == ["hiphop"]
    assert persona_terms(per, cfg) == ["hiphop"]                     # cfg no longer widens anything
    assert "syrianrap" not in persona_terms(per, cfg)
    assert "barsoverbeats" not in persona_terms(per, cfg)


def test_expand_persona_writes_validated_seeds_only(tmp_path, monkeypatch):
    """LLM output is sanitized; viral/junk tokens never land in the durable vocab file."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["undergroundmusic"])
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")

    def _fake_claude(prompt, schema, **kw):
        assert "viral" not in prompt.lower() or "do not" in prompt.lower()
        assert "high-reach" not in prompt.lower() or "do not" in prompt.lower()
        assert "undergroundmusic" in prompt
        return {"terms": ["Syrian Rap", "bars over beats", "#fyp", "viral", "love",
                          "within", "!!!", "drillscene"]}

    monkeypatch.setattr(hv, "claude_json", _fake_claude)
    r = hv.expand_persona_vocab(cfg, pid)
    assert r.get("ok") is True
    stored = hv.load_vocab(cfg).get(pid, {}).get("terms") or []
    assert "syrianrap" in stored and "barsoverbeats" in stored and "drillscene" in stored
    assert "fyp" not in stored and "viral" not in stored and "love" not in stored
    assert "within" not in stored
    # never mutates operator niche / corpus
    per = Personas.load(cfg).get(pid)
    assert list(per.niche) == ["undergroundmusic"]
    assert list(per.hashtag_corpus) == []


def test_expand_prompt_names_sibling_owned_terms(tmp_path, monkeypatch):
    """MOL-716: the LLM sees sibling niche + durable vocab as territory it must not propose."""
    cfg = Config(root=tmp_path)
    a = _persona(cfg, pid="craft", niche=["undergroundmusic"])
    b = _persona(cfg, pid="street", niche=["streetinterview"])
    _link_active_many(cfg, {"craftacct": a, "streetacct": b})
    hv.write_vocab(cfg, {b: {"terms": ["voxpop"], "expanded_at": "2026-07-28T00:00:00+00:00",
                             "source": "llm"}})

    def _fake_claude(prompt, schema, **kw):
        assert "Do NOT propose these terms already owned by sibling personas:" in prompt
        assert "streetinterview" in prompt and "voxpop" in prompt
        return {"terms": ["drillscene"]}

    monkeypatch.setattr(hv, "claude_json", _fake_claude)
    assert hv.expand_persona_vocab(cfg, a).get("ok") is True


def test_expand_filters_sibling_owned_terms_after_llm(tmp_path, monkeypatch):
    """MOL-716: prompt disobedience cannot write a sibling's niche or durable vocab into this row."""
    cfg = Config(root=tmp_path)
    a = _persona(cfg, pid="craft", niche=["undergroundmusic"])
    b = _persona(cfg, pid="street", niche=["streetinterview"])
    _link_active_many(cfg, {"craftacct": a, "streetacct": b})
    hv.write_vocab(cfg, {b: {"terms": ["#VoxPop"], "expanded_at": "2026-07-28T00:00:00+00:00",
                             "source": "llm"}})
    fake, _ = _replying(["street-interview", "VOX POP", "drillscene"])
    monkeypatch.setattr(hv, "claude_json", fake)

    r = hv.expand_persona_vocab(cfg, a)
    assert r.get("ok") is True
    assert hv.load_vocab(cfg).get(a, {}).get("terms") == ["drillscene"]



def test_input_fingerprint_is_stable_and_input_sensitive(tmp_path):
    """MOL-693: the fingerprint is stable across reloads and sensitive to every own-persona input."""
    cfg = Config(root=tmp_path)
    _persona(cfg, pid="craft", niche=["undergroundmusic"], voice="syrian rap craft")
    per = Personas.load(cfg).get("craft")
    fp = hv._input_fingerprint(per)
    assert isinstance(fp, str) and fp
    assert hv._input_fingerprint(Personas.load(cfg).get("craft")) == fp   # reload -> identical
    assert hv._input_fingerprint(per.model_copy(update={"niche": ["basementshow"]})) != fp
    assert hv._input_fingerprint(per.model_copy(update={"voice": "other voice"})) != fp
    assert hv._input_fingerprint(per.model_copy(update={"name": "other name"})) != fp


def test_sibling_niche_edit_marks_other_persona_due(tmp_path):
    """MOL-716: sibling territory is an input, so editing A invalidates B's current vocab fingerprint."""
    cfg = Config(root=tmp_path)
    a = _persona(cfg, pid="craft", niche=["undergroundmusic"])
    b = _persona(cfg, pid="street", niche=["streetinterview"])
    _link_active_many(cfg, {"craftacct": a, "streetacct": b})
    personas = Personas.load(cfg).all()
    data = {a: {"terms": ["drillscene"], "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"},
            b: {"terms": ["voxpop"], "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"}}
    for per in personas:
        data[per.id]["input_fp"] = hv._input_fingerprint(per, siblings=personas, data=data)
    assert hv.vocab_due_reason(next(p for p in personas if p.id == b), data, siblings=personas) is None

    from fanops.personas import update_persona
    update_persona(cfg, a, niche=["undergroundmusic", "basementshow"])
    personas = Personas.load(cfg).all()
    assert hv.vocab_due_reason(next(p for p in personas if p.id == b), data,
                               siblings=personas) == "inputs_changed"


def test_current_sibling_fingerprints_keep_unchanged_tick_call_free(tmp_path, monkeypatch):
    """MOL-716/MOL-693: once every row carries the sibling-aware shape, unchanged inputs cost zero calls."""
    cfg = Config(root=tmp_path)
    a = _persona(cfg, pid="craft", niche=["undergroundmusic"])
    b = _persona(cfg, pid="street", niche=["streetinterview"])
    _link_active_many(cfg, {"craftacct": a, "streetacct": b})
    personas = Personas.load(cfg).all()
    data = {a: {"terms": ["drillscene"], "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"},
            b: {"terms": ["voxpop"], "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"}}
    for per in personas:
        data[per.id]["input_fp"] = hv._input_fingerprint(per, siblings=personas, data=data)
    hv.write_vocab(cfg, data)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")

    def _boom(*a, **k):
        raise AssertionError("current sibling-aware fingerprints must skip the LLM")

    monkeypatch.setattr(hv, "claude_json", _boom)
    r = hv.expand_vocab_if_due(cfg)
    assert r == {"refreshed": False, "reason": "fresh", "personas": 2}


def test_expand_if_due_skips_unchanged_persona(tmp_path, monkeypatch):
    """MOL-693: no routine regeneration — an untouched persona costs ZERO LLM calls on later ticks."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["undergroundmusic"]); _link_active(cfg, pid)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    fake, seen = _replying(["drillscene", "syrianrap"])
    monkeypatch.setattr(hv, "claude_json", fake)

    first = hv.expand_vocab_if_due(cfg)
    assert first.get("refreshed") is True and first.get("ok") == 1
    assert len(seen) == 1
    row = hv.load_vocab(cfg).get(pid)
    assert row.get("input_fp") and row.get("terms") == ["drillscene", "syrianrap"]

    for _ in range(2):                                               # consecutive ticks, no input change
        r = hv.expand_vocab_if_due(cfg)
        assert r.get("refreshed") is False
    assert len(seen) == 1                                            # the LLM was never asked again
    assert hv.load_vocab(cfg).get(pid) == row                        # durable row untouched (incl. expanded_at)


def test_niche_edit_reexpands_owner_and_sibling_once(tmp_path, monkeypatch):
    """MOL-716: a changed niche re-expands its owner and sibling, then both become current."""
    cfg = Config(root=tmp_path)
    a = _persona(cfg, pid="craft", niche=["undergroundmusic"])
    b = _persona(cfg, pid="street", niche=["streetinterview"])
    _link_active_many(cfg, {"craftacct": a, "streetacct": b})
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")

    seen: list[str] = []; calls = {"craft": 0, "street": 0}
    def _fake(prompt, schema, **kw):
        seen.append(prompt)
        pid = "craft" if "Persona name: craft" in prompt else "street"
        calls[pid] += 1
        if pid == "craft":
            return {"terms": ["drillscene"]}
        return {"terms": ["voxpop" if calls[pid] == 1 else "streetculture"]}

    monkeypatch.setattr(hv, "claude_json", _fake)
    assert hv.expand_vocab_if_due(cfg).get("ok") == 2
    assert len(seen) == 2
    before_b = hv.load_vocab(cfg).get(b)
    seen.clear()

    from fanops.personas import update_persona
    update_persona(cfg, a, niche=["undergroundmusic", "basementshow"])
    r = hv.expand_vocab_if_due(cfg)
    assert r.get("refreshed") is True and r.get("ok") == 2 and r.get("fail") == 0
    assert len(seen) == 2 and any("basementshow" in prompt for prompt in seen)
    assert hv.load_vocab(cfg).get(b, {}).get("terms") == ["streetculture"]  # sibling output really moved
    assert hv.load_vocab(cfg).get(b) != before_b                      # sibling re-stamped for A's new territory
    seen.clear()

    assert hv.expand_vocab_if_due(cfg).get("refreshed") is False      # the edit is now absorbed
    assert seen == []


def test_failed_expand_retains_vocab_and_stays_due(tmp_path, monkeypatch):
    """MOL-693: an LLM error or an all-junk reply keeps the PRIOR terms AND the prior fingerprint, so the
    persona is retried on the very next tick instead of waiting out a timer."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["undergroundmusic"]); _link_active(cfg, pid)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    good_reply, _ = _replying(["drillscene"])
    monkeypatch.setattr(hv, "claude_json", good_reply)
    assert hv.expand_vocab_if_due(cfg).get("ok") == 1
    good = hv.load_vocab(cfg).get(pid)

    from fanops.personas import update_persona
    update_persona(cfg, pid, niche=["basementshow"])

    def _boom(prompt, schema, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(hv, "claude_json", _boom)
    r = hv.expand_vocab_if_due(cfg)
    assert r.get("refreshed") is True and r.get("ok") == 0 and r.get("fail") == 1
    assert hv.load_vocab(cfg).get(pid) == good                        # prior terms + prior fp preserved

    junk, _ = _replying(["#fyp", "viral", "love"])                    # sanitize failure = same contract
    monkeypatch.setattr(hv, "claude_json", junk)
    assert hv.expand_vocab_if_due(cfg).get("fail") == 1
    assert hv.load_vocab(cfg).get(pid) == good

    recovered, seen = _replying(["basementshow"])
    monkeypatch.setattr(hv, "claude_json", recovered)
    assert hv.expand_vocab_if_due(cfg).get("ok") == 1                 # still due -> retried, no timer wait
    assert len(seen) == 1
    row = hv.load_vocab(cfg).get(pid)
    assert row.get("terms") == ["basementshow"] and row.get("input_fp") != good.get("input_fp")
    assert hv.expand_vocab_if_due(cfg).get("refreshed") is False


def test_legacy_row_without_fingerprint_expands_once_then_stamps(tmp_path, monkeypatch):
    """MOL-693: a pre-MOL-693 row carries no input_fp — expand it ONCE to stamp one, then never again."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["undergroundmusic"]); _link_active(cfg, pid)
    hv.write_vocab(cfg, {pid: {"terms": ["syrianrap"], "expanded_at": "2026-07-28T00:00:00+00:00",
                               "source": "llm"}})
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    fake, seen = _replying(["drillscene"])
    monkeypatch.setattr(hv, "claude_json", fake)

    assert hv.expand_vocab_if_due(cfg).get("ok") == 1
    assert len(seen) == 1
    assert hv.load_vocab(cfg).get(pid, {}).get("input_fp")
    assert hv.expand_vocab_if_due(cfg).get("refreshed") is False
    assert len(seen) == 1


def test_vocab_seeds_are_not_layer_a_anchors(tmp_path, monkeypatch):
    """MOL-719: durable vocab is no longer searched, so an unreferenced seed is never even measured, while
    co-tags harvested from a NICHE root still admit on inbound evidence (MOL-643 unchanged)."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    hv.write_vocab(cfg, {pid: {"terms": ["syrianrap"], "expanded_at": "2026-07-28T00:00:00+00:00",
                               "source": "llm"}})
    media = {
        "#hiphop": [{"caption": "x #barsoverbeats #nashville", "like_count": 10, "play_count": 100}],
        "#syrianrap": [{"caption": "home #barsoverbeats", "like_count": 20, "play_count": 200}],
        "#nashville": [{"caption": "country only", "like_count": 5000, "play_count": 90000}],
        "#barsoverbeats": [{"caption": "craft #hiphop", "like_count": 80, "play_count": 8000},
                           {"caption": "again #hiphop", "like_count": 70, "play_count": 7000}],
    }
    refresh_store(cfg, scrape_client=_FakeClient(
        media_by_tag=media,
        media_count_by_tag={"#hiphop": 4_000_000, "#syrianrap": 50_000, "#nashville": 9_000_000,
                            "#barsoverbeats": 8_000}))
    m = load_measurements(cfg)
    assert "#syrianrap" not in m                                     # never a root -> never fetched
    assert "#nashville" not in m                                     # outbound-only megatag evicted
    assert m.get("#barsoverbeats", {}).get("from", {}).get("#hiphop", 0) >= 2
    per = Personas.load(cfg).get(pid)
    pool = {t for t, _, _ in _aligned_pool(per, m, cfg=cfg)}
    assert "#barsoverbeats" in pool                                  # niche-rooted harvest still admits
    assert "#nashville" not in pool
