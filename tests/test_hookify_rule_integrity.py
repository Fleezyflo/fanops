"""Loader-integrity guard for the `.claude/hookify.*.local.md` PreToolUse rule layer.

WHY THIS EXISTS — a silent, total-denial footgun in the vendored loader.
`hookify_vendor/config_loader.extract_frontmatter` splits the file with `content.split('---', 2)`,
so the frontmatter region ends at the SECOND `---` ANYWHERE in the file — not at the closing
delimiter. One stray `---` inside the frontmatter (an em-dash typed as three hyphens in a rationale
comment, a `---` inside a regex) truncates it mid-`conditions:`. The truncated condition dict keeps
its `field:`/`operator:` but loses `pattern:`, and `Condition.from_dict` defaults a missing pattern
to `''`. `rule_engine.compile_regex('')` then matches EVERY string, so that rule DENIES EVERY tool
call — with no parse error, no warning, and no failed import. A previous edit hit this by accident.

The mirror-image failure is just as silent: `load_rules()` globs the CWD-relative
`.claude/hookify.*.local.md`, so a wrong cwd loads ZERO rules and every block rule becomes a no-op.
`.claude/hooks/hookify-run.py` pins the cwd for that; `test_rule_files_all_load` pins the count so a
regression shows up as a number, not as a guardrail layer that quietly stopped existing.

Both directions are asserted here: structurally (every condition carries a non-empty, compilable
pattern) and behaviourally (no rule fires on a routine developer payload).

SCOPE NOTE: the rule files are machine-local and gitignored (`.gitignore:33` — `.claude/*.local.md`,
untracked since ef845cac), so they do NOT exist on a CI runner. The checks are conditional on the
files being present: they are real assertions on an operator machine and deselect where there is
nothing to assert on. `-rs` shows the skip reason rather than a false green.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / ".claude" / "hooks" / "hookify_vendor"
RULE_FILES = sorted((REPO / ".claude").glob("hookify.*.local.md"))

# Raw-source counts of what the frontmatter DECLARES, compared against what the loader actually
# produced. An empty pattern is only one of the two shapes the truncation takes: the cut can also
# land BEFORE a whole `- field:` block, in which case every surviving condition looks perfectly
# healthy and the rule silently loses a condition — e.g. block-silent-fail-open reduced to its
# `file_path: \.py$` condition alone denies EVERY python write. Counting the declarations is what
# separates "the loader read the file" from "the loader read part of the file".
_DECLARED_CONDITION = re.compile(r"^\s*-\s+field:\s*\S", re.M)
_DECLARED_PATTERN = re.compile(r"^\s*pattern:\s*\S", re.M)

pytestmark = pytest.mark.skipif(
    not RULE_FILES or not VENDOR.is_dir(),
    reason="hookify rule files are gitignored machine-local guardrails (.gitignore:33); absent here",
)

# Routine developer payloads, one per file/path shape the rule layer scopes itself to. Each one is a
# real operation from this repo's workflow and must stay ALLOWED. Their job is to give the behavioural
# assertion teeth: a rule whose file_path condition survived truncation but whose content condition
# lost its pattern only reveals itself on a payload whose path the surviving condition still accepts —
# so a python file, a test file, a plan, a doc and a rule file are all represented.
_BENIGN_PY = "def add(a, b):\n    return a + b\n"
BENIGN_PAYLOADS = [
    ("bash-ls", "Bash", {"command": "ls -la"}),
    ("bash-git-status", "Bash", {"command": "git status"}),
    ("bash-ruff", "Bash", {"command": "ruff check ."}),
    ("write-src-py", "Write", {"file_path": f"{REPO}/src/fanops/example.py", "content": _BENIGN_PY}),
    ("edit-src-py", "Edit", {"file_path": f"{REPO}/src/fanops/example.py",
                             "old_string": "a + b", "new_string": "a - b"}),
    ("write-test-py", "Write", {"file_path": f"{REPO}/tests/test_example.py",
                                "content": "def test_add():\n    assert add(2, 3) == 5\n"}),
    ("write-plan", "Write", {"file_path": f"{REPO}/.claude/plans/m10.plan.md",
                             "content": "## M10\nThe ledger becomes append-only; a wipe is unrepresentable.\n"}),
    ("write-doc", "Write", {"file_path": f"{REPO}/docs/ENFORCEMENT.md",
                            "content": "# Enforcement index\n- tests/test_governance_tombstone.py\n"}),
    ("write-json", "Write", {"file_path": f"{REPO}/docs/example.json", "content": '{"a": 1}'}),
    ("edit-rule-enable", "Edit", {"file_path": f"{REPO}/.claude/hookify.block-reformat.local.md",
                                  "old_string": "enabled: true", "new_string": "enabled: true"}),
    ("multiedit-src-py", "MultiEdit", {"file_path": f"{REPO}/src/fanops/example.py",
                                       "edits": [{"old_string": "a + b", "new_string": "a * b"}]}),
]


def _vendor():
    """Import the vendored loader/engine the runner actually uses (not a reimplementation)."""
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    import config_loader
    import rule_engine
    return config_loader, rule_engine


def _rule_for(path):
    """Parse one rule file through the real loader. Returns None when it yields no rule at all."""
    config_loader, _ = _vendor()
    return config_loader.load_rule_file(str(path))


def _expected_name(path):
    return path.name[len("hookify."):-len(".local.md")]


def _rule_defects(path, rule):
    """Every way the `---` truncation (or an equivalent parse loss) shows up in a loaded rule.

    Returned as a list of strings so a failure names the exact key that went missing instead of
    just asserting a boolean.
    """
    defects = []
    raw = path.read_text()

    # Root cause, named directly: the loader's frontmatter region must END at a line that is exactly
    # `---`. A stray `---` mid-line (an em-dash typed as three hyphens) makes the split stop there,
    # and everything after it — including the remaining `pattern:` keys — falls into the message body.
    _, _, after_open = raw.partition("---")
    frontmatter, _, body = after_open.partition("---")
    if not (frontmatter.endswith("\n") and (body == "" or body.startswith("\n"))):
        defects.append(f"{path.name}: frontmatter is cut by a stray `---` mid-line, not by the closing "
                       f"delimiter (loader sees it ending at ...{frontmatter[-40:]!r}). "
                       "Use an em-dash, not three hyphens.")

    if rule is None:
        return defects + [f"{path.name}: produced NO rule (frontmatter unparseable)"]

    declared_conditions = len(_DECLARED_CONDITION.findall(raw))
    declared_patterns = len(_DECLARED_PATTERN.findall(raw))
    if len(rule.conditions) != declared_conditions:
        defects.append(f"{path.name}: file declares {declared_conditions} conditions but the loader "
                       f"produced {len(rule.conditions)} — a dropped condition makes the rule "
                       "MORE permissive to write and far broader to match")
    live_patterns = sum(1 for c in rule.conditions if c.pattern)
    if live_patterns != declared_patterns:
        defects.append(f"{path.name}: file declares {declared_patterns} patterns but only "
                       f"{live_patterns} survived parsing")

    if rule.name != _expected_name(path):
        defects.append(f"{path.name}: name={rule.name!r} != filename stem {_expected_name(path)!r}")
    if not rule.event:
        defects.append(f"{path.name}: lost `event:`")
    if rule.action not in ("block", "warn"):
        defects.append(f"{path.name}: action={rule.action!r} not block/warn")
    if not rule.conditions:
        defects.append(f"{path.name}: NO conditions — rule is inert, it can never fire")
    for i, cond in enumerate(rule.conditions):
        where = f"{path.name}: condition[{i}]"
        if not cond.field:
            defects.append(f"{where} lost `field:`")
        if not cond.operator:
            defects.append(f"{where} lost `operator:`")
        if not cond.pattern:
            defects.append(f"{where} (field={cond.field!r}) has an EMPTY pattern — "
                           "re.compile('') matches everything, so this rule denies every tool call")
            continue
        if cond.operator == "regex_match":
            try:
                re.compile(cond.pattern)
            except re.error as exc:
                defects.append(f"{where} pattern does not compile: {exc}")
    return defects


@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: _expected_name(p))
def test_rule_frontmatter_survives_the_loader(path):
    """Every condition of every rule — enabled or parked — keeps a non-empty, compilable pattern."""
    assert not _rule_defects(path, _rule_for(path))


def test_rule_files_all_load(monkeypatch):
    """`load_rules()` returns exactly the enabled rule files — not zero (wrong cwd), not more."""
    config_loader, _ = _vendor()
    monkeypatch.chdir(REPO)
    loaded = config_loader.load_rules(event=None)
    expected = {_expected_name(p) for p in RULE_FILES if getattr(_rule_for(p), "enabled", False)}
    assert {r.name for r in loaded} == expected
    assert loaded, "zero rules loaded — the whole guardrail layer is a silent no-op"


@pytest.mark.parametrize("label,tool_name,tool_input", BENIGN_PAYLOADS, ids=[p[0] for p in BENIGN_PAYLOADS])
def test_no_rule_fires_on_a_routine_operation(monkeypatch, label, tool_name, tool_input):
    """No loaded rule matches a routine developer payload.

    This is the behavioural half: an empty pattern that slipped past the structural check would
    show up here as a rule denying `ls -la`.
    """
    config_loader, rule_engine = _vendor()
    monkeypatch.chdir(REPO)
    data = {"session_id": "integrity", "hook_event_name": "PreToolUse", "cwd": str(REPO),
            "tool_name": tool_name, "tool_input": tool_input}
    engine = rule_engine.RuleEngine()
    fired = [r.name for r in config_loader.load_rules(event=None) if engine._rule_matches(r, data)]
    assert not fired, f"{label} is a routine operation but fired: {fired}"
