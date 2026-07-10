# tests/test_persona_link_accounts.py
# Ticket mol-persona-link-accounts-2c21: verify all active accounts can link to personas
# via the existing paths and that hydration works end-to-end.
#
# Code-gap verdict: NO gaps found. All three paths are present and correct:
#   - persona_store.migrate_from_accounts  (lift inline voice -> Persona + set persona_id)
#   - persona_store.link_personas_by_voice (link accounts whose voice EXACTLY matches an existing Persona)
#   - studio/personas.connect_account      (operator manual link in the Studio)
#   - accounts._hydrate_from_personas      (load-time: persona voice/corpus/levers -> account)
#
# These tests cover the acceptance criteria:
#   1. link_personas_by_voice standalone (voice-match to a PRE-EXISTING Persona)
#   2. migrate_from_accounts: 0 active accounts missing persona_id in the fixture
#   3. Each persona in the fixture has >= 1 linked account
#   4. Hydration carries voice + corpus + levers after migration
#   5. Mixed fixture: accounts with + without inline persona handle correctly
import json
from fanops.config import Config
from fanops.accounts import Accounts
from fanops import personas as P
from fanops.persona_store import link_personas_by_voice, migrate_from_accounts


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


# ---------------------------------------------------------------------------
# 1. link_personas_by_voice — accounts with inline voice matching an existing
#    Persona get linked without creating a new Persona
# ---------------------------------------------------------------------------

def test_link_personas_by_voice_links_matching_accounts(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="Hip-Hop Curator", voice="underground scene evangelist")
    P.add_corpus_tag(cfg, pid, "#hiphop")
    _write_accounts(cfg, [
        {"handle": "@hip",  "platforms": ["instagram"], "status": "active",
         "persona": "underground scene evangelist"},   # exact voice match -> should link
        {"handle": "@jazz", "platforms": ["instagram"], "status": "active",
         "persona": "jazz appreciation society"},      # no match -> stays unlinked
    ])
    linked = link_personas_by_voice(cfg)
    # handles are canonicalized (@ stripped) by validate_account_handle at load time
    assert "hip" in linked and "jazz" not in linked
    accts = Accounts.load(cfg)
    by_handle = {a.handle: a for a in accts.accounts}
    assert by_handle["hip"].persona_id == pid
    assert by_handle["jazz"].persona_id is None


def test_link_personas_by_voice_skips_already_linked(tmp_path):
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="P1", voice="exact match voice")
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "exact match voice", "persona_id": pid}])
    linked = link_personas_by_voice(cfg)
    assert linked == []          # already linked -> skipped (idempotent)


def test_link_personas_by_voice_requires_exact_match(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="P1", voice="underground scene evangelist")
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "Underground Scene Evangelist"}])   # different case -> no match
    linked = link_personas_by_voice(cfg)
    assert linked == []


# ---------------------------------------------------------------------------
# 2. migrate_from_accounts: ALL active accounts with inline personas get
#    persona_id — the "0 active accounts missing persona_id" criterion
# ---------------------------------------------------------------------------

def test_migrate_all_active_accounts_get_persona_id(tmp_path):
    """After migrate_from_accounts, every active account that had an inline persona
    carries a persona_id — no active account is left unlinked when it has a voice."""
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [
        {"handle": "@alpha",  "platforms": ["instagram"],          "status": "active",
         "persona": "the curious explorer"},
        {"handle": "@beta",   "platforms": ["tiktok"],             "status": "active",
         "persona": "the hype machine"},
        {"handle": "@gamma",  "platforms": ["instagram", "tiktok"],"status": "active",
         "persona": "credibility first sports journalist"},
    ])
    result = migrate_from_accounts(cfg)
    # handles are canonicalized (@ stripped) by validate_account_handle at load time
    assert set(result["created"]) == {"alpha", "beta", "gamma"}
    assert set(result["linked"])  == {"alpha", "beta", "gamma"}

    accts = Accounts.load(cfg)
    active = accts.active()
    missing = [a.handle for a in active if not (a.persona_id or "").strip()]
    assert missing == [], f"active accounts still missing persona_id: {missing}"


# ---------------------------------------------------------------------------
# 3. Each persona has >= 1 linked account after migration
# ---------------------------------------------------------------------------

def test_each_persona_has_at_least_one_linked_account(tmp_path):
    """After migration, no orphaned Persona exists — every Persona created by
    migrate_from_accounts is linked from at least one account."""
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [
        {"handle": "@one", "platforms": ["instagram"], "status": "active",
         "persona": "music blog voice"},
        {"handle": "@two", "platforms": ["instagram"], "status": "active",
         "persona": "underground zine voice"},
    ])
    migrate_from_accounts(cfg)

    all_personas = P.Personas.load(cfg).all()
    assert len(all_personas) == 2
    all_accts = Accounts.load(cfg).accounts
    linked_pids = {a.persona_id for a in all_accts if a.persona_id}
    for per in all_personas:
        assert per.id in linked_pids, f"persona {per.id!r} has no linked account"


# ---------------------------------------------------------------------------
# 4. Hydration carries voice, corpus, and levers after migration + corpus add
# ---------------------------------------------------------------------------

def test_hydration_carries_voice_corpus_levers_after_migration(tmp_path):
    """After migrate, the account hydrates the persona's voice, corpus, and levers
    (not just persona_id is set — the full hydration chain works)."""
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@curator", "platforms": ["instagram"],
                            "status": "active", "persona": "beat tape archivist"}])
    result = migrate_from_accounts(cfg)
    pid = result["created"][0]

    # Operator adds levers + corpus to the freshly-created persona
    P.update_persona(cfg, pid, content_focus=["storytelling"], hook_angle="emotional",
                     selection_scope="subject_locked")
    P.add_corpus_tag(cfg, pid, "#beatmaking")
    P.add_corpus_tag(cfg, pid, "#underground")

    a = Accounts.load(cfg).accounts[0]
    assert a.persona_id == pid
    assert a.persona == "beat tape archivist"           # voice from persona
    assert a.content_focus == ["storytelling"]          # levers hydrated
    assert a.hook_angle == "emotional"
    assert a.selection_scope == "subject_locked"
    assert "#beatmaking" in a.hashtag_corpus             # corpus hydrated
    assert "#underground" in a.hashtag_corpus
    assert a.clip_profile == "long"                      # derived from storytelling
    assert a.framing == "top"                            # derived from storytelling


# ---------------------------------------------------------------------------
# 5. Mixed fixture: accounts without inline persona are silently skipped;
#    accounts with inline persona are all linked; no crash
# ---------------------------------------------------------------------------

def test_migrate_mixed_fixture_no_persona_accounts_skipped(tmp_path):
    """Accounts without an inline persona are unaffected by migration.
    Accounts WITH a persona (active or not) all get linked; no-voice accounts stay unlinked."""
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [
        {"handle": "@with_voice", "platforms": ["instagram"], "status": "active",
         "persona": "hip hop commentator"},
        {"handle": "@no_voice",   "platforms": ["tiktok"],    "status": "active"},   # no persona
        {"handle": "@planned",    "platforms": ["instagram"], "status": "planned",
         "persona": "planned with voice"},                                            # not active but has voice
    ])
    result = migrate_from_accounts(cfg)
    # migrate iterates ALL accounts (not just active); handles are canonicalized (no @)
    assert set(result["linked"]) == {"with_voice", "planned"}

    accts = Accounts.load(cfg)
    by_handle = {a.handle: a for a in accts.accounts}
    assert (by_handle["with_voice"].persona_id or "").strip()       # linked
    assert not (by_handle["no_voice"].persona_id or "").strip()     # no voice -> stays unlinked
    assert (by_handle["planned"].persona_id or "").strip()          # planned but has voice -> linked

    # the acceptance criterion: 0 ACTIVE accounts with an inline persona are missing persona_id
    active_with_voice = [a for a in accts.active() if (a.persona or "").strip()]
    missing = [a.handle for a in active_with_voice if not (a.persona_id or "").strip()]
    assert missing == []


# ---------------------------------------------------------------------------
# 6. Idempotency: running migrate_from_accounts twice is safe
# ---------------------------------------------------------------------------

def test_migrate_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [
        {"handle": "@x", "platforms": ["instagram"], "status": "active", "persona": "voice x"},
        {"handle": "@y", "platforms": ["instagram"], "status": "active", "persona": "voice y"},
    ])
    first  = migrate_from_accounts(cfg)
    second = migrate_from_accounts(cfg)
    assert set(first["created"]) == {"x", "y"}
    assert second["created"] == [] and second["linked"] == []   # nothing new on re-run
    assert len(P.Personas.load(cfg).all()) == 2                 # no duplicates


# ---------------------------------------------------------------------------
# 7. migrate result keys (voice_linked, created, linked) are complete
# ---------------------------------------------------------------------------

def test_migrate_returns_all_result_keys(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Existing", voice="pre-existing voice")
    _write_accounts(cfg, [
        {"handle": "@a", "platforms": ["instagram"], "status": "active",
         "persona": "pre-existing voice"},        # voice_linked path (persona already exists)
        {"handle": "@b", "platforms": ["instagram"], "status": "active",
         "persona": "brand new voice"},           # created + linked path
    ])
    result = migrate_from_accounts(cfg)
    assert set(result.keys()) == {"created", "linked", "voice_linked"}
    # handles are canonicalized (@ stripped) — "a" not "@a"
    assert "a" in result["voice_linked"]          # matched via link_personas_by_voice
    assert "b" in result["created"]               # new persona created for @b
    assert "b" in result["linked"]
