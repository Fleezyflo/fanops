"""Static contract for the TRACKED INSTRUCTION CORPUS (MOL-725).

Two failure modes rotted the instruction docs faster than anyone read them:

1. **A local link that resolves nowhere.** A router is only worth reading if its destinations open. Links to
   deleted namespaces, and off-by-one relative paths, both read as authoritative and both dead-end.
2. **A superseded operational claim.** Prose that once matched the runtime and no longer does is worse than no
   prose: an agent acts on it. Every pattern below was a REAL stale claim in a tracked doc at the time this
   test landed, each one contradicted by executable source or live GitHub config.

Scope is the instruction corpus an agent is routed through — the root/nested rulebooks and `docs/`. NOT
`.reports/architecture/**`: that tree is a generated architecture KB whose ~1k `src/fanops/foo.py:123` links
are IDE-style deep links written root-relative on purpose, so link-resolving them is meaningless.

Each checker has a NEGATIVE CONTROL that feeds it a known-bad string, because a doc rule that cannot be shown
to fire is decoration. Pure static reads; no ledger, no network, no subprocess.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The instruction corpus: what an agent is told to read.
ROOTS = ("CLAUDE.md", "AGENTS.md", "README.md")
GLOBS = ("src/fanops/**/CLAUDE.md", "tests/CLAUDE.md", "docs/**/*.md")

# `[text](target)` — target captured up to a closing paren, whitespace, or a `#`/`?` suffix.
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s#?]+)[^)]*\)")
_EXTERNAL = ("http://", "https://", "mailto:", "tel:", "data:", "//")
_FENCE = re.compile(r"^\s*```")
# Inline code is blanked before link-matching: Python like `_PROMPT[kind](payload)` is subscript-then-call, not
# a markdown link. A link whose TEXT is a code span still matches — only the span's contents are dropped.
_INLINE_CODE = re.compile(r"`[^`]*`")


def _corpus() -> list[Path]:
    seen: dict[Path, None] = {}
    for name in ROOTS:
        p = ROOT / name
        if p.is_file(): seen[p] = None
    for pat in GLOBS:
        for p in sorted(ROOT.glob(pat)):
            if p.is_file(): seen[p] = None
    return list(seen)


def _rel(path: Path) -> str:
    """Repo-relative label, falling back to the raw path (the negative controls feed a tmp_path)."""
    try: return path.relative_to(ROOT).as_posix()
    except ValueError: return path.as_posix()


def _outside_fences(text: str):
    """Yield (lineno, line) for lines outside ``` fences — a fenced example is not a live link/claim."""
    infence = False
    for i, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line): infence = not infence; continue
        if not infence: yield i, line


def broken_links(path: Path, text: str) -> list[str]:
    """Repo-local markdown links in `text` whose target does not exist, resolved against `path`'s directory."""
    bad = []
    for lineno, line in _outside_fences(text):
        for target in _LINK.findall(_INLINE_CODE.sub(" ", line)):
            if target.startswith(_EXTERNAL) or target.startswith("<"): continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists(): bad.append(f"{_rel(path)}:{lineno} -> {target}")
    return bad


# (regex, what the runtime actually does). Written so the DISPROVED phrasing matches and the corrected
# phrasing does not — each `why` names the source that decides it.
SUPERSEDED = (
    (re.compile(r"niche\s*(∪|union)\s*[`*]*(durable\s+)?(LLM\s+)?vocab", re.I),
     "persona_research.persona_terms / relatedness_terms return niche_terms(per) ONLY (MOL-719) — LLM vocab is "
     "out of hashtag discovery entirely"),
    (re.compile(r"edit_caption[^\n]{0,80}skips the brand-risk", re.I),
     "studio/actions.edit_caption calls caption.brand_risk_flag and rejects an off-brand edit (MOL-86); "
     "tests/test_studio_actions.py::test_edit_caption_off_brand_rejected pins it"),
    (re.compile(r"anchors verified", re.I),
     "a dated 'anchors verified' promise cannot stay true across any line-shifting commit — say 'trust the "
     "symbol, re-find the line' instead"),
    # Only WHOLE-TREE coverage claims: an `N/N modules` fraction, `N modules under src/fanops`, or an
    # `N-module map/trace/coverage/split` routing claim. A per-cluster count ("these 17 modules", "129
    # module-level calls") is locally scoped and verifiable in place — deliberately NOT matched.
    (re.compile(r"\b\d{2,4}/\d{2,4} modules\b|\b\d{2,4}[- ]modules? under\b"
                r"|\b\d{2,4}-module (map|trace|coverage|split)\b", re.I),
     "a hand-frozen whole-tree module count drifts silently (the docs said 107/108/109 while the tree holds "
     "135); derive it — `git ls-files 'src/fanops/**/*.py'` — or drop the number"),
    (re.compile(r"definitive unit \+ e2e gate", re.I),
     "the e2e job is `workflow_dispatch` + nightly `schedule` only (.github/workflows/ci.yml job `if:`) and "
     "`unit` is the sole required status check — a PR never runs e2e"),
    (re.compile(r"\.reports/issue-register-[\d-]+\.md"),
     ".reports/* is gitignored apart from .reports/architecture/, so that register is one machine's local "
     "artifact and is absent from a fresh clone — never a tracked authority"),
)


def superseded_claims(path: Path, text: str) -> list[str]:
    hits = []
    for lineno, line in _outside_fences(text):
        for pat, why in SUPERSEDED:
            if pat.search(line): hits.append(f"{_rel(path)}:{lineno} — {pat.pattern!r}: {why}")
    return hits


def test_corpus_is_not_empty():
    """Guards the guard: a glob typo would make both checks below pass by scanning nothing."""
    files = _corpus()
    assert len(files) > 20, f"instruction corpus collapsed to {len(files)} files — the globs are wrong"
    names = {_rel(p) for p in files}
    for required in ("CLAUDE.md", "AGENTS.md", "README.md", "src/fanops/CLAUDE.md", "tests/CLAUDE.md"):
        assert required in names, f"{required} missing from the scanned corpus"


def test_tracked_local_links_resolve():
    bad = [b for p in _corpus() for b in broken_links(p, p.read_text(encoding="utf-8"))]
    assert not bad, "instruction docs link to paths that do not exist:\n  " + "\n  ".join(bad)


def test_no_superseded_operational_claims():
    hits = [h for p in _corpus() for h in superseded_claims(p, p.read_text(encoding="utf-8"))]
    assert not hits, "instruction docs assert behaviour the runtime no longer has:\n  " + "\n  ".join(hits)


# ── Negative controls: prove each checker fires on the exact rot it exists to catch ────────────────────────

def test_negative_control_broken_link_is_caught(tmp_path):
    doc = tmp_path / "router.md"
    doc.write_text("See [the register](../reports/issue-register-2026-07-03.md) first.\n", encoding="utf-8")
    assert broken_links(doc, doc.read_text()), "the link checker missed a target that does not exist"


def test_negative_control_live_link_is_not_flagged(tmp_path):
    """The other half: a resolvable link, an external URL and a fenced example must all stay clean."""
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    doc = tmp_path / "router.md"
    doc.write_text("[ok](real.md) [ext](https://example.invalid/nope.md)\n"
                   "```\n[fenced](totally-missing.md)\n```\n", encoding="utf-8")
    assert broken_links(doc, doc.read_text()) == []


@pytest.mark.parametrize("line", [
    "`persona_terms` = declared niche ∪ durable LLM vocab (MOL-637/644)",
    "**`edit_caption` (`actions.py:87`) skips the brand-risk guard** that its sibling enforces",
    "<!-- Edit-time rulebook. Anchors verified 2026-07-03. -->",
    "Full 108-module map / 10-cluster split: `docs/CODEMAPS/full-trace-index.md`.",
    "wait for CI (the definitive unit + e2e gate) to go GREEN",
    "Fixing any MOL-numbered issue -> `.reports/issue-register-2026-07-03.md` FIRST",
])
def test_negative_control_superseded_claim_is_caught(tmp_path, line):
    """Every string here is a VERBATIM claim that shipped in a tracked doc and was disproved by source."""
    doc = tmp_path / "stale.md"
    doc.write_text(line + "\n", encoding="utf-8")
    assert superseded_claims(doc, doc.read_text()), f"the claim checker missed: {line!r}"


def test_negative_control_corrected_phrasing_is_not_flagged(tmp_path):
    """The corrections this contract landed must themselves pass, or the rule is unclearable."""
    doc = tmp_path / "fixed.md"
    doc.write_text(
        "`persona_terms` returns the declared niche and nothing else (MOL-719).\n"
        "`edit_caption` applies the SAME `caption.brand_risk_flag` screen its sibling does.\n"
        "Line anchors are a starting point, not a promise - trust the symbol, re-find the line.\n"
        "Module split and safety verdicts: `docs/CODEMAPS/full-trace-index.md`.\n"
        "The `unit` job is the only required status check; `e2e` is dispatch + nightly only.\n"
        "Take the file:line from the Linear ticket body.\n", encoding="utf-8")
    assert superseded_claims(doc, doc.read_text()) == []
