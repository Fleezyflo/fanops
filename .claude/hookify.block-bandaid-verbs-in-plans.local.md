---
name: block-bandaid-verbs-in-plans
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.(prd|plan)\.md$|/prds/|/plans/
  - field: content
    operator: regex_match
    pattern: make (it |them |every )?(collapse )?visible|un-?hide|surface the (bad|stale|broken)
---
BLOCKED: band-aid verb in a plan. make-visible/un-hide describe guarding a bad state, not removing it. Restate the milestone as the CLASS made impossible ('X can no longer be constructed' / 'X is now an explicit labeled state'). (sweep/reconcile/harden were dropped — they are legit FanOps domain verbs and collided.)
