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
import re
import subprocess
from pathlib import Path

from .model import CI_REGISTRY_PATH, REQUIRED_CONTEXTS_KEY

REPO = Path(__file__).resolve().parents[2]

_TIMEOUT = 30                     # matches `tools/ci/live.py:15`'s probe budget; not a new number

# A GitHub path segment this module is willing to build. Deliberately narrow: no slash, no dot, so a
# segment can neither traverse (`../reviews`) nor append (`707/reviews`). See `MergeFactsPort`.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}$")


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

    def tree_of(self, ref: str) -> str | None:
        """The TREE object a commit points at — content identity, independent of commit identity.

        A squash merge deliberately produces a different COMMIT than the PR head (new parent, new
        message, new hash) while producing the same CONTENT. Comparing commits would therefore always
        differ and prove nothing; comparing trees answers the question actually being asked — did the
        thing that landed equal the thing that was authorized (ADR-0105 §4.3a).
        """
        out, rc = self._git("rev-parse", f"{ref}^{{tree}}", check=False)
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


# ── GitHub merge facts ──────────────────────────────────────────────────────────────────────
#
# THE ONE THING THIS PORT MUST NOT BECOME IS A GENERAL GITHUB CLIENT.
#
# Merge authorization is still the operator's own parent-bound lifecycle event; no port reads
# reviews, reviewer identity, collaborators, App installations, deploy keys or workflow tokens.
# `ReviewPort` stays DELETED. What ADR-0105 §4.3a needs is different in kind: after a squash the
# authorized parent is no longer an ancestor of the head, so verifying an authorization that already
# existed requires the pre-merge PR head — a merge fact, not a person.
#
# The safety property is enforced by SHAPE, not by discipline. There is no `get(path)`, no `api()`,
# no base-URL parameter and no format string a caller can steer: three private methods each build
# their own fixed path from validated components. `/reviews` is not one argument away, because there
# is no argument that reaches path construction. This mirrors `lifecycle.gates()` having no
# `reviews` parameter — you cannot pass what the signature cannot express.
class MergeFactsPort:
    """Three closed reads: the PR's merge facts, the check runs at a SHA, the protected contexts."""

    def __init__(self, slug: str = "") -> None:
        self.slug = slug or _repo_slug()
        if not _SAFE_SLUG.match(self.slug):
            raise PortError(f"refusing to build a GitHub path from the slug {self.slug!r}")

    def _api(self, *segments: str) -> object:
        """`gh api <fixed-path>` — segments are validated, never a caller-supplied path.

        The slug is validated in `__init__` and every other component here, so EVERY part of the
        path is constrained. An unvalidated component would reopen by the back door exactly what the
        missing `get(path)` closes at the front.
        """
        for s in segments:
            if not _SAFE_SEGMENT.match(s):
                raise PortError(f"refusing to build a GitHub path from {s!r}")
        path = "/".join(("repos", self.slug, *segments))
        try:
            r = subprocess.run(["gh", "api", path, "--paginate"], capture_output=True, text=True,
                               timeout=_TIMEOUT)
        except FileNotFoundError:
            raise PortError("gh not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise PortError(f"gh api {path} timed out after {_TIMEOUT}s") from None
        if r.returncode != 0:
            raise PortError(f"gh api {path} failed: {r.stderr.strip()[:160]}")
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            raise PortError(f"gh api {path} returned unparseable JSON: {exc}") from None

    def pull(self, pr: int) -> dict:
        """Merge facts for ONE pull request. Returns only the five keys §4.3a reads."""
        d = self._api("pulls", str(int(pr)))
        if not isinstance(d, dict):
            raise PortError(f"gh api pulls/{pr} did not return an object")
        head = (d.get("head") or {}).get("sha") or ""
        return {"pr_head": head, "merge_sha": d.get("merge_commit_sha") or "",
                "merged_at": d.get("merged_at") or "", "merged": bool(d.get("merged"))}

    def check_runs(self, sha: str) -> list[tuple[str, str, str]]:
        """`(id, name, conclusion)` for every check run bound to `sha`. Ids are strings on purpose.

        A check-run id is a platform object identifier, not a number to do arithmetic on, and the
        `accepted` row records it as text. Comparing text to text keeps the recorded set and the
        verified set the same kind of thing.

        PAGINATION IS VERIFIED, NOT ASSUMED. `total_count` is compared against what was actually
        returned, and a short read raises rather than answering. Silently returning page one would
        let "this run is absent" mean "this run was on page two" — an absence conjured from a
        truncated read, which is the strongest possible form of the failure `ST-7` exists to prevent.
        """
        if not _is_sha(sha):
            raise PortError(f"{sha!r} is not a 40-character commit SHA")
        d = self._api("commits", sha, "check-runs")
        if not isinstance(d, dict):
            raise PortError(f"gh api commits/{sha[:12]}/check-runs did not return an object")
        runs = [c for c in (d.get("check_runs") or []) if isinstance(c, dict)]
        total = d.get("total_count")
        if isinstance(total, int) and len(runs) < total:
            raise PortError(f"check-runs for {sha[:12]} returned {len(runs)} of {total} — the read "
                            f"is incomplete and an unreturned page is not proof of absence")
        return sorted((str(c.get("id") or ""), str(c.get("name") or ""),
                       str(c.get("conclusion") or "")) for c in runs)


def required_contexts_at(raw: bytes) -> list[str]:
    """The required set PINNED to the contract's base commit, parsed from the in-repo registry.

    NOT live branch protection. Live protection is present-day configuration: reading it here would
    mean relaxing a setting tomorrow could retroactively invalidate an acceptance recorded today, or
    manufacture one that was never earned. A verdict about the past must rest on evidence that is
    itself fixed in the past, and a git blob at a named commit is exactly that.

    `intended_required_contexts` is deliberately NOT consulted — it is an aspiration, and a bar that
    was never live cannot be the bar a past merge had to clear.
    """
    try:
        import yaml
    except Exception as exc:
        raise PortError(f"the required-context set is unreadable ({type(exc).__name__}: {exc}); "
                        f"PyYAML is undeclared and reaches tools/ci third-order via vcrpy") from None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise PortError(f"{CI_REGISTRY_PATH} is unparseable at the pinned commit: {exc}") from None
    if not isinstance(doc, dict) or REQUIRED_CONTEXTS_KEY not in doc:
        raise PortError(f"{CI_REGISTRY_PATH} has no `{REQUIRED_CONTEXTS_KEY}` at the pinned commit")
    ctx = doc.get(REQUIRED_CONTEXTS_KEY) or []
    if not isinstance(ctx, list) or not ctx:
        raise PortError(f"`{REQUIRED_CONTEXTS_KEY}` is empty at the pinned commit — a merge with no "
                        f"required context proves nothing about required CI")
    return sorted(str(c) for c in ctx)


def _repo_slug() -> str:
    """`owner/name` from the git remote. No network, and no way to point this at another repo."""
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO, capture_output=True,
                           text=True, timeout=_TIMEOUT)
    except FileNotFoundError:
        raise PortError("git not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise PortError(f"git remote get-url timed out after {_TIMEOUT}s") from None
    if r.returncode != 0:
        raise PortError(f"cannot read the origin remote: {r.stderr.strip()[:160]}")
    url = r.stdout.strip().removesuffix(".git")
    m = re.search(r"[:/]([^/:]+/[^/]+)$", url)
    if not m:
        raise PortError(f"cannot derive owner/name from the origin remote {url!r}")
    return m.group(1)
