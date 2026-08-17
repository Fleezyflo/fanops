# src/fanops/tag_outcomes.py
"""Selection-only own-outcome table: (platform, account, tag) → {n, p50}.

Built from PostState.analyzed posts. Used by vet_hashtags when n ≥ OUTCOME_MIN_N for THIS
account+platform; otherwise selection stays on size_rank_key. No (platform, tag) rollup — that
launders one account's death into another's menu. Not a scoring module and not listed among
the learning files. YouTube is not trained. Sidecar is cfg.control / "tag_outcomes.json"
(no config.py path). Fail-open everywhere."""
from __future__ import annotations
import json
import statistics
from fanops.controlio import write_json_atomic
from fanops.errors import fail_open
from fanops.models import Platform, PostState

OUTCOME_MIN_N = 4
TAG_OUTCOMES_NAME = "tag_outcomes.json"


def tag_outcomes_path(cfg):
    """Sidecar path. Not a Config field — callers must not add one."""
    return cfg.control / TAG_OUTCOMES_NAME


def _norm(tag: str) -> str:
    if not tag: return ""
    t = tag.strip().lower().lstrip("#").strip()
    return f"#{t}" if t else ""


def _num(v) -> float | None:
    """Non-negative number, or None. Bools are never numbers here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return None
    return float(v)


def _post_metric(metrics) -> float | None:
    """Per-post outcome: numeric views, else numeric reach. Else skip."""
    if not isinstance(metrics, dict):
        return None
    v = _num(metrics.get("views"))
    if v is not None:
        return v
    return _num(metrics.get("reach"))


def _platform_value(platform) -> str:
    if platform is None:
        return ""
    return platform.value if hasattr(platform, "value") else str(platform)


def lookup_outcome(table, platform, account, tag) -> dict | None:
    """One (platform, account, normalized_tag) row, or None. Never a cross-account rollup."""
    plat = _platform_value(platform)
    acc = account if isinstance(account, str) else ""
    if not plat or not acc or not isinstance(table, dict):
        return None
    by_acc = table.get(plat)
    if not isinstance(by_acc, dict):
        return None
    by_tag = by_acc.get(acc)
    if not isinstance(by_tag, dict):
        return None
    rec = by_tag.get(_norm(tag) if isinstance(tag, str) else "")
    return rec if isinstance(rec, dict) else None


def qualifies(row) -> bool:
    """True when this row may replace size rank (n ≥ OUTCOME_MIN_N)."""
    if not isinstance(row, dict):
        return False
    n = row.get("n")
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n < OUTCOME_MIN_N:
        return False
    return _num(row.get("p50")) is not None


def _clean_table(raw) -> dict:
    """Keep only (platform → account → tag → {n, p50}). Drop anything else."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for plat, by_acc in raw.items():
        if not isinstance(plat, str) or not plat or not isinstance(by_acc, dict):
            continue
        accs: dict = {}
        for acc, by_tag in by_acc.items():
            if not isinstance(acc, str) or not acc or not isinstance(by_tag, dict):
                continue
            tags: dict = {}
            for tag, rec in by_tag.items():
                ntag = _norm(tag) if isinstance(tag, str) else ""
                if not ntag or not isinstance(rec, dict):
                    continue
                n = rec.get("n")
                p50 = _num(rec.get("p50"))
                if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 1 or p50 is None:
                    continue
                tags[ntag] = {"n": int(n), "p50": p50}
            if tags:
                accs[acc] = tags
        if accs:
            out[plat] = accs
    return out


def load_tag_outcomes(cfg) -> dict:
    """Read the sidecar. Missing / corrupt / unreadable → {}. Never raises."""
    table: dict = {}
    with fail_open("tag_outcomes.load"):
        if cfg is None:
            return {}
        p = tag_outcomes_path(cfg)
        if not p.exists():
            return {}
        raw = json.loads(p.read_text())
        table = _clean_table(raw)
    return table


def _build_table(led) -> dict:
    buckets: dict[tuple[str, str, str], list[float]] = {}
    posts = led.posts.values() if led is not None else ()
    for p in posts:
        if getattr(p, "state", None) is not PostState.analyzed:
            continue
        plat = _platform_value(getattr(p, "platform", None))
        if not plat or plat == Platform.youtube.value:
            continue
        acc = getattr(p, "account", None)
        if not isinstance(acc, str) or not acc:
            continue
        metric = _post_metric(getattr(p, "metrics", None))
        if metric is None:
            continue
        for tag in getattr(p, "hashtags", None) or []:
            ntag = _norm(tag) if isinstance(tag, str) else ""
            if not ntag:
                continue
            buckets.setdefault((plat, acc, ntag), []).append(metric)
    table: dict = {}
    for plat, acc, tag in sorted(buckets):
        vals = buckets[(plat, acc, tag)]
        table.setdefault(plat, {}).setdefault(acc, {})[tag] = {
            "n": len(vals), "p50": float(statistics.median(vals)),
        }
    return table


def refresh_tag_outcomes(cfg, led) -> dict:
    """Rewrite the sidecar from analyzed posts. Whole-file, idempotent. Never raises."""
    table: dict = {}
    with fail_open("tag_outcomes.refresh"):
        table = _build_table(led)
        write_json_atomic(tag_outcomes_path(cfg), table)
    return table
