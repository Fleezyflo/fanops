# Minimal baked-archetype persona fixtures for P15 proofs (MOL-175 may land separately).
import json
from fanops.config import Config
from fanops.accounts import Accounts, link_persona
from fanops import personas as P


def ensure_archetype_personas(cfg: Config) -> Accounts:
    """Two divergent archetypes + a third account for negative crosspost assertions."""
    cfg.control.mkdir(parents=True, exist_ok=True)
    if not cfg.personas_path.exists():
        # the corpus is DERIVED from platform evidence, so a fixture SIMULATES a completed derivation
        # (apply_auto_corpus, the only writer) rather than hand-curating tags one by one.
        # MOL-523: selection_scope / content_focus / hook_angle are FREE TEXT; cut_policy holds the tokens.
        # The prose here is the engine's own former clause for each archetype, so the two stay DIVERGENT —
        # which is the whole point of this fixture (the differentiation proofs compare their prompts).
        specs = (
            ("Credibility First", "restraint is the product; pass on the sensational cut",
             "Favor clear and accurate over sensational; pass on cuts that misrepresent the source.",
             ["emotional", "storytelling"], "open a curiosity gap the viewer has to close",
             ["#podcast", "#facts"]),
            ("Controversy", "pick the cut that starts the argument",
             "Prefer the most inflammatory or rivalry-coded statement in the source.",
             ["bold-statement"], "dare or challenge the viewer to react",
             ["#drama", "#popculture"]),
        )
        for name, voice, scope, policy, angle, corpus in specs:
            pid = P.add_persona(cfg, name=name, voice=voice, selection_scope=scope,
                                cut_policy=policy, hook_angle=angle, niche=["hiphop"])
            P.apply_auto_corpus(cfg, pid, tags=corpus, meta={})
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
