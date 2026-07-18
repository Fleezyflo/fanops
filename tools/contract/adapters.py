"""The five ports, and the only concrete I/O in this package.

THIS IS THE ONLY MODULE THAT IMPORTS `subprocess`, `tools.arch` OR `tools.ci`. Isolating impurity
here is what lets `decide.py` and `validate.py` be exercised with fakes and no repository, no git and
no network — which in turn is what makes `AC-3` (determinism) and `AC-4` (totality) provable rather
than merely plausible.

Every port fails EXPLICITLY. A method that cannot answer returns a sentinel and records why, never a
plausible default: a governance tool that reports `continue` because a check silently did not run is
the single worst failure available to it, and `Derived.unverifiable` + rule `ST-7` exist to make that
outcome unreachable.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_TIMEOUT = 30                     # matches `tools/ci/live.py:15`'s probe budget; not a new number


class PortError(Exception):
    """Raised only by adapters. Callers convert it into a NAMED `unverifiable` entry."""


# ── git ─────────────────────────────────────────────────────────────────────────────────────
class RepoPort:
    """Four methods, so a fake is a dict literal. Every one of them is read-only."""

    def __init__(self, repo: Path = REPO) -> None:
        self.repo = repo

    def _git(self, *args: str, check: bool = True) -> tuple[str, int]:
        try:
            r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True,
                               timeout=_TIMEOUT)
        except FileNotFoundError:
            raise PortError("git not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise PortError(f"git {' '.join(args[:2])} timed out after {_TIMEOUT}s") from None
        if r.returncode != 0 and check:
            raise PortError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:160]}")
        return r.stdout, r.returncode

    def blob(self, ref: str, path: str) -> bytes | None:
        try:
            r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=self.repo,
                               capture_output=True, timeout=_TIMEOUT)
        except FileNotFoundError:
            raise PortError("git not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise PortError(f"git show timed out after {_TIMEOUT}s") from None
        return r.stdout if r.returncode == 0 else None

    def blob_sha(self, ref: str, path: str) -> str | None:
        """`None` when the path does not exist at `ref`. BOTH guards below are load-bearing.

        `git rev-parse` on a path that is absent from the ref exits 128 AND ECHOES ITS ARGUMENT TO
        STDOUT. Reading that stdout without checking the exit code returns the literal string
        `"<ref>:<path>"`, which is truthy — so `contains()` answered True for a file that does not
        exist, and a contract that had never landed derived the state `merged`. The shape check is
        the second guard: a blob id is 40 hex characters, and anything else is not an answer.
        """
        out, rc = self._git("rev-parse", f"{ref}:{path}", check=False)
        sha = out.strip()
        return sha if rc == 0 and _is_sha(sha) else None

    def diff_names(self, base: str, head: str) -> list[str]:
        """`base...head` — the three-dot form, so the diff is against the MERGE BASE.

        This is the same form `tools/arch/impact.py:41` uses. Two-dot would report every file `main`
        moved since the branch point as if this change had touched it, which would make the
        `unauthorized` set (ADR-0105 §5.3) fire on other people's commits.
        """
        out, _ = self._git("diff", "--name-only", f"{base}...{head}")
        return sorted(f for f in out.splitlines() if f)

    def contains(self, ref: str, path: str) -> bool:
        return self.blob_sha(ref, path) is not None

    def resolve(self, ref: str) -> str | None:
        out, rc = self._git("rev-parse", ref, check=False)
        sha = out.strip()
        return sha if rc == 0 and _is_sha(sha) else None

    def is_ancestor(self, maybe_ancestor: str, ref: str) -> bool:
        """`git merge-base --is-ancestor` — exit 0 yes, 1 no, anything else is not an answer.

        This is what makes `diff_names(parent, head)` safe to read as "what the append added": the
        three-dot form diffs against the merge base, and the merge base IS `parent` exactly when
        `parent` is an ancestor. Without this guard a sibling branch could present a small diff and
        borrow an approval it was never given.
        """
        _, rc = self._git("merge-base", "--is-ancestor", maybe_ancestor, ref, check=False)
        return rc == 0


def _is_sha(s: str) -> bool:
    return len(s) == 40 and all(c in "0123456789abcdef" for c in s)


# ── tools.arch ──────────────────────────────────────────────────────────────────────────────
class ImpactPort:
    """One method. `tools.arch.impact.report` already returns a dict in-process.

    ADR-0105 §12 records gap **G1** as *"`tools.arch impact` emits Markdown only"*. That is true of
    the CLI (`cli.py:181-184` prints `render(rep)`), not of the library: `report()` has returned a
    dict since it was written. Calling it in-process closes G1 with no change to `tools/arch`.
    """

    def report(self, base: str) -> dict:
        try:
            from tools.arch import impact
        except Exception as exc:
            raise PortError(f"cannot import tools.arch.impact: {type(exc).__name__}: {exc}") from None
        try:
            return impact.report(base)
        except Exception as exc:
            raise PortError(f"impact.report({base!r}) failed: {type(exc).__name__}: {exc}") from None


class ArtifactPort:
    """The five tracked-artifact reads, wrapped so a fake needs no `.reports/` tree."""

    def __init__(self, derived: Path | None = None) -> None:
        self.derived = derived or (REPO / ".reports" / "architecture" / "derived")

    def _load(self, name: str) -> dict:
        p = self.derived / f"{name}.json"
        if not p.exists():
            raise PortError(f"derived artifact {name}.json is absent at {self.derived}")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PortError(f"derived/{name}.json is unparseable: {exc}") from None

    def modules(self) -> dict: return self._load("modules")

    def dependencies(self) -> dict: return self._load("dependencies")

    def entities(self) -> dict: return self._load("entities")

    def generated_paths(self) -> set[str]:
        """Which repository paths are generated, from the ONE dict `cmd_docs` and drift both read."""
        out = {"/".join(Path(p).relative_to(REPO).parts) for p in _expected_docs()}
        return out | {f".reports/architecture/derived/{p.name}" for p in self.derived.glob("*.json")}

    def stale(self) -> list[str]:
        try:
            from tools.arch import drift
        except Exception as exc:
            raise PortError(f"cannot import tools.arch.drift: {type(exc).__name__}: {exc}") from None
        try:
            return sorted(d.artifact for d in drift.all_stale())
        except Exception as exc:
            raise PortError(f"drift.all_stale() failed: {type(exc).__name__}: {exc}") from None


def _expected_docs() -> list[Path]:
    from tools.arch import render
    return list(render.expected().keys())


# ── tools.ci ────────────────────────────────────────────────────────────────────────────────
class RegistryPort:
    """The only `tools.ci` dependency, imported LAZILY and on purpose.

    `tools/ci/registry.py` imports PyYAML at module level, and PyYAML is UNDECLARED in this
    repository — it reaches the CI unit lane third-order through `vcrpy` in the `dev` extra
    (`requirements/ci-unit.txt` records `# via vcrpy`). An environment without it is therefore a
    realistic environment, and a module-level import here would turn that into an ImportError crash
    at startup instead of a named `unverifiable` input. Degrading is correct; crashing is not, and
    silently passing would be worse than either.
    """

    def control_ids(self) -> set[str]:
        try:
            from tools.ci.registry import load_registry
        except Exception as exc:
            raise PortError(f"control ids unavailable ({type(exc).__name__}: {exc}); PyYAML is "
                            f"undeclared and reaches tools/ci third-order via vcrpy") from None
        try:
            reg = load_registry()
        except Exception as exc:
            raise PortError(f"cannot load the control registry: {type(exc).__name__}: {exc}") from None
        return {c["id"] for c in reg.get("controls", []) if isinstance(c, dict) and "id" in c}


# ── gh ──────────────────────────────────────────────────────────────────────────────────────
class ReviewPort:
    """The only `gh` surface. Modelled on `tools/ci/live.py:15-29` — a failure is a message.

    ADR-0105 §Risks directs Phase 3 to COMPARE `review.commit_id` against the head rather than trust
    GitHub's `reviewDecision` badge, whether or not `dismiss_stale_reviews` is enabled. So this
    returns the raw `(commit_id, state)` pairs and `lifecycle.py` does the comparison; the badge is
    never read.
    """

    def __init__(self, repo_slug: str = "") -> None:
        self.repo_slug = repo_slug

    def _api(self, path: str) -> list:
        try:
            r = subprocess.run(["gh", "api", path, "--paginate"], capture_output=True, text=True,
                               timeout=_TIMEOUT)
        except FileNotFoundError:
            raise PortError("gh CLI not found") from None
        except subprocess.TimeoutExpired:
            raise PortError(f"gh api timed out after {_TIMEOUT}s") from None
        if r.returncode != 0:
            raise PortError(r.stderr.strip()[:160] or f"gh api exit {r.returncode}")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            raise PortError(f"unparseable gh JSON: {exc}") from None
        return [x for x in data if isinstance(x, dict)]

    def approvals(self, pr: int) -> list[tuple[str, str]]:
        slug = self.repo_slug or _slug()
        return [(str(x.get("commit_id", "")), str(x.get("state", "")))
                for x in self._api(f"repos/{slug}/pulls/{pr}/reviews")]

    def write_principals(self) -> list[str]:
        """Logins that can push. The ONE fact that decides whether §4.1's witnessed route exists.

        It is read from the platform and never from the repository, because a fact the governed tree
        can assert about itself is a fact an agent can arrange. `PortError` (no `gh`, no network, no
        permission) leaves the answer UNKNOWN, and `lifecycle.gates` treats unknown as "the
        unwitnessed route is inadmissible" — the fail-closed direction.
        """
        slug = self.repo_slug or _slug()
        return sorted({str(x.get("login", "")) for x in self._api(f"repos/{slug}/collaborators")
                       if isinstance(x.get("permissions"), dict) and x["permissions"].get("push")})


def _slug() -> str:
    """The repo slug `tools/ci` already declares. Unreachable ⇒ PortError, never a blank slug.

    A blank slug would build the URL `repos//pulls/N/reviews`, which `gh` answers with a 404 — an
    error that reads like "no such PR" rather than "the slug was never resolved". Two different
    failures must not arrive wearing the same face.
    """
    try:
        from tools.ci.common import DEFAULT_REPO
    except Exception as exc:
        raise PortError(f"cannot resolve the repository slug: {type(exc).__name__}: {exc}") from None
    return DEFAULT_REPO
