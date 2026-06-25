---
name: block-test-defeating
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: (^|/)tests/.*\.py$|(^|/)test_[^/]*\.py$
  - field: content
    operator: regex_match
    pattern: \bassert\s+True\b|\bpytest\.skip\(|@pytest\.mark\.(skip|xfail)
---
BLOCKED: defeating a test to fake green. `assert True`, `pytest.skip(...)`, `@pytest.mark.skip/xfail` make a red suite report pass — that hollows out the completion-gate (which trusts the suite). Fix the IMPLEMENTATION so the real assertion passes; don't weaken or skip the test. A genuinely-not-yet-supported case gets an xfail ONLY with an operator-visible reason — disable this rule deliberately for that one edit.
