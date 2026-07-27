# tests/test_personas.py
# A1 — Personas become a FIRST-CLASS entity. Today a "persona" is only a free-text Account.persona
# string + tag_lean, seeded by hand from a brief doc. This makes a Persona a named, reusable, editable
# record in 00_control/personas.json that accounts LINK to via Account.persona_id; the linked persona's
# voice/tag_lean HYDRATE the account in memory at load so every existing consumer (caption/moments/
# casting/variant_transfer) stays byte-identical while persona edits take effect on the next load.
import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts, link_persona, set_clip_profile
from fanops import personas as P


def _write_accounts(cfg, rows):
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


# --- registry CRUD -----------------------------------------------------------------------------

def test_add_and_load_persona(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Music Blogger", voice="champions craft")
    p = P.Personas.load(cfg).get(pid)
    assert p is not None
    assert p.voice == "champions craft"
    assert p.name == "Music Blogger"


def test_add_persona_rejects_duplicate(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Dupe")
    with pytest.raises(ValueError):
        P.add_persona(cfg, name="Dupe")


def test_add_persona_requires_name(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        P.add_persona(cfg, name="   ")


def test_update_persona_fields(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Z", voice="old")
    P.update_persona(cfg, pid, voice="new")
    p = P.Personas.load(cfg).get(pid)
    assert p.voice == "new"


def test_update_unknown_persona_raises(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(KeyError):
        P.update_persona(cfg, "ghost", voice="x")


# --- niche (A-10): declared terms, per-entry validated at BOTH writers ---------------------------

def test_persona_round_trips_a_niche_normalized(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Nicher", niche=["hiphop", " #ArabicRap ", "hiphop"])
    assert P.Personas.load(cfg).get(pid).niche == ["hiphop", "arabicrap"]   # stripped, de-hashed, lowered, deduped
    P.update_persona(cfg, pid, niche=["syria"])
    assert P.Personas.load(cfg).get(pid).niche == ["syria"]


def test_persona_without_a_niche_defaults_empty(tmp_path):
    # TRANSITIONAL: empty is accepted only until A-13 refuses it (the 3 live rows have none yet).
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="No Niche")
    assert P.Personas.load(cfg).get(pid).niche == []


def test_add_persona_refuses_a_defective_niche_entry(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError, match="keysmash"):
        P.add_persona(cfg, name="Bad", niche=["fypppppppppp"])
    assert P.Personas.load(cfg).get("bad") is None   # refused BEFORE the lock — nothing was written


def test_update_persona_refuses_a_defective_niche_entry(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Good", niche=["hiphop"])
    with pytest.raises(ValueError, match="keysmash"):
        P.update_persona(cfg, pid, niche=["fypppppppppp"])
    assert P.Personas.load(cfg).get(pid).niche == ["hiphop"]   # the refusal left the stored value intact


def test_apply_auto_corpus_normalizes_dedupes_and_REPLACES(tmp_path):
    # The corpus is a DERIVED value, so the writer replaces it wholesale: no pin partition, no merge, no
    # absent-meta-means-pinned rule. Those protected hand-curated entries and, in doing so, froze rotation.
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Z")
    P.apply_auto_corpus(cfg, pid, tags=["DetroitRap", "#detroitrap", 7, "", "#flintbars"], meta={})
    assert P.Personas.load(cfg).get(pid).hashtag_corpus == ["#detroitrap", "#flintbars"]
    P.apply_auto_corpus(cfg, pid, tags=["#newone"], meta={})
    assert P.Personas.load(cfg).get(pid).hashtag_corpus == ["#newone"]   # REPLACED, not merged


def test_apply_auto_corpus_unknown_persona_raises(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(KeyError):
        P.apply_auto_corpus(cfg, "ghost", tags=["#x"], meta={})


def test_deprecate_legacy_corpus_moves_unprovenanced_tags_and_is_idempotent(tmp_path):
    # The cutover: a corpus tag with no DERIVATION meta has no platform evidence behind it, so it is
    # retired somewhere the operator can still see it — never silently dropped, never left shipping.
    import json
    from fanops.hashtags import METRIC_FIELD
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Z")
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d["id"] == pid:
            d["hashtag_corpus"] = ["#derived", "#legacypin", "#nometa"]
            d["hashtag_corpus_meta"] = {
                "#derived": {METRIC_FIELD: 900.0, "measured_at": "2026-07-20T00:00:00+00:00"},
                "#legacypin": {"source": "pinned", "reach": None, "added": "20260716T130424Z"}}
    cfg.personas_path.write_text(json.dumps(raw))
    assert set(P.deprecate_legacy_corpus(cfg, pid)) == {"#legacypin", "#nometa"}
    per = P.Personas.load(cfg).get(pid)
    assert per.hashtag_corpus == ["#derived"]                     # only the evidence-backed tag survives
    assert set(per.hashtag_corpus_deprecated) == {"#legacypin", "#nometa"}
    assert P.deprecate_legacy_corpus(cfg, pid) == []              # idempotent


def test_delete_persona(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Gone")
    P.delete_persona(cfg, pid)
    assert P.Personas.load(cfg).get(pid) is None


# --- account link + load-time hydration --------------------------------------------------------

def test_link_persona_sets_account_field(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    pid = P.add_persona(cfg, name="P1", voice="voice-1")
    link_persona(cfg, "@a", pid)
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["persona_id"] == pid


def test_link_unknown_account_raises(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(KeyError):
        link_persona(cfg, "@nope", "pid")


def test_load_hydrates_linked_account_from_persona(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="P1", voice="curator voice")
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "stale inline", "persona_id": pid}])
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "curator voice"    # the linked persona overrides the stale inline string


def test_load_failopen_when_personas_absent(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "inline voice", "persona_id": "ghost"}])
    a = Accounts.load(cfg).accounts[0]    # no personas.json + dangling id -> inline stands, no crash
    assert a.persona == "inline voice"


def test_load_unlinked_account_is_byte_identical(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Other", voice="other voice")   # a persona exists but this account isn't linked
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "my own voice"}])
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "my own voice" and a.persona_id is None


def test_unlinking_a_persona_leaves_no_stale_hydrated_state(tmp_path):
    # D3 (audit concern, proven NOT a defect): hydration is IN-MEMORY only — no writer persists a hydrated
    # field back to accounts.json (every writer mutates the raw dict; there is no Accounts.save). So
    # clearing a link must leave the account byte-identical to its raw inline values: the persona's voice/
    # tag_lean never leak into accounts.json, and the next load reads the inline persona again. This pins that
    # contract so a future hydrated-save path can't silently strand a stale hydrated value on unlink.
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="P1", voice="curator voice")
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "my own inline voice"}])
    link_persona(cfg, "@a", pid)
    linked = Accounts.load(cfg).accounts[0]
    assert linked.persona == "curator voice"   # hydrated in memory
    link_persona(cfg, "@a", "")                       # clear the link (blank -> persona_id None)
    raw = json.loads(cfg.accounts_path.read_text())["accounts"][0]
    assert raw.get("persona_id") is None and raw.get("persona") == "my own inline voice"   # no hydrated value persisted
    after = Accounts.load(cfg).accounts[0]
    assert after.persona == "my own inline voice"   # inline restored
    assert after.persona_id is None


# --- migration ---------------------------------------------------------------------------------

def test_migrate_from_accounts_creates_and_links(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [
        {"handle": "@mark", "platforms": ["instagram"], "status": "active",
         "persona": "music blogger curator"},
        {"handle": "@perca", "platforms": ["instagram"], "status": "active",
         "persona": "underground zine"},
    ])
    P.migrate_from_accounts(cfg)
    assert len(P.Personas.load(cfg).all()) == 2
    accts = Accounts.load(cfg)
    for a in accts.accounts:
        assert a.persona_id                       # every account with an inline persona is now linked
    # voice carried over via the link hydration
    by_handle = {a.handle: a for a in accts.accounts}
    assert by_handle["mark"].persona == "music blogger curator"
    # idempotent: a second run creates nothing new and re-links nothing
    P.migrate_from_accounts(cfg)
    assert len(P.Personas.load(cfg).all()) == 2


def test_migrate_preserves_inline_cut_spec(tmp_path):
    # D4 (audit concern, proven NOT a defect): migrate lifts only voice+tag_lean into the new Persona — the
    # ONLY fields hydration overwrites unconditionally (tag_lean at _hydrate line 222). An account's inline
    # clip_profile/framing are NOT carried, but they SURVIVE: hydration overrides them only when the persona
    # PINS them (conditional `if _prof`), and a freshly-migrated persona pins neither, so the account's own
    # cut spec stands. This pins that no-data-loss contract (a future unconditional clip_profile hydrate
    # would silently drop an operator's inline length on migrate).
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "hypewoman energy", "framing": "top"}])
    set_clip_profile(cfg, "@a", "long")               # operator hand-set an inline cut spec
    P.migrate_from_accounts(cfg)
    a = Accounts.load(cfg).accounts[0]                 # reloaded + hydrated (now linked)
    assert a.persona_id and a.persona == "hypewoman energy"   # voice carried
    assert a.clip_profile == "long" and a.framing == "top"   # inline cut spec NOT lost through migrate+hydrate


def test_migrate_skips_accounts_without_persona(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@blank", "platforms": ["instagram"], "status": "active"}])
    P.migrate_from_accounts(cfg)
    assert P.Personas.load(cfg).all() == []


def test_migrate_skips_unsluggable_handle(tmp_path):
    # A handle that slugs to "" must NOT be linked to an empty persona_id (a false "link to nothing").
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@@@", "platforms": ["instagram"], "status": "active",
                           "persona": "some voice"}])
    out = P.migrate_from_accounts(cfg)
    assert out["created"] == [] and out["linked"] == []
    assert P.Personas.load(cfg).all() == []
    assert Accounts.load(cfg).accounts[0].persona_id is None


def test_update_persona_rejects_blank_name(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Real")
    with pytest.raises(ValueError):
        P.update_persona(cfg, pid, name="   ")


def test_apply_auto_corpus_truncates_at_the_cap(tmp_path):
    # _CORPUS_CAP bounds a DERIVED list, so the surplus is truncated, not refused: a derivation must never
    # raise into the unattended run.
    from fanops.persona_store import _CORPUS_CAP
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Full")
    P.apply_auto_corpus(cfg, pid, tags=[f"#tag{i}" for i in range(_CORPUS_CAP + 10)], meta={})
    corpus = P.Personas.load(cfg).get(pid).hashtag_corpus
    assert len(corpus) == _CORPUS_CAP and corpus[0] == "#tag0"


# --- MOL-175: baked archetype personas (seed data) ---------------------------------------------

def test_baked_personas_load():
    baked = P.baked_personas()
    assert 3 <= len(baked) <= 5
    for p in baked:
        assert isinstance(p, P.Persona) and p.id and p.voice
        assert p.content_focus and p.hook_angle
        # a baked archetype ships its voice + levers, NEVER a corpus: hashtags are derived from platform
        # evidence, so a hand-written starter list would be exactly the unmeasured seeding this removed.
        assert p.hashtag_corpus == []


def test_each_baked_persona_coherent(tmp_path):
    cfg = Config(root=tmp_path)
    for p in P.baked_personas():
        rows = P.manifest(cfg, p)
        assert rows, f"manifest empty for {p.id}"
        assert all(r["health"] == "ok" for r in rows), {r["key"]: r["health"] for r in rows if r["health"] != "ok"}


def test_credibility_first_scope_reaches_pick(tmp_path):
    from fanops.accounts import Accounts
    from fanops.moments import _pick_personas
    cfg = Config(root=tmp_path)
    P.ensure_baked_personas(cfg)
    _write_accounts(cfg, [{"handle": "@trust", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    link_persona(cfg, "@trust", "credibility-first")
    specs = _pick_personas(cfg, Accounts.load(cfg))
    assert len(specs) == 1
    scope = specs[0]["selection_scope"].lower()
    assert "sensational" in scope or "accurate" in scope


def test_baked_personas_mappable_to_account(tmp_path):
    cfg = Config(root=tmp_path)
    added = P.ensure_baked_personas(cfg)
    assert added
    pid = added[0]
    baked = next(p for p in P.baked_personas() if p.id == pid)
    _write_accounts(cfg, [{"handle": "@map", "platforms": ["instagram"], "status": "active", "persona": "stale"}])
    link_persona(cfg, "@map", pid)
    a = Accounts.load(cfg).accounts[0]
    assert a.persona_id == pid and a.persona == baked.voice
    assert a.content_focus == baked.content_focus
    assert a.selection_scope == baked.selection_scope
    assert a.hook_angle == baked.hook_angle
    assert a.hashtag_corpus == baked.hashtag_corpus
