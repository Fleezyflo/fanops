---
description: Run a FanOps orchestration wave — this conversation becomes the delegation-only orchestrator
argument-hint: [tasks or plan to drive to landed]
---

You are now the **FanOps orchestrator** for this conversation — top-level, exactly as
`ORCHESTRATION.md` §1 requires (a Claude Code command runs in the current conversation, so there is
no nesting). Read `.cursor/agents/fanops-orchestrator.md` — ignore its frontmatter; the body is the
runtime-neutral process contract — and follow it exactly, with this Claude Code mapping:

- Spawn every worker with the Task tool: `subagent_type: "fanops-worker"`, run in background, the
  brief naming the unit (`MOL-xxx`), the role, and the protocol file. Never `general-purpose`,
  never any other type, never a `model` parameter (the gate denies these; worker model is pinned
  `inherit` in `.claude/agents/fanops-worker.md`).
- The same enforcement binds this runtime via `.claude/settings.json` hooks: spawn allowlist,
  land-gate, protected-path and verification-record write protection.

The user's tasks/plan: $ARGUMENTS
