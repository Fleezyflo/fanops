# Minimal baked-archetype persona fixtures for P15 proofs (MOL-175 may land separately).
import json
from fanops.config import Config
from fanops.accounts import Accounts, link_persona
from fanops import personas as P


def ensure_archetype_personas(cfg: Config) -> Accounts:
    """Two divergent archetypes + a third account for negative crosspost assertions."""
    cfg.control.mkdir(parents=True, exist_ok=True)
    if not cfg.personas_path.exists():
        specs = (
            ("Credibility First", "restraint is the product; pass on the sensational cut",
             "credibility_first", ["emotional", "storytelling"], "curiosity", ["#podcast", "#facts"]),
            ("Controversy", "pick the cut that starts the argument",
             "controversy_seeking", ["bold-statement"], "challenge", ["#drama", "#popculture"]),
        )
        for name, voice, scope, focus, angle, corpus in specs:
            pid = P.add_persona(cfg, name=name, voice=voice, selection_scope=scope,
                                content_focus=focus, hook_angle=angle)
            for tag in corpus:
                P.add_corpus_tag(cfg, pid, tag)
    pids = {p.name: p.id for p in P.Personas.load(cfg).personas}
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.accounts_path.exists():
        cfg.accounts_path.write_text(json.dumps({"accounts": [
            {"handle": "@trust", "account_id": "1", "platforms": ["instagram"], "status": "active"},
            {"handle": "@drama", "account_id": "2", "platforms": ["instagram"], "status": "active"},
            {"handle": "@extra", "account_id": "3", "platforms": ["instagram"], "status": "active"},
        ]}))
    link_persona(cfg, "@trust", pids["Credibility First"])
    link_persona(cfg, "@drama", pids["Controversy"])
    return Accounts.load(cfg)
