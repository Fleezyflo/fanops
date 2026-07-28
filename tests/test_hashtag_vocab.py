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
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    return Accounts.load(cfg)


def test_persona_terms_merges_durable_vocab_after_niche(tmp_path):
    """MOL-644: niche first, then durable LLM vocab seeds — corpus-blind, voice still ignored."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"], voice="believe in yourself punchlines")
    hv.write_vocab(cfg, {pid: {"terms": ["syrianrap", "barsoverbeats", "Believe", "#fyp", ""],
                                "expanded_at": "2026-07-28T00:00:00+00:00", "source": "llm"}})
    per = Personas.load(cfg).get(pid)
    assert persona_terms(per) == ["hiphop"]                          # no cfg → niche only (MOL-637)
    terms = persona_terms(per, cfg)
    assert terms[:1] == ["hiphop"]
    assert "syrianrap" in terms and "barsoverbeats" in terms
    assert "believe" not in terms and "punchlines" not in terms      # voice still never seeds
    assert "fyp" not in terms                                        # platform junk filtered at write+read


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


def test_expand_if_due_noop_when_responder_manual(tmp_path, monkeypatch):
    """Fail-open: without FANOPS_RESPONDER=llm, vocab expand is a no-op (no LLM call)."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg); _link_active(cfg, pid)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    called = []

    def _boom(*a, **k):
        called.append(1); raise AssertionError("claude must not run in manual mode")

    monkeypatch.setattr(hv, "claude_json", _boom)
    r = hv.expand_vocab_if_due(cfg)
    assert r.get("refreshed") is False
    assert r.get("reason") in ("responder_manual", "manual", "not_llm")
    assert called == []
    assert not cfg.hashtag_vocab_path.exists()


def test_vocab_anchor_admits_inbound_only(tmp_path, monkeypatch):
    """LLM vocab seeds become Layer A anchors; outbound co-tags still do not admit (MOL-643)."""
    cfg = Config(root=tmp_path)
    pid = _persona(cfg, niche=["hiphop"]); _link_active(cfg, pid)
    hv.write_vocab(cfg, {pid: {"terms": ["syrianrap"], "expanded_at": "2026-07-28T00:00:00+00:00",
                               "source": "llm"}})
    media = {
        "#hiphop": [{"caption": "x #nashville", "like_count": 10, "play_count": 100}],
        "#syrianrap": [{"caption": "home #barsoverbeats", "like_count": 20, "play_count": 200}],
        "#nashville": [{"caption": "country only", "like_count": 5000, "play_count": 90000}],
        "#barsoverbeats": [{"caption": "craft #syrianrap", "like_count": 80, "play_count": 8000},
                         {"caption": "again #syrianrap", "like_count": 70, "play_count": 7000}],
    }
    refresh_store(cfg, scrape_client=_FakeClient(media_by_tag=media))
    m = load_measurements(cfg)
    assert "#syrianrap" in m
    assert "#nashville" not in m                                     # outbound-only megatag evicted
    assert m.get("#barsoverbeats", {}).get("from", {}).get("#syrianrap", 0) >= 1
    per = Personas.load(cfg).get(pid)
    pool = {t for t, _, _ in _aligned_pool(per, m, cfg=cfg)}
    assert "#syrianrap" in pool and "#barsoverbeats" in pool
    assert "#nashville" not in pool
