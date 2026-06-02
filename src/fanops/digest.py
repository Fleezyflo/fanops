"""Human-readable digest: unit counts by state, brand-risk holds, FAILURES (posts in failed +
units in error — FIX F51), and the agent steps awaiting a response."""
from __future__ import annotations
from collections import Counter
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.agentstep import pending

def _counts(units) -> str:
    c = Counter(u.state.value for u in units)
    return "".join(f"  - {s}: {n}\n" for s, n in sorted(c.items())) or "  (none)\n"

def render_digest(led: Ledger, cfg: Config) -> str:
    out = ["# FAN OPS Ledger Digest\n"]
    out.append(f"\n**Sources** ({len(led.sources)}):\n" + _counts(led.sources.values()))
    out.append(f"\n**Moments** ({len(led.moments)}):\n" + _counts(led.moments.values()))
    out.append(f"\n**Clips** ({len(led.clips)}):\n" + _counts(led.clips.values()))
    out.append(f"\n**Posts** ({len(led.posts)}):\n" + _counts(led.posts.values()))

    holds = [f"- clip `{c.id}` (moment {c.parent_id}): {c.held_reason or '(no reason given)'}"
             for c in led.clips.values() if c.held]
    if holds:
        out.append("\n## Brand-risk holds (need Moh)\n" + "\n".join(holds) + "\n")

    fails = ([f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
              for p in led.posts.values()
              if p.state in (PostState.failed, PostState.error)] +          # M4: error too
             [f"- {kind} `{u.id}`: {u.error_reason or '(no reason given)'}"
              for kind, store in (("source", led.sources), ("moment", led.moments),
                                  ("clip", led.clips))
              for u in store.values() if u.state.value == "error"])         # M3: drop getattr
    if fails:
        out.append("\n## Failures (need attention)\n" + "\n".join(fails) + "\n")

    # Needs reconcile (AUDIT C1): an ambiguous publish failure (5xx / network timeout after the
    # body was sent) — the post MAY be live on the platform. It is deliberately NOT in Failures
    # (re-queueing a failed post is safe; re-queueing this one could double-post). Surface it on
    # its own so the operator verifies via GET /v2/posts/:id (or my.blotato.com/failed) before any
    # resubmit. This is a manual step by design — there is no idempotency key to make it automatic.
    reconcile = [f"- post `{p.id}` ({p.platform.value}): {p.error_reason or '(no reason given)'}"
                 for p in led.posts.values() if p.state is PostState.needs_reconcile]
    if reconcile:
        out.append("\n## Needs reconcile (may be live — verify before resubmit)\n"
                   + "\n".join(reconcile) + "\n")

    # Published but never measured: track.py flips published->analyzed only when a metrics row
    # matches by submission_id, so a post that shipped but Blotato never returned metrics for
    # stays 'published' with empty metrics forever. Surface it so the operator notices (the
    # one stuck-state the pipeline can't auto-resolve — you can't fabricate metrics).
    unmeasured = [f"- post `{p.id}` ({p.platform.value}): published, no metrics yet"
                  for p in led.posts.values()
                  if p.state is PostState.published and not p.metrics]
    if unmeasured:
        out.append("\n## Published but unmeasured (shipped, never measured)\n"
                   + "\n".join(unmeasured) + "\n")

    awaiting = ([f"- moments: {k}" for k in pending(cfg, kind="moments")] +
                [f"- captions: {k}" for k in pending(cfg, kind="captions")])
    if awaiting:
        out.append("\n## Awaiting agent (request written, no response yet)\n"
                   + "\n".join(awaiting) + "\n")

    # E3: an explicit "Pending agent gates" section (the word 'pending' is the searchable signal a
    # monitor/operator greps for) — same per-kind list as Awaiting, gated on the same pending keys
    # so an empty ledger renders neither. These are the gates a responder has NOT yet cleared.
    pend = ([f"- moments: {k}" for k in pending(cfg, kind="moments")] +
            [f"- captions: {k}" for k in pending(cfg, kind="captions")])
    if pend:
        out.append("\n## Pending agent gates (responder has not cleared)\n"
                   + "\n".join(pend) + "\n")
    return "".join(out)

def write_digest(led: Ledger, cfg: Config) -> None:
    cfg.digest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.digest_path.write_text(render_digest(led, cfg))
