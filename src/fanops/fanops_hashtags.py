# src/fanops/fanops_hashtags.py
"""M4 offline core — own-reach hashtag intelligence (finding #7: hashtags update from the visibility
they actually give US). rank_tags_by_reach ranks tags by mean reach-per-post over the ledger's analyzed
posts, attributing `post.hashtags <-> post.metrics["reach"]` on ONE entity (audit H2, no clip/surface
join). refresh_store writes the reach-ranked 00_control/hashtags.json store — but ONLY when the F2
learn-doctor verdict is PASS (if the reach analytics label does not reconcile, reach is garbage-in, so
we refuse to write and the frozen pools stand). The live Meta Graph TREND fetch (ig_hashtag_search +
top_media) + its 30/7-day budget is a DEFERRED, operator-gated follow-up — not built here because it
needs a real IG Business token + app-review and cannot be unit-tested (the cutover-harness precedent)."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.hashtags import _norm, vetted_menu

# The F2 learn-doctor persists its tri-state verdict here (00_control/learn_doctor.json). M4 reads the
# FILE directly (not learn_doctor.load_verdict) so the reach-attribution gate is decoupled from that
# module — the same soft-coupling-via-a-known-file the tuning.json / cutover.json contracts use. Absent
# / corrupt / not-PASS -> reach is treated as unvalidated and refresh writes nothing.
def _doctor_verdict(cfg: Config):
    p = cfg.control / "learn_doctor.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return d.get("verdict") if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def rank_tags_by_reach(led: Ledger) -> list[str]:
    """Tags ordered by mean reach-per-post (desc) over ANALYZED posts. H2: read reach + hashtags off the
    SAME Post — no join. A post without a numeric `reach` or with no hashtags contributes nothing. Pure."""
    totals: dict[str, list[float]] = {}              # tag -> [reach_sum, post_count]
    for p in led.posts.values():
        if p.state is not PostState.analyzed:
            continue
        reach = (p.metrics or {}).get("reach")
        if not isinstance(reach, (int, float)) or isinstance(reach, bool):
            continue
        for raw in (p.hashtags or []):
            h = _norm(raw) if isinstance(raw, str) else ""
            if not h:
                continue
            agg = totals.setdefault(h, [0.0, 0.0])
            agg[0] += float(reach); agg[1] += 1
    return [t for t, _ in sorted(totals.items(), key=lambda kv: kv[1][0] / kv[1][1], reverse=True)]


def refresh_store(led: Ledger, cfg: Config, *, get=None, now=None) -> dict:
    """Recompute + write the reach-ranked tag store — GATED on the learn-doctor PASS verdict. Not PASS
    (FAIL / NO-DATA / never run) -> write NOTHING and report why (reach is untrustworthy until the label
    reconciles). On PASS the rank is OWN-REACH first (the accurate, owned, rate-limit-free signal), then
    LIVE Meta Graph TREND tags (opt-in via FANOPS_HASHTAG_TRENDS + a wired Meta app — fail-open: no flag /
    no token / a fetch miss -> trends simply absent), then the frozen seed so a never-posted/never-trending
    tag still appears. Returns a summary dict (never raises on a clean run)."""
    verdict = _doctor_verdict(cfg)
    if verdict != "PASS":
        return {"written": False, "verdict": verdict, "reason": "learn-doctor not PASS — reach is unreliable until the analytics label reconciles"}
    own = rank_tags_by_reach(led)
    seed = vetted_menu()
    trends: dict = {}
    if cfg.hashtag_trends:                            # opt-in live trend sampling (budget-bounded, fail-open)
        from fanops.meta_graph import sample_trends
        candidates = [t for t in (own + seed)]       # ask about owned + frozen-seed tags within budget
        trends = sample_trends(cfg, candidates, get=get, now=now)
    merged: list = []; seen: set = set()
    for t in own:                                    # PRIMARY: our own measured reach
        if t not in seen: seen.add(t); merged.append(t)
    for t in sorted(trends, key=lambda k: trends[k], reverse=True):  # SECONDARY: trending, not yet owned
        if t not in seen: seen.add(t); merged.append(t)
    for t in seed:                                   # LAST: frozen seed so the menu is never empty/narrow
        if t not in seen: seen.add(t); merged.append(t)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": merged}, indent=2))
    return {"written": True, "verdict": "PASS", "own_ranked": len(own),
            "trend_sampled": len(trends), "total": len(merged)}


def cmd_hashtags_refresh(cfg: Config) -> int:
    """`fanops hashtags refresh` — recompute the reach-ranked store from analyzed posts (doctor-gated) +
    optional live Meta Graph trend sampling (FANOPS_HASHTAG_TRENDS). Read-only of the ledger; writes ONLY
    00_control/hashtags.json. Always exits 0."""
    led = Ledger.load(cfg)
    r = refresh_store(led, cfg)
    if r.get("written"):
        trend = f" + {r['trend_sampled']} trend-sampled" if r.get("trend_sampled") else ""
        print(f"hashtags store refreshed: {r['own_ranked']} own-reach{trend} + frozen seed = {r['total']} tags (00_control/hashtags.json)")
    else:
        print(f"hashtags refresh SKIPPED: learn-doctor verdict={r.get('verdict')!r} — run `fanops learn doctor`; reach is unreliable until PASS.")
    return 0
