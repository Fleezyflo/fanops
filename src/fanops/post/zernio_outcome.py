"""The typed result of ONE Zernio create attempt (POST /posts). PRIVATE to the Zernio backend: it never
crosses the Poster protocol, which stays `publish(led, post_id) -> Ledger` for EVERY backend. postiz.py,
dryrun.py and post/__init__.py neither import nor know about this module, and must stay that way — an
unrelated backend does not pay for a Zernio contract quirk (report 11 §4/§10, invariant I-11).

Why a type instead of each HTTP branch mutating post.state inline: it separates "what Zernio said" from
"what the ledger becomes", so the 409 rule (never `failed`) and the candidate rule (never `submission_id`)
each live at exactly ONE mapping site — ZernioPoster.publish — reviewable in one place. The pre-fix code
let every branch set state itself, which is how a 409 fell through to `failed` (report 11 R-3): the rule
had no owner."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Created:
    """Zernio created a NEW post. post_id is a real backend id."""
    post_id: str


@dataclass(frozen=True, slots=True)
class IdempotentReplay:
    """Zernio recognised our x-request-id and returned the ORIGINAL post (HTTP 200 + `existingPost`) — the
    SAME logical submission as Created. Publication semantics are identical, so it maps to the identical
    ledger state; it is a distinct type only so the replay is auditable in the log."""
    post_id: str


@dataclass(frozen=True, slots=True)
class ReconciliationRequired:
    """Disposition UNKNOWN: never success, never terminal. candidate_post_id is an UNPROVEN pointer from a
    duplicate/ambiguity signal (a 409's details.existingPostId) — EVIDENCE ONLY, never an identity. Zernio
    is a hosted SCHEDULER, so a 409 proves only that Zernio holds a matching record: not social-platform
    publication, not ownership by THIS FanOps record, not completion (report 11 §3)."""
    reason: str
    evidence: str
    candidate_post_id: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalFailure:
    """Provably not accepted — nothing reached Zernio, or Zernio rejected it with a verdict re-sending
    cannot change. The only result safe to mark `failed`, which is RE-QUEUEABLE (so a wrong terminal here
    is a double-post, which is why every may-have-landed boundary returns ReconciliationRequired)."""
    reason: str
    evidence: str


ZernioCreateResult = Created | IdempotentReplay | ReconciliationRequired | TerminalFailure
