# tests/test_hashtag_migrate.py
# R4 migration — this rewrites LIVE BRAND DATA (00_control/personas.json), which is not repo data and
# cannot be corrected by a deploy. So the contract is narrow and non-negotiable: snapshot before any byte
# moves, converge on the DECLARED curated target, never invent a reach number, be idempotent, and report a
# miss loudly rather than a clean "0 changes" (it first did exactly that against a wrong root — silent
# success on a no-op is the same failure shape as the reach erasure this whole program is about).
import json
from pathlib import Path
from fanops.config import Config
from fanops import personas as P
from fanops.hashtag_hygiene import tag_defect
from fanops.hashtags import load_store_evidence

_FYP = "#fypppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"


def _persona(cfg, pid="p1", corpus=()):
    P.add_persona(cfg, name=pid, id=pid)
    if corpus:                                     # bypass the hygiene gate to model LEGACY polluted data
        raw = json.loads(cfg.personas_path.read_text())
        for d in raw["personas"]:
            if d["id"] == pid: d["hashtag_corpus"] = list(corpus)
        cfg.personas_path.write_text(json.dumps(raw))
    return cfg


def test_dry_run_writes_nothing(tmp_path):
    from fanops.hashtag_migrate import migrate_corpora
    cfg = _persona(Config(root=tmp_path), pid="craft-curator", corpus=["#taylorswift", "#80s", _FYP])
    before = cfg.personas_path.read_text()
    r = migrate_corpora(cfg, apply=False)
    assert r["changed"] and not r["backups"]
    assert cfg.personas_path.read_text() == before, "a dry run touched live brand data"


def test_snapshots_then_cleans_and_is_idempotent(tmp_path):
    from fanops.hashtag_migrate import migrate_corpora, CURATED
    polluted = ["#taylorswift", "#80s", "#instagood", _FYP, "#wutang", "#lyrics"]
    cfg = _persona(Config(root=tmp_path), pid="craft-curator", corpus=polluted)
    r = migrate_corpora(cfg, apply=True)
    assert r["backups"], "live brand data was modified with no rollback snapshot"
    assert json.loads(Path(r["backups"][0]).read_text())["personas"][0]["hashtag_corpus"] == polluted
    after = P.Personas.load(cfg).get("craft-curator").hashtag_corpus
    assert after == CURATED["craft-curator"]
    for junk in ("#taylorswift", "#80s", "#instagood", _FYP, "#wutang"):
        assert junk not in after
    assert all(tag_defect(t) is None for t in after)
    again = migrate_corpora(cfg, apply=True)              # convergent, not a state machine
    assert again["changed"] == 0 and not again["backups"]


def test_curated_tags_are_marked_pinned(tmp_path):
    # pinned is what makes the corpus human-governed: _partition_corpus/_is_pinned stops the S12 auto
    # refresh reclaiming the slot. Without it the migration's own output would be auto-rewritable.
    from fanops.hashtag_migrate import migrate_corpora
    cfg = _persona(Config(root=tmp_path), pid="burner-bold", corpus=["#viral", _FYP])
    migrate_corpora(cfg, apply=True)
    meta = json.loads(cfg.personas_path.read_text())["personas"][0]["hashtag_corpus_meta"]
    assert meta and all(m["source"] == "pinned" for m in meta.values())
    assert all(m["reach"] is None for m in meta.values()), "the migration invented a reach number"


def test_never_fabricates_reach(tmp_path):
    from fanops.hashtag_migrate import migrate_corpora
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a", "#b"], "reach": {"#a": 1200}}))
    migrate_corpora(cfg, apply=True)
    ev = load_store_evidence(cfg)
    assert ev["#a"]["reach"] == 1200.0                   # the number survives verbatim
    assert ev["#a"]["source"] == "unknown"               # provenance genuinely unknown — say so
    assert ev["#a"]["measured_at"] is None               # NOT back-dated into fake evidence
    assert "#b" not in ev, "reach was invented for a tag that was never measured"


def test_missing_control_files_is_reported_not_a_clean_zero(tmp_path):
    # pointed at a wrong root this returned "0 changes" and looked like success while touching nothing.
    from fanops.hashtag_migrate import migrate_corpora, cmd_hashtags_migrate
    cfg = Config(root=tmp_path / "nope")
    r = migrate_corpora(cfg, apply=False)
    assert r["missing"], "a migration that cannot find its data must say so"
    assert cmd_hashtags_migrate(cfg, apply=False) == 2, "nothing-to-migrate must not exit 0"
