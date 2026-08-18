# tests/test_persona_corpus.py
# The per-persona hashtag CORPUS drives selection. A persona's DERIVED corpus reaches the caption path:
# it JOINS the membership (so a corpus tag whose cache entry has since expired still survives) and LEADS
# the metric order for that persona's accounts. vet_hashtags(corpus=...) is the deterministic gate;
# request_captions carries each surface's corpus to ingest + the prompt; the account hydrates its corpus
# from the linked persona. corpus=None/empty -> byte-identical.
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem)
from fanops.accounts import Accounts, Account
from fanops import personas as core
from fanops.hashtags import vet_hashtags
from fanops.prompts import caption_prompt
from fanops.agentstep import response_path, request_path, latest_request_id
from fanops.caption import request_captions, ingest_captions
from fanops.source_tags import source_tag_locks_path

STORE = ["#hiphop", "#rap"]        # the measurement cache as an ordered menu


# --- vet_hashtags(corpus=...) — the deterministic gate -----------------------------------------

def test_corpus_tag_outside_the_cache_survives_and_leads():
    # A corpus tag the measurement cache no longer carries must survive AND lead: the corpus JOINS the
    # membership, so a cache entry expiring between derivation and selection cannot silently drop it.
    out = vet_hashtags(["#hiphop"], Platform.instagram, "en", store=STORE, corpus=["#detroitrap"])
    assert out[0] == "#detroitrap"
    assert "#hiphop" in out


def test_empty_corpus_is_byte_identical():
    base = vet_hashtags(["#hiphop", "#rap"], Platform.tiktok, "en", store=STORE)
    assert vet_hashtags(["#hiphop", "#rap"], Platform.tiktok, "en", store=STORE, corpus=[]) == base
    assert vet_hashtags(["#hiphop", "#rap"], Platform.tiktok, "en", store=STORE, corpus=None) == base


def test_corpus_with_non_str_entry_is_dropped_not_crashed():
    # Investigation-2 D6 (audit feared a crash, proven fail-open BY CONSTRUCTION): a hand-edited
    # personas.json could in principle carry a non-str in hashtag_corpus. vet_hashtags isinstance-guards
    # every corpus entry (n = _norm(t) if isinstance(t, str) else "") so a non-str is DROPPED, never raised.
    # Pinned so a future refactor that removes the guard can't reintroduce a Personas-page crash. (NB the
    # Persona model also validates hashtag_corpus: list[str], so this is the second line of defense.)
    out = vet_hashtags(["#hiphop"], Platform.instagram, "en", store=STORE,
                       corpus=["#detroitrap", 123, None, "#rap"])
    assert "#detroitrap" in out and "#hiphop" in out      # valid tags kept
    assert all(isinstance(t, str) for t in out)            # no non-str leaked into the result; no exception


def test_persona_facts_failopen_on_weird_corpus(tmp_path):
    # D6 end-to-end: persona_facts is the Personas-page transparency read. Even a duck-typed object whose
    # corpus holds a non-str must NOT crash the read (vet_hashtags drops it). Pins the page's fail-open.
    from types import SimpleNamespace
    cfg = Config(root=tmp_path)
    p = SimpleNamespace(clip_profile=None, framing="top", hashtag_corpus=["#detroitrap", 7])
    facts = core.persona_facts(cfg, p)                      # must return cleanly, not raise
    assert facts["framing"] == "top" and isinstance(facts["lead_tags"], list)
    assert "#detroitrap" in facts["lead_tags"]


def test_corpus_hard_capped_at_4():
    out = vet_hashtags([], Platform.instagram, "en", corpus=["#a", "#b", "#c", "#d", "#e", "#f"])
    assert len(out) == 4 and out == ["#a", "#b", "#c", "#d"]


def test_corpus_does_not_starve_arabic_floor():
    # A 4-tag non-Arabic corpus on an AR clip must not displace the AR region floor: the
    # floor still injects an AR tag (one corpus tag yields), so curated tags never strip AR reach.
    out = vet_hashtags([], Platform.instagram, "ar", corpus=["#x", "#y", "#z", "#w"])
    assert len(out) == 4 and any(t in {"#arabicmusic", "#arabtiktok", "#arabicmusiclovers"} for t in out)


def test_corpus_normalizes_and_dedupes_model_picks():
    # a model tag equal (after norm) to a corpus tag must not double-count; corpus order wins.
    out = vet_hashtags(["DetroitRap"], Platform.instagram, "en", corpus=["#detroitrap", "#flintbars"])
    assert out[0] == "#detroitrap" and out.count("#detroitrap") == 1 and "#flintbars" in out


# --- Account hydrates its corpus from the linked persona ---------------------------------------

def test_account_hydrates_hashtag_corpus_from_persona(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", voice="v1", niche=["hiphop"])
    core.apply_auto_corpus(cfg, pid, tags=["#detroitrap"], meta={})
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    a = Accounts.load(cfg).accounts[0]
    assert a.hashtag_corpus == ["#detroitrap"]


def test_unlinked_account_corpus_is_empty(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram"], "status": "active"}]}))
    assert Accounts.load(cfg).accounts[0].hashtag_corpus == []


# --- caption request/ingest carry + apply the corpus -------------------------------------------

def _clip(led, transcript="they slept on me"):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt=transcript, state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))


def _write_meas_tags(cfg, tags, sizes=None):
    now = "2026-07-01T00:00:00+00:00"
    sizes = sizes or {}
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        t: {"graph_id": f"g{t}", "like_count": 100, "media_count": sizes.get(t, 1000.0),
            "measured_at": now} for t in tags
    }))


def _accounts_with_corpus(cfg, corpus):
    a = Accounts(cfg)
    a.accounts = [Account(handle="a", platforms=[Platform.instagram], hashtag_corpus=corpus)]
    return a


def _write_lock(cfg, sid, lock, pile=None):
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        sid: {"pile": list(pile or lock), "lock": list(lock), "researched_at": "2026-08-17T00:00:00Z"},
    }))


def test_request_captions_carries_source_measured_lead_not_persona_corpus(tmp_path):
    """HV1-PR3: request carries no content_tags / no ASR corpus lead (lock only)."""
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _clip(led, transcript="detroit rap bars fire")
    _write_meas_tags(cfg, ["#detroit", "#rap", "#bars", "#fire", "#alphacorpus"],
                     {"#detroit": 5000.0, "#alphacorpus": 10.0})
    accts = _accounts_with_corpus(cfg, ["#alphacorpus", "#betacorpus", "#gammacorpus"])
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "content_tags" not in payload
    assert "corpus" not in payload["surfaces"][0]
    assert accts.accounts[0].hashtag_corpus == ["#alphacorpus", "#betacorpus", "#gammacorpus"]


def test_request_captions_omits_corpus_when_no_measured_overlap(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    accts = _accounts_with_corpus(cfg, ["#detroitrap"])
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "corpus" not in payload["surfaces"][0]          # cold cache / no overlap -> no key


def test_ingest_uses_source_corpus_lead_not_persona_monopoly(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _clip(led, transcript="detroit rap bars fire")
    _write_meas_tags(cfg, ["#detroit", "#rap", "#bars", "#fire", "#alphacorpus"],
                     {"#detroit": 9000.0, "#alphacorpus": 5.0})
    accts = _accounts_with_corpus(cfg, ["#alphacorpus", "#betacorpus"])
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and c.state is ClipState.held
    assert "caption_missing_language" in (c.held_reason or "")
    assert "a/instagram" not in c.meta_captions


# --- the prompt surfaces the corpus rule -------------------------------------------------------

def test_caption_prompt_has_no_corpus_preference_rule():
    payload = {"language": "en", "surfaces": [{"surface": "a/instagram", "platform": "instagram",
                                               "corpus": ["#detroitrap"]}]}
    out = caption_prompt(payload)
    assert "prefer the tags in that surface's `corpus`" not in out.lower()
    assert "UNION" not in out


# --- MOL-513 (C-3): per-surface hashtag_store = persona aligned pool -------------------------

def _write_meas(cfg, rows):
    """rows: {tag: (like_count, from_anchor_or_None)} — fresh evidence for _aligned_pool."""
    now = datetime.now(timezone.utc).isoformat()
    data = {}
    for tag, (metric, src) in rows.items():
        rec = {"graph_id": "id-" + tag.lstrip("#"), "like_count": metric, "measured_at": now,
               "media_count": 50_000.0}  # MOL-714: non-niche needs volume floor
        if src:
            rec["from"] = {src: 2}  # MOL-665: relatedness bar needs hits>=2
        data[tag] = rec
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps(data))


def test_request_captions_carries_per_surface_aligned_store(tmp_path):
    # HV1-PR3: both surfaces of a source get the SAME lock, not per-persona aligned pools.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    lock = ["#lockone", "#locktwo"]
    _write_lock(cfg, "src_1", lock)
    pid_a = core.add_persona(cfg, name="Hip", voice="va", niche=["hiphop"], id="pa")
    pid_b = core.add_persona(cfg, name="Pod", voice="vb", niche=["podcast"], id="pb")
    _write_meas(cfg, {
        "#hiphop": (100, None),
        "#detroitrap": (900, "#hiphop"),
        "#podcast": (200, None),
        "#interview": (800, "#podcast"),
        "#globalwinner": (9999, None),
    })
    a = Accounts(cfg)
    a.accounts = [
        Account(handle="a", platforms=[Platform.instagram], persona_id=pid_a),
        Account(handle="b", platforms=[Platform.instagram], persona_id=pid_b),
    ]
    request_captions(led, cfg, "clip_1",
                     [("a", Platform.instagram), ("b", Platform.instagram)], accounts=a)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "hashtag_store" not in payload                    # root key gone (MOL-513)
    by = {s["surface"]: s for s in payload["surfaces"]}
    sa, sb = by["a/instagram"]["hashtag_store"], by["b/instagram"]["hashtag_store"]
    assert sa == lock and sb == lock
    assert "corpus" not in by["a/instagram"] and "content_tags" not in payload
    prompt = caption_prompt(payload)
    a_rule = prompt.split('For surface "a/instagram"', 1)[1].split('For surface "b/instagram"', 1)[0]
    assert "#lockone" in a_rule and "#locktwo" in a_rule


def test_request_captions_omits_hashtag_store_when_no_aligned_pool(tmp_path):
    import inspect
    from fanops.caption import request_captions as req_fn
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    # No sidecar lock -> empty/absent store. Never fail-open to the 80-pile.
    accts = _accounts_with_corpus(cfg, [])
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "hashtag_store" not in payload
    assert "hashtag_store" not in payload["surfaces"][0]
    assert "_per_account_hashtag_stores" not in inspect.getsource(req_fn)


# --- MOL-511 (C-1): ingest consumes the per-surface hashtag_store written by C-3 -----------------

def test_ingest_uses_request_hashtag_store_not_global_cache(tmp_path):
    # HV1-PR3: request writes the source lock on every surface; ingest vets that lock.
    # Off-lock tags die on both surfaces even when they sit in the global cache.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    lock = ["#detroitrap"]
    _write_lock(cfg, "src_1", lock)
    _write_meas(cfg, {
        "#hiphop": (100, None),
        "#detroitrap": (900, "#hiphop"),
        "#podcast": (200, None),
        "#interview": (800, "#podcast"),
        "#globalwinner": (9999, None),
    })
    a = Accounts(cfg)
    a.accounts = [
        Account(handle="a", platforms=[Platform.instagram]),
        Account(handle="b", platforms=[Platform.instagram]),
    ]
    request_captions(led, cfg, "clip_1",
                     [("a", Platform.instagram), ("b", Platform.instagram)], accounts=a)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x",
                    hashtags=["#detroitrap", "#interview", "#globalwinner"]),
        CaptionItem(surface="b/instagram", caption="x",
                    hashtags=["#detroitrap", "#interview", "#globalwinner"]),
    ]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    ta = led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"]
    tb = led.clips["clip_1"].meta_captions["b/instagram"]["hashtags"]
    assert ta == tb
    assert "#detroitrap" in ta
    assert "#interview" not in ta and "#globalwinner" not in ta


# --- MOL-512 (C-2): persona_facts lead_tags use this persona's aligned pool --------------------

def test_persona_facts_lead_tags_use_aligned_pool_not_global(tmp_path):
    """lead_tags must not surface a foreign persona's / unaligned global-cache winner.
    Same cache shape as C-3: hiphop-aligned vs podcast-aligned vs unaligned #globalwinner."""
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Hip", voice="va", niche=["hiphop"], id="pa")
    _write_meas(cfg, {
        "#hiphop": (100, None),
        "#detroitrap": (900, "#hiphop"),
        "#podcast": (200, None),
        "#interview": (800, "#podcast"),
        "#globalwinner": (9999, None),
    })
    per = core.Personas.load(cfg).get(pid)
    facts = core.persona_facts(cfg, per)
    lead = facts["lead_tags"]
    assert "#detroitrap" in lead or "#hiphop" in lead
    assert "#interview" not in lead and "#podcast" not in lead and "#globalwinner" not in lead


# --- HV1-PR3: caption menu is the source lock ---------------------------------------------------

def test_request_captions_lock_is_same_on_every_surface(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    lock = ["#alpha", "#beta", "#gamma"]
    _write_lock(cfg, "src_1", lock)
    _write_meas_tags(cfg, lock, {"#alpha": 100.0})
    accts = Accounts(cfg)
    accts.accounts = [
        Account(handle="a", platforms=[Platform.instagram]),
        Account(handle="b", platforms=[Platform.tiktok]),
    ]
    request_captions(led, cfg, "clip_1",
                     [("a", Platform.instagram), ("b", Platform.tiktok)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "content_tags" not in payload
    assert "corpus" not in payload
    for s in payload["surfaces"]:
        assert s["hashtag_store"] == lock
        assert "corpus" not in s
    assert set(payload.get("hashtag_metrics", {})).issubset(set(lock))


def test_request_without_lock_drops_invented_tags_sentence_stays(tmp_path):
    import inspect
    from fanops.caption import request_captions as req_fn
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "hashtag_store" not in payload["surfaces"][0]
    assert "_per_account_hashtag_stores" not in inspect.getsource(req_fn)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="they slept. not anymore.",
                    hashtags=["#invented", "#music"]),
    ]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is False
    mc = c.meta_captions["a/instagram"]
    assert mc["caption"] == "they slept. not anymore."
    assert mc["hashtags"] == []


def test_request_without_lock_tags_only_still_holds(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led)
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="#invented #music",
                    hashtags=["#invented", "#music"]),
    ]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and c.state is ClipState.held
    assert "caption_tags_only" in (c.held_reason or "")
