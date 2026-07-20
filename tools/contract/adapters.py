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
    """A small READ-ONLY surface, so a fake is a dict literal.

    The count is deliberately not stated here: it was "four" while the class had seven, because a
    number in prose does not move when the code does. `NC-AC-31` asserts the actual surface instead.
    """

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
# no base-URL parameter and no format string a caller can steer: each platform read builds its own
# FIXED path from validated components through the private `_api`, whose every segment must match
# `_SAFE_SEGMENT` and whose only query parameters are a validated commit SHA. `/reviews` is not one
# argument away, because no argument reaches path construction. This mirrors `lifecycle.gates()`
# having no `reviews` parameter — you cannot pass what the signature cannot express.
# The DOCUMENTED check-run join. A job's `check_run_url` is a published field naming the check run
# that job produced; the id is its last path component. The slug is captured so a URL pointing at
# another repository can be refused rather than joined.
_CHECK_RUN_URL = re.compile(r"^https://api\.github\.com/repos/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/check-runs/(\d+)$")

# GitHub Actions' App identity, PINNED. Required CI is only meaningful if the runs proving it were
# produced by the CI system; any App with `checks:write` can publish a green check run under a
# required context's exact name. Both the numeric id and the slug must match, because the id is
# stable and machine-checkable while the slug is what a human reads in the failure message.
GH_ACTIONS_APP_ID = "15368"
GH_ACTIONS_APP_SLUG = "github-actions"


class MergeFactsPort:
    """Four closed PLATFORM reads, and nothing that can be steered into a fifth.

    `pull` (merge facts incl. the PR BASE), `check_runs` (runs bound to a SHA, with their producing
    App identity and server timestamps), `workflow_runs` (the Actions runs at a SHA, with the
    workflow PATH each came from), and `jobs` (a run's jobs, each carrying `check_run_url` — the
    DOCUMENTED join back to a check run).

    The required-context set is NOT among them. It is parsed from the in-repo registry at the
    contract's own base commit by the module-level `required_contexts_at`, so live configuration
    cannot reach a verdict about the past. Its absence from this class is the same structural
    guarantee as the absence of a review reader: what the interface cannot express, no caller can
    request.
    """

    def __init__(self, slug: str = "") -> None:
        self.slug = slug or _repo_slug()
        if not _SAFE_SLUG.match(self.slug):
            raise PortError(f"refusing to build a GitHub path from the slug {self.slug!r}")

    def _api(self, *segments: str, head_sha: str = "") -> list:
        """`gh api <fixed-path> --paginate --slurp` → the LIST OF PAGE DOCUMENTS.

        The slug is validated in `__init__` and every other component here, so EVERY part of the
        path is constrained. An unvalidated component would reopen by the back door exactly what the
        missing `get(path)` closes at the front. `head_sha` is the ONLY query parameter this port can
        express and it must be a 40-hex commit SHA, so it can select a commit and nothing else — it
        cannot traverse, cannot name another repository, and cannot reach a different endpoint.

        `--slurp` IS THE CORRECTION, NOT A FLAG CHOICE. Plain `--paginate` concatenates one JSON
        document PER PAGE, and a single `json.loads` over that either throws on page two or silently
        reads only the first. Either way the previous implementation could see one page of a
        multi-page result while `total_count` was checked against that same short list, so a
        genuinely paginated answer would have been reported as complete. `--slurp` returns every page
        as an element of one array; aggregation and the `total_count` comparison happen in the
        endpoint-specific readers below, over the WHOLE result.
        """
        for seg in segments:
            if not _SAFE_SEGMENT.match(seg):
                raise PortError(f"refusing to build a GitHub path from {seg!r}")
        path = "/".join(("repos", self.slug, *segments))
        argv = ["gh", "api", path, "--paginate", "--slurp"]
        if head_sha:
            if not _is_sha(head_sha):
                raise PortError(f"refusing to query by {head_sha!r}, which is not a commit SHA")
            argv += ["-f", f"head_sha={head_sha}"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)
        except FileNotFoundError:
            raise PortError("gh not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise PortError(f"gh api {path} timed out after {_TIMEOUT}s") from None
        if r.returncode != 0:
            raise PortError(f"gh api {path} failed: {r.stderr.strip()[:160]}")
        try:
            pages = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            raise PortError(f"gh api {path} returned unparseable JSON: {exc}") from None
        if not isinstance(pages, list):
            raise PortError(f"gh api {path} --slurp did not return a page list")
        return pages

    def _collect(self, pages: list, key: str, where: str) -> list[dict]:
        """Flatten `key` across every page and PROVE the aggregate against `total_count`.

        The count is taken from the first page and compared to the total collected, so a truncated
        or malformed page cannot pass as a complete answer. Short read → `PortError` → `ST-7`:
        "this run is absent" must never be able to mean "this run was on a page nobody read".
        """
        if not pages:
            raise PortError(f"{where} returned no pages at all")
        out: list[dict] = []
        for page in pages:
            if not isinstance(page, dict):
                raise PortError(f"{where} returned a page that is not an object")
            items = page.get(key)
            if items is None or not isinstance(items, list):
                raise PortError(f"{where} returned a page with no `{key}` list")
            out += [x for x in items if isinstance(x, dict)]
        total = pages[0].get("total_count")
        if isinstance(total, int) and len(out) != total:
            raise PortError(f"{where} collected {len(out)} of {total} `{key}` across {len(pages)} "
                            f"page(s) — the read is incomplete and an unreturned page is not proof "
                            f"of absence")
        return out

    def pull(self, pr: int) -> dict:
        """Merge facts for ONE pull request. Returns exactly the FIVE keys §4.3a reads.

        `base_sha` is the fifth and it is not cosmetic. `created.base_sha` is written by the agent
        into the lifecycle, OUTSIDE `D`, and it selects which commit's registry supplies the required
        set. Left unchecked, an agent could name an older, weaker registry commit and the acceptance
        bar would follow it. The platform's own `base.sha` is the external anchor that claim is
        checked against; the caller refuses before reading the registry when they disagree.
        """
        pages = self._api("pulls", str(int(pr)))
        d = pages[0] if pages else None
        if not isinstance(d, dict):
            raise PortError(f"gh api pulls/{pr} did not return an object")
        return {"pr_head": (d.get("head") or {}).get("sha") or "",
                "base_sha": (d.get("base") or {}).get("sha") or "",
                "merge_sha": d.get("merge_commit_sha") or "",
                "merged_at": d.get("merged_at") or "", "merged": bool(d.get("merged"))}

    def check_runs(self, sha: str) -> list[dict]:
        """Every check run bound to `sha`, with the facts provenance and chronology need.

        A NAME IS NOT PROVENANCE. Any App with checks:write can create a check run called
        `unit (fast, no toolchain)`; matching on the name alone accepts a green tick that GitHub
        Actions never produced. So the producing App identity travels with every run, and the caller
        pins it. The server timestamps travel too: without them "a later rerun" is a claim about
        ordering with no time in it, and ordering inferred from numeric ids alone is not chronology.
        """
        if not _is_sha(sha):
            raise PortError(f"{sha!r} is not a 40-character commit SHA")
        runs = self._collect(self._api("commits", sha, "check-runs"), "check_runs",
                             f"check-runs for {sha[:12]}")
        return sorted(({"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                        "conclusion": str(c.get("conclusion") or ""),
                        "status": str(c.get("status") or ""),
                        "started_at": str(c.get("started_at") or ""),
                        "completed_at": str(c.get("completed_at") or ""),
                        "app_id": str((c.get("app") or {}).get("id") or ""),
                        "app_slug": str((c.get("app") or {}).get("slug") or "")}
                       for c in runs), key=lambda r: (r["name"], r["id"]))

    def workflow_runs(self, sha: str) -> list[dict]:
        """The Actions runs at `sha`, each carrying the workflow PATH it was produced from.

        The path is what the registry pins a required context to. Reading it here is what makes
        "this context came from the workflow that is supposed to produce it" checkable at all.
        """
        if not _is_sha(sha):
            raise PortError(f"{sha!r} is not a 40-character commit SHA")
        runs = self._collect(self._api("actions", "runs", head_sha=sha), "workflow_runs",
                             f"workflow runs at {sha[:12]}")
        return [{"id": str(r.get("id") or ""), "path": str(r.get("path") or ""),
                 "head_sha": str(r.get("head_sha") or "")} for r in runs]

    def jobs(self, run_id: str) -> list[dict]:
        """One workflow run's jobs. `check_run_url` is the DOCUMENTED join back to a check run.

        NOT `job.id == check_run.id`. That equality happens to hold today and is documented nowhere,
        so a verifier resting on it rests on a coincidence the platform never promised. `check_run_url`
        is a published field whose whole purpose is to name the check run a job produced; the join is
        made by parsing the id off the END of that URL, and the URL's own prefix is required to match
        this repository so a foreign run cannot be joined in.
        """
        if not str(run_id).isdigit():
            raise PortError(f"{run_id!r} is not a workflow run id")
        jobs = self._collect(self._api("actions", "runs", str(run_id), "jobs"), "jobs",
                             f"jobs for run {run_id}")
        out = []
        for j in jobs:
            url = str(j.get("check_run_url") or "")
            m = _CHECK_RUN_URL.match(url)
            if url and (not m or m.group(1) != self.slug):
                raise PortError(f"job {j.get('name')!r} names a check run outside {self.slug}: {url}")
            out.append({"name": str(j.get("name") or ""), "run_id": str(j.get("run_id") or ""),
                        "check_run_id": m.group(2) if m else "",
                        "conclusion": str(j.get("conclusion") or ""),
                        "status": str(j.get("status") or ""),
                        "started_at": str(j.get("started_at") or ""),
                        "completed_at": str(j.get("completed_at") or "")})
        return out


def _yaml(raw: bytes, what: str) -> dict:
    """Parse YAML, or raise `PortError`. PyYAML is a DECLARED dependency (`[dev]`), not a lucky one.

    It previously reached this module only as a third-order dependency of `vcrpy`, so the code that
    reads this repository's governance authority was one unrelated dev-dependency bump away from
    being unable to read it. A dependency load-bearing for a verdict is declared where it is used.
    """
    try:
        import yaml
    except Exception as exc:                                  # pragma: no cover - declared in [dev]
        raise PortError(f"{what} is unreadable ({type(exc).__name__}: {exc}); PyYAML is declared in "
                        f"the `dev` extra — install with `pip install -e '.[dev]'`") from None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise PortError(f"{what} is unparseable at the pinned commit: {exc}") from None
    if not isinstance(doc, dict):
        raise PortError(f"{what} did not parse to a mapping at the pinned commit")
    return doc


def required_contexts_at(raw: bytes) -> list[str]:
    """The required set PINNED to the contract's base commit, parsed from the in-repo registry.

    NOT live branch protection. Live protection is present-day configuration: reading it here would
    mean relaxing a setting tomorrow could retroactively invalidate an acceptance recorded today, or
    manufacture one that was never earned. A verdict about the past must rest on evidence that is
    itself fixed in the past, and a git blob at a named commit is exactly that.

    `intended_required_contexts` is deliberately NOT consulted — it is an aspiration, and a bar that
    was never live cannot be the bar a past merge had to clear.
    """
    doc = _yaml(raw, CI_REGISTRY_PATH)
    if REQUIRED_CONTEXTS_KEY not in doc:
        raise PortError(f"{CI_REGISTRY_PATH} has no `{REQUIRED_CONTEXTS_KEY}` at the pinned commit")
    ctx = doc.get(REQUIRED_CONTEXTS_KEY) or []
    if not isinstance(ctx, list) or not ctx:
        raise PortError(f"`{REQUIRED_CONTEXTS_KEY}` is empty at the pinned commit — a merge with no "
                        f"required context proves nothing about required CI")
    # Each required context is mapped to the (workflow path, job key) the registry says produces it.
    # Without that mapping a context is only a NAME, and a name is author-controlled: the provenance
    # chain has nothing to check the joined workflow run against.
    by_name = {}
    for c in doc.get("controls") or []:
        if isinstance(c, dict) and c.get("name") and c.get("workflow") and c.get("job"):
            by_name.setdefault(str(c["name"]), (str(c["workflow"]), str(c["job"])))
    wanted = sorted(str(c) for c in ctx)
    missing = [c for c in wanted if c not in by_name]
    if missing:
        raise PortError(f"the registry at the pinned commit names required context(s) "
                        f"{', '.join(missing)} with no control declaring their workflow and job, so "
                        f"their provenance cannot be checked")
    return wanted, {c: by_name[c] for c in wanted}


def workflow_job_name(raw: bytes, job_key: str) -> str:
    """The DISPLAY NAME of `job_key` in a workflow blob — the value a check run carries as its name.

    The registry pins a job KEY; the platform reports a job's display name. This is the only place
    the two are reconciled, and it is done against the workflow blob itself rather than assumed, so
    renaming a job's display name without updating the registry breaks the chain instead of quietly
    binding a required context to a different job.
    """
    doc = _yaml(raw, "the governing workflow")
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or job_key not in jobs:
        raise PortError(f"the governing workflow declares no job `{job_key}`")
    spec = jobs.get(job_key)
    name = spec.get("name") if isinstance(spec, dict) else None
    return str(name) if name else job_key


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
