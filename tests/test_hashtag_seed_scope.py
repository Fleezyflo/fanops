# tests/test_hashtag_seed_scope.py
# R4b — the discovery store is the menu for the accounts that POST, so only personas linked to an ACTIVE
# account may seed it.
#
# Found while verifying the R4 migration on live data: the corpora were clean, but the rebuilt store STILL
# carried `#science` and `#gossip`. They came from five DORMANT personas (no linked account) whose
# `intake.genre` words `_seed_tags` fed into the harvest regardless. Post-#679 the model's picks reach the
# shipped line, so a stray `#science` on a Syrian rapper's clip was a live risk, not a cosmetic one.
# Patching the two offending genre strings would have left the class open; scoping the seed set closes it.
import json
from fanops.config import Config
from fanops import personas as P
from fanops.fanops_hashtags import _posting_persona_ids, _seed_tags


def _accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def test_dormant_persona_cannot_seed_the_store(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Live", id="live", intake={"genre": "rap"})
    P.add_corpus_tag(cfg, "live", "#bars")
    P.add_persona(cfg, name="Dormant", id="dormant", intake={"genre": "science"})
    P.add_corpus_tag(cfg, "dormant", "#podcast")
    _accounts(cfg, [{"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "live"}])
    seeds = _seed_tags(cfg)
    assert "#bars" in seeds and "#rap" in seeds                 # the posting persona seeds
    assert "#science" not in seeds, "a dormant persona's genre reached the discovery menu"
    assert "#podcast" not in seeds, "a dormant persona's corpus reached the discovery menu"


def test_inactive_account_does_not_make_a_persona_live(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Live", id="live", intake={"genre": "rap"})
    P.add_corpus_tag(cfg, "live", "#bars")
    P.add_persona(cfg, name="Paused", id="paused", intake={"genre": "gossip"})
    _accounts(cfg, [{"handle": "a", "platforms": ["instagram"], "status": "active", "persona_id": "live"},
                    {"handle": "b", "platforms": ["instagram"], "status": "planned", "persona_id": "paused"}])
    assert _posting_persona_ids(cfg) == {"live"}
    assert "#gossip" not in _seed_tags(cfg)


def test_no_accounts_file_falls_back_to_every_persona(tmp_path):
    # FAIL-OPEN: unknown liveness must not shrink the menu to nothing.
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="P1", id="p1", intake={"genre": "rap"})
    P.add_corpus_tag(cfg, "p1", "#bars")
    assert _posting_persona_ids(cfg) == set()                  # unknown, not "none"
    assert "#bars" in _seed_tags(cfg)


def test_no_active_persona_link_falls_back_rather_than_emptying(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="P1", id="p1")
    P.add_corpus_tag(cfg, "p1", "#bars")
    _accounts(cfg, [{"handle": "a", "platforms": ["instagram"], "status": "active"}])   # no persona_id
    assert _posting_persona_ids(cfg) == set()
    assert "#bars" in _seed_tags(cfg), "an unlinked active account emptied the seed set"


def test_corrupt_accounts_file_is_fail_open(tmp_path):
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="P1", id="p1")
    P.add_corpus_tag(cfg, "p1", "#bars")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text("{ not json")
    assert _posting_persona_ids(cfg) == set()                  # never raises
    assert "#bars" in _seed_tags(cfg)
