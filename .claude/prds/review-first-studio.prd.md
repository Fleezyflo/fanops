# Review-First Studio — a usable content cockpit

## Problem
FanOps's pipeline works, but its operator surface does not: when the operator opens the Studio to
actually use it, they land on screens that expose the machinery — a form asking them to *write* the
hook/caption, raw prompt text, a layout that "looks like the backend, not the frontend." It reads as a
developer debug console, not a product. The per-clip work is not auto-prepared and handed over for
review; instead the human is asked to do the machine's job, and there is no way to edit what the system
produced and ask for another take. Left unsolved, the system is shelfware: the engine runs, but the
person it was built for cannot operate it — and neither could anyone non-technical.

## Evidence
- Operator, using the live Studio this session (primary evidence — the actual user, n=1):
  - *"I open the UI… it looks like the backend not the frontend… it's not something I can understand or anyone else for that matter."*
  - *"it asked me to write some hook or text on the screen"* — i.e. the Gates surface presented a caption-writing form + prompt rather than a finished clip to review.
  - Desired behavior, in their words: *"I can change whatever was added by the system when I'm reviewing… input information in the fields the system created and filled… change that information… press regenerate and it goes through that and gives it to me again."*
- Corroborating observation: the Review screen showed *"Nothing in the ledger yet"* despite 57 prepared clips, because the review surface is built around posts/gates, not around finished, reviewable content.

## Users
- **Primary**: the solo operator running the artist's fan accounts — **non-technical**. Wants to review and refine finished content, not operate a pipeline. Should never see a prompt, a raw form, or a terminal.
- **Also**: any non-technical person the operator hands it to ("or anyone else for that matter").
- **Not for**: developers driving the CLI (that path already exists and stays); not a multi-tenant/SaaS product.

## Hypothesis
We believe a **review-first Studio** — where ingested content is auto-prepared end-to-end (clip +
caption + every configurable field filled by the system) and presented as **editable, pre-filled cards
with a regenerate loop**, flowing review → schedule → publish in one interface — will make FanOps usable
by a non-technical operator and remove the manual work.
We'll know we're right when the operator can take a batch from ingest to scheduled posts **entirely in
the UI** — editing or regenerating any field they don't like — **without ever seeing a prompt, a raw
form, or a terminal.**

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| Operator completes ingest → scheduled with zero CLI use and zero gate/prompt forms seen | Yes | Operator walkthrough of one real batch |
| Every system-generated field on a piece is editable in-place | 100% of fields | UI audit per content type |
| "Regenerate" returns an updated piece reflecting the operator's edits/config | Works for the fields the operator can edit | Operator test: edit a field, regenerate, confirm change |
| Operator (and one non-technical bystander) rates the UI "usable without help" | Both yes | Qualitative — direct ask |

## Scope
**MVP** — the full path, in one usable UI (operator chose "full flow, first version"):
1. **Auto-prepare**: ingested content runs all the way to review-ready (clipped, captioned, all
   configurable fields filled) with **no human gate** — the system answers its own moment/caption gates.
2. **Review-and-edit**: each piece is shown as a finished clip plus every system-filled field as an
   **editable input**, pre-populated. No prompts, no raw forms.
3. **Regenerate**: the operator changes a field / configurable input and hits **Regenerate**; the system
   re-runs that piece and returns the new version.
4. **Schedule**: approved pieces move into a visible schedule from the review screen.
5. **Publish**: a publish path exists (dryrun first; real publishing is a later milestone).

**Out of scope**
- **Continuous always-on engine (cron/launchd daemon, `fanops autopilot`'s recurring mode)** — cadence is occasional batches, so a forever background agent is not the operator's path. Existing daemon/autopilot work is retained only as optional plumbing for the *eventual* auto-publish-on-schedule, not the main flow.
- **Blotato** — dropped by the operator; not a dependency anywhere in this product.
- **Multi-user / multi-artist / hosted SaaS** — single operator, local.
- **Going fully live (real publishing wiring, e.g. self-hosted Postiz)** — "eventually"; a later milestone, gated by the operator.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Auto-prepare | Ingested content reaches "review-ready" (clipped + captioned + fields filled) with zero human gates — the operator never writes a caption | pending | — |
| 2 | Review-and-edit cockpit | Operator sees finished clips, every system-filled field is editable in a clean screen (no prompts/forms/dev text) | pending | — |
| 3 | Regenerate loop | Operator edits a field/config and regenerates a single piece, gets the updated result back | pending | — |
| 4 | Schedule in the UI | Approved pieces flow into a visible schedule without touching the CLI | pending | — |
| 5 | Publish path | Approved/scheduled content ships (dryrun first; real poster later, operator-gated) | pending | — |

## Open Questions
- [ ] Which fields are "configurable" per piece, exactly? (caption, on-screen hook, which moment/cut, aspect ratio, scheduled time, target accounts/platforms?) — needs enumeration before /plan.
- [ ] Regenerate granularity: per-field (regenerate just the caption) vs. whole-piece (re-cut + re-caption)? Cost and latency differ a lot (each regenerate is a `claude` call).
- [ ] Does "full flow, first version" realistically fit one build, or should milestones 1–5 ship incrementally behind a usable slice? (Scope-vs-time tension — flagged as a risk.)
- [ ] What is the actual publishing target post-Blotato (self-hosted Postiz vs. manual queue), and when does it become real vs. dryrun?
- [ ] How "finished" must a clip look in review — embedded video player, poster thumbnail, both? (Studio already embeds players; confirm sufficiency.)
- [ ] Is the current Studio (server-rendered) the right foundation to make "feel like a real app," or does the front end need a different approach? — assess in /plan, not here.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| "Full flow, first version" is a large surface → big-bang build that slips | High | High | Stage by milestone; ship milestones 1–2 (auto-prepare + review-edit) as the first usable slice, prove usability before 3–5 |
| Making it "feel like a real product" needs real front-end work the current server-rendered Studio can't carry | Medium | High | Assess the Studio's UI foundation in /plan; treat "usable UI" as a first-class requirement, not a restyle |
| Evidence is n=1 | Low | Low | It is the actual operator of a single-operator product — sufficient; re-validate with one non-technical bystander |
| Regenerate loop = unbounded `claude` cost | Medium | Medium | Prefer per-field regenerate to bound each action; show the operator what a regenerate will re-run |
| Prior session built daemon/autopilot that this PRD largely sidelines | Done | Low | Keep as optional auto-publish-on-schedule plumbing (milestone 5+); do not expand it |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
