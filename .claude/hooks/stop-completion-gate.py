#!/usr/bin/env python3
"""Stop completion-gate — guarantees code work is actually DONE before a turn ends.

This does NOT block any build step. It runs only at the Stop event (when the agent
tries to end its turn) and, *only if this session changed Python*, refuses to stop
while the project's own definition-of-done is unmet:

  1. no stub/placeholder in the lines I ADDED  (TODO/FIXME/assert True/NotImplementedError)
  2. `ruff check .` is clean                    (the CI lint gate, per CLAUDE.md)
  3. the fast unit suite is green               (`pytest -q -m "not integration"`)

Cheap gates run first and short-circuit, so pytest only runs once lint/stubs are clean.

Safety (this gate can NEVER trap you):
  * kill-switch:  export FANOPS_STOP_GATE=0   -> always allow
  * 3-strike circuit breaker: the SAME failure signature blocks at most 3 times,
    then allows with a loud warning (so a pre-existing/unfixable red can't loop you).
  * fail-OPEN on any internal error: a bug in this gate allows the stop, never blocks.

Contract: print {"decision":"block","reason":...} to stdout to keep the turn going;
print nothing (exit 0) to allow the stop. Mirrors the hookify Stop contract.
"""
import sys, os, re, json, hashlib, subprocess, tempfile, pathlib

STUB = re.compile(r'\bTODO\b|\bFIXME\b|\bassert\s+True\b|\bNotImplementedError\b|#\s*for now\b')
PYTEST_CEILING_S = 150
MAX_STRIKES = 3


def allow():
    sys.exit(0)


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason, "systemMessage": reason}))
    sys.exit(0)


def run(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except FileNotFoundError:
        return 127, "NOTFOUND"


def changed_py(root):
    """Return (added_lines_by_file, list_of_changed_py). Added lines = git diff '+' lines
    for tracked files + whole body for untracked new files. Restricted to src/ and tests/."""
    added, files = {}, set()
    def keep(p): return p.endswith('.py') and (p.startswith('src/') or p.startswith('tests/'))
    # tracked changes (unstaged + staged), zero context so only real adds show
    rc, diff = run(['git', 'diff', '-U0', 'HEAD', '--', 'src', 'tests'], root, 30)
    if rc == 0:
        cur = None
        for ln in diff.splitlines():
            if ln.startswith('+++ b/'):
                cur = ln[6:]
                if keep(cur):
                    files.add(cur); added.setdefault(cur, [])
                else:
                    cur = None
            elif cur and ln.startswith('+') and not ln.startswith('+++'):
                added[cur].append(ln[1:])
    # untracked new files
    rc, out = run(['git', 'ls-files', '--others', '--exclude-standard', '--', 'src', 'tests'], root, 30)
    if rc == 0:
        for p in out.split():
            if keep(p):
                files.add(p)
                try:
                    added.setdefault(p, []).extend(pathlib.Path(root, p).read_text(errors='ignore').splitlines())
                except OSError:
                    pass
    return added, sorted(files)


def strike(root, sig):
    """Return how many times this exact failure signature has blocked (incl. this one)."""
    sess = os.environ.get('CLAUDE_SESSION_ID') or hashlib.sha1(root.encode()).hexdigest()[:12]
    f = pathlib.Path(tempfile.gettempdir(), f'fanops-stopgate-{re.sub(r"[^A-Za-z0-9_-]","_",sess)}.json')
    try:
        state = json.loads(f.read_text())
    except (OSError, ValueError):
        state = {}
    state[sig] = state.get(sig, 0) + 1
    try:
        f.write_text(json.dumps(state))
    except OSError:
        pass
    return state[sig]


# (claim regex, evidence regex): a strong verification CLAIM in the closing message
# is only honest if a matching evidence tool ran in the SAME turn. Narrow on purpose.
CLAIMS = [
    (r"\b(tests?|the suite|pytest)\b[^.\n]{0,24}\b(pass|passes|passing|green)\b|\ball green\b", r"pytest|ruff"),
    (r"\b(it'?s|now)\s+live\b|\bwent live\b|\bposted to (ig|instagram|tiktok)\b|\bthe (post|reel)\b[^.\n]{0,20}\blive\b|\blanded on (ig|instagram)\b", r"curl|\bgh\b|postiz|https?://|playwright|browser_|requests\."),
    # verify-vs-disk: asserting branch/CI/PR state needs a git/gh check THIS turn
    (r"\bmain is (synced|up to date|clean|even)\b|\bbranch is (clean|synced|up to date)\b|\bci (passed|is green|went green)\b|\bthe pr is (mergeable|clean|green)\b|\bin sync with (origin|main)\b", r"\bgit\b|\bgh\b|origin/"),
]

# self-narrowing confessions in the closing message — flag unless already marked as an
# explicit operator-facing scope cut.  Resolution for the #1 recurring frustration.
NARROW = re.compile(
    r"for brevity|rather than (do(ing)? |implement(ing)? )?all|a (representative |small )?subset|"
    r"the most important (ones|few|part)|i(?:'ll| will)? just do\b|instead of all\b|"
    r"didn'?t (do|implement|cover|handle|finish) (all|every|the (rest|others))|"
    r"the (rest|remaining|others) (can|could|will) (be )?(done|added|handled) later|"
    r"as (a )?follow-?up\b|left .{0,20}(as|for) (later|follow)", re.I)


def _read_jsonl(path):
    out = []
    try:
        with open(path, errors='ignore') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try: out.append(json.loads(line))
                    except ValueError: pass
    except OSError:
        return []
    return out


def claim_without_evidence(transcript_path):
    """Block ending a turn whose closing message asserts a strong, checkable claim
    that no tool in THIS turn backs. Satisfiable: run the check, then stop."""
    if not transcript_path:
        return None
    recs = _read_jsonl(transcript_path)
    if not recs:
        return None
    # current turn = records after the last genuine (non-tool-result) user prompt
    start = 0
    for i in range(len(recs) - 1, -1, -1):
        if recs[i].get('type') == 'user':
            c = recs[i].get('message', {}).get('content')
            is_tr = isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c)
            if not is_tr:
                start = i; break
    final_text, tool_blob = '', ''
    for r in recs[start:]:
        content = (r.get('message') or {}).get('content')
        if r.get('type') == 'assistant' and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict): continue
                if b.get('type') == 'text' and b.get('text', '').strip():
                    final_text = b['text']
                elif b.get('type') == 'tool_use':
                    tool_blob += ' ' + json.dumps(b.get('input', {}))[:2000]
        elif r.get('type') == 'user' and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    tool_blob += ' ' + str(b.get('content', ''))[:2000]
    if not final_text:
        return None
    # #1 narrowing: a self-admission of incompleteness with no explicit operator scope-cut
    if NARROW.search(final_text) and not re.search(r'scope[- ]?cut', final_text, re.I):
        m = NARROW.search(final_text)
        return (f'POSSIBLE SILENT NARROWING — your closing message says "{m.group(0).strip()[:50]}". '
                f'Execute-literally: do the task IN FULL now, OR turn the cut into an explicit '
                f'"SCOPE CUT (operator approval needed): ..." line, then stop again.')
    for claim, ev in CLAIMS:
        m = re.search(claim, final_text, re.I)
        if m and not re.search(ev, tool_blob, re.I):
            return (f'CLAIM WITHOUT EVIDENCE — your closing message says "{m.group(0).strip()[:60]}" '
                    f'but no {ev.split("|")[0].strip(chr(92)+"b")}-style check ran in THIS turn. '
                    f'Run it and show the result, or retract the claim.')
    return None


def main():
    if os.environ.get('FANOPS_STOP_GATE') == '0':
        allow()
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        inp = {}
    root = inp.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()

    failures = []

    # 0) claim-evidence (cheap, ALWAYS) — refutes "prose deceit is unhookable"
    ce = claim_without_evidence(inp.get('transcript_path'))
    if ce:
        failures.append(ce)

    # code gates only for sessions that changed src/|tests/ .py
    added, files = ({}, [])
    if pathlib.Path(root, '.git').exists():
        added, files = changed_py(root)

    # 1) stub/placeholder in ADDED lines (cheap)
    if not failures and files:
        stubbed = []
        for f, lines in added.items():
            for ln in lines:
                if STUB.search(ln):
                    stubbed.append(f"{f}: {ln.strip()[:80]}")
                    break
        if stubbed:
            failures.append("STUB/PLACEHOLDER in your added lines (simulated completion):\n  " + "\n  ".join(stubbed[:8]))

    # 2) ruff (fast) — only if nothing failed yet, to give one clear failure at a time
    if not failures and files:
        rc, out = run(['ruff', 'check', '.'], root, 60)
        if rc not in (0, 127):  # 127 = ruff not installed -> can't gate, skip
            tail = "\n  ".join([line for line in out.splitlines() if line.strip()][-12:])
            failures.append(f"RUFF is red (the CI lint gate):\n  {tail}")

    # 3) fast unit suite — only if lint+stubs clean (most expensive, run last)
    if not failures and files:
        rc, out = run([sys.executable, '-m', 'pytest', '-q', '-m', 'not integration', '--timeout=60'], root, PYTEST_CEILING_S)
        if rc is None:
            failures.append(f"PYTEST exceeded {PYTEST_CEILING_S}s — a hang is the bug (CLAUDE.md: ledger flock). Don't end on an unverifiable suite.")
        elif rc not in (0, 5, 127):  # 5 = no tests collected, 127 = pytest absent
            tail = "\n  ".join([line for line in out.splitlines() if line.strip()][-15:])
            failures.append(f"PYTEST is red:\n  {tail}")

    if not failures:
        allow()

    body = "\n\n".join(failures)
    sig = hashlib.sha1(body.encode()).hexdigest()[:16]
    n = strike(root, sig)
    if n > MAX_STRIKES:
        # circuit broken: do not trap the operator on an unfixable/pre-existing red
        sys.stderr.write(f"[stop-gate] gave up after {MAX_STRIKES} attempts; allowing stop with red:\n{body}\n")
        allow()
    block(
        f"Not done yet — the build's definition-of-done is unmet (attempt {n}/{MAX_STRIKES}). "
        f"This did not block any step; it blocks ENDING on unverified work.\n\n{body}\n\n"
        f"Fix it and finish, or `export FANOPS_STOP_GATE=0` to disable this gate deliberately."
    )


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # gate must never trap the user on its own bug
        sys.stderr.write(f"[stop-gate] internal error, failing open: {e}\n")
        sys.exit(0)
