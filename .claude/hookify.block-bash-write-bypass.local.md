---
name: block-bash-write-bypass
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: (?s)^(?=.*(\bcat\s*>|\btee\b|python[0-9.]*\s+-c|printf\b[^\n]*>|>\s*\S+\.py|open\([^)]*['"][wa]))(?=.*(\bTODO\b|\bFIXME\b|NotImplementedError|\bassert\s+True\b|\bpytest\.skip|pytest\.mark\.(skip|xfail)|#\s*for now\b))
---
BLOCKED: writing code through Bash to dodge the file-content rules. A heredoc / `python -c` / redirect into a file, carrying a stub or test-defeating marker (TODO, NotImplementedError, `assert True`, pytest.skip), is the same simulated-completion the Edit/Write rules catch — just routed around them. Write the real implementation with the normal editor so the content gate sees it.
