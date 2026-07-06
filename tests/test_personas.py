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


def test_corpus_add_remove_normalized_and_deduped(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Z")
    P.add_corpus_tag(cfg, pid, "DetroitRap")        # no '#', mixed case
    P.add_corpus_tag(cfg, pid, "#detroitrap")        # duplicate after normalization
    assert P.Personas.load(cfg).get(pid).hashtag_corpus == ["#detroitrap"]
    P.remove_corpus_tag(cfg, pid, "#DetroitRap")     # remove is normalization-insensitive
    assert P.Personas.load(cfg).get(pid).hashtag_corpus == []


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


def test_add_corpus_tag_raises_when_full_but_existing_is_noop(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Full")
    for i in range(40):                              # fill to _CORPUS_CAP
        P.add_corpus_tag(cfg, pid, f"#tag{i}")
    assert len(P.Personas.load(cfg).get(pid).hashtag_corpus) == 40
    with pytest.raises(ValueError):                  # a NEW tag past the cap is refused, not silently dropped
        P.add_corpus_tag(cfg, pid, "#overflow")
    P.add_corpus_tag(cfg, pid, "#tag0")              # an already-present tag at cap is a clean no-op
    assert len(P.Personas.load(cfg).get(pid).hashtag_corpus) == 40
