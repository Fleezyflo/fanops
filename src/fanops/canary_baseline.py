"""Canary baseline subsystem — read-only, non-disclosive multilayer ledger manifest capture + compare.

Extracted from canary.py (SA-C8-2). Public callers should continue to use `fanops.canary`; this module is
the implementation home for baseline capture/compare and the pinned baseline contract.
"""
from __future__ import annotations
import hashlib, json, re, sqlite3, subprocess
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from fanops.studio.actions_common import ActionResult

from fanops.config import Config
from fanops.log import get_logger
from fanops.models import is_real_submission_id
from fanops.canary_identity import _canon, _is_hex64, _sha256_text


BASELINE_FORMAT_VERSION = "1"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# --- the PINNED baseline contract. _validate_baseline_shape holds a supplied baseline to EXACT equality
# against these sets, so an extra/missing/renamed field is an error rather than a silently-clean diff. ---
_BASELINE_TOP_KEYS = frozenset({"format_version", "status", "canonicalization", "schema_version",
                                "repo_commit", "post_count", "state_distribution", "digests",
                                "per_post_manifest", "per_post_layers", "frozen_incident"})
_BASELINE_STATUS = "candidate"
_BASELINE_CANONICALIZATION = {
    "json": "sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False",
    "row_order": "sorted by row_id",
    "aggregate": "sha256 of concat(row_id + 0x00 + blob + 0x1e)",
    "hash": "sha256",
}
_BASELINE_DIGEST_KEYS = frozenset({"raw_posts", "safety_critical", "scheduling", "content", "manifest"})
_SAFE_LAYER_KEYS = frozenset({"state", "account", "platform", "aspect", "has_public_url", "has_media_urls",
                              "has_submission_id", "is_real_submission_id", "has_reconcile_candidate",
                              "has_published_at", "account_id_sha256", "parent_id_sha256",
                              "submission_id_sha256", "reconcile_candidate_id_sha256", "public_url_sha256",
                              "media_urls_sha256", "error_reason_sha256", "published_at_sha256",
                              "created_at_sha256"})
_SAFE_BOOL_KEYS = frozenset({"has_public_url", "has_media_urls", "has_submission_id", "is_real_submission_id",
                             "has_reconcile_candidate", "has_published_at"})
_SAFE_CATEGORICAL_KEYS = frozenset({"state", "account", "platform", "aspect"})
_SCHED_LAYER_KEYS = frozenset({"scheduled_time_sha256", "approval"})
_CONTENT_LAYER_KEYS = frozenset({"caption_sha256", "hashtags_sha256", "parent_id_sha256", "aspect",
                                 "media_urls_sha256", "media_id_sha256"})
_FROZEN_INCIDENT_IDS = ("post_04b29c9f7f2d", "post_07e45c69ac0d", "post_0943840705ce", "post_0a12cff53619")
_FROZEN_ENTRY_KEYS = frozenset({"raw_sha256", "state", "submission_id_sha256",
                                "reconcile_candidate_id_sha256", "public_url_sha256", "has_public_url"})


def _ok(detail=None):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.success(detail)

def _err(msg):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.failure(msg)


def _read_posts_ro(cfg: Config):
    if not Path(cfg.ledger_path).exists():
        return [], None
    con = sqlite3.connect(f"file:{cfg.ledger_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT row_id,payload FROM ledger_rows WHERE map_name='posts'").fetchall()
        sv = con.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    finally:
        con.close()
    return rows, (sv[0] if sv else None)

def _sep_digest(pairs) -> str:
    h = hashlib.sha256()
    for rid, blob in pairs:
        h.update(rid.encode()); h.update(b"\x00"); h.update(blob.encode()); h.update(b"\x1e")
    return h.hexdigest()

def _field_hash(v) -> str:
    """sha256 of a value's canonical JSON — the change-detection primitive that discloses NOTHING of the value."""
    return _sha256_text(_canon(v))

def _post_layers(d: dict) -> dict:
    """Per-post comparison layers. Every URL / token / id / timestamp is a per-field HASH or a categorical
    presence flag — NO raw public_url, media_urls, submission_id, error_reason, caption ever appears."""
    state = d.get("state"); sid = d.get("submission_id")
    pub = d.get("public_url")
    safe = {
        "state": state, "account": d.get("account"), "platform": d.get("platform"), "aspect": d.get("aspect"),
        "has_public_url": bool((pub or "").strip()) if isinstance(pub, str) else bool(pub),
        "has_media_urls": bool(d.get("media_urls")),
        "has_submission_id": bool(sid),
        "is_real_submission_id": bool(is_real_submission_id(sid)),
        "has_reconcile_candidate": d.get("reconcile_candidate_id") is not None,
        "has_published_at": bool(d.get("published_at")),
        "account_id_sha256": _field_hash(d.get("account_id")),
        "parent_id_sha256": _field_hash(d.get("parent_id")),
        "submission_id_sha256": _field_hash(sid),
        "reconcile_candidate_id_sha256": _field_hash(d.get("reconcile_candidate_id")),
        "public_url_sha256": _field_hash(pub),
        "media_urls_sha256": _field_hash(d.get("media_urls") or []),
        "error_reason_sha256": _field_hash(d.get("error_reason")),
        "published_at_sha256": _field_hash(d.get("published_at")),
        "created_at_sha256": _field_hash(d.get("created_at")),
    }
    sched = {"scheduled_time_sha256": _field_hash(d.get("scheduled_time")), "approval": state}
    content = {"caption_sha256": _sha256_text(d.get("caption") or ""),
               "hashtags_sha256": _field_hash(d.get("hashtags") or []),
               "parent_id_sha256": _field_hash(d.get("parent_id")), "aspect": d.get("aspect"),
               "media_urls_sha256": _field_hash(d.get("media_urls") or []),
               "media_id_sha256": _field_hash(d.get("media_id"))}
    return {"safe": _canon(safe), "sched": _canon(sched), "content": _canon(content)}

def _layer_digests(manifest: dict, layers: dict) -> dict:
    """Recompute the three per-layer aggregates + the manifest aggregate from the per-post maps alone, in a
    stable sorted row order. Capture stores these; validation RECOMPUTES them and requires equality, so a
    tampered aggregate (or a corrupted raw-hash manifest) is detectable without ever storing raw payloads."""
    rids = sorted(manifest)
    return {
        "safety_critical": _sep_digest([(r, layers[r]["safe"]) for r in rids]),
        "scheduling": _sep_digest([(r, layers[r]["sched"]) for r in rids]),
        "content": _sep_digest([(r, layers[r]["content"]) for r in rids]),
        "manifest": _sep_digest([(r, manifest[r]) for r in rids]),
    }

def _repo_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent), timeout=5)
        out = r.stdout.strip()
        return out if (r.returncode == 0 and _COMMIT_RE.match(out)) else "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"

def _build_manifest(cfg: Config) -> dict:
    rows, sv = _read_posts_ro(cfg)
    parsed = sorted(((rid, blob, json.loads(blob)) for rid, blob in rows), key=lambda t: t[0])
    manifest = {rid: hashlib.sha256(blob.encode()).hexdigest() for rid, blob, _ in parsed}
    layers = {rid: _post_layers(d) for rid, _, d in parsed}
    dist = {}
    for _, _, d in parsed:
        dist[str(d.get("state"))] = dist.get(str(d.get("state")), 0) + 1
    incident = {}
    for i in _FROZEN_INCIDENT_IDS:
        for rid, _blob, d in parsed:
            if rid == i:
                # non-disclosive: raw-payload sha + state (categorical) + per-field hashes; NO raw url/token
                incident[i] = {"raw_sha256": manifest[i], "state": d.get("state"),
                               "submission_id_sha256": _field_hash(d.get("submission_id")),
                               "reconcile_candidate_id_sha256": _field_hash(d.get("reconcile_candidate_id")),
                               "public_url_sha256": _field_hash(d.get("public_url")),
                               "has_public_url": bool((d.get("public_url") or ""))}
    digests = {"raw_posts": _sep_digest([(rid, blob) for rid, blob, _ in parsed]), **_layer_digests(manifest, layers)}
    return {
        "format_version": BASELINE_FORMAT_VERSION,
        "status": _BASELINE_STATUS,               # ALWAYS candidate — capture never self-accepts
        "canonicalization": dict(_BASELINE_CANONICALIZATION),
        "schema_version": sv, "repo_commit": _repo_commit(), "post_count": len(parsed),
        "state_distribution": dist,
        "digests": digests,
        "per_post_manifest": manifest,            # rid -> sha256(raw payload)  (raw bytes never emitted)
        "per_post_layers": layers,                # rid -> {safe, sched, content} canon strings of per-field hashes
        "frozen_incident": incident,
    }

def _write_json_atomic(p: Path, obj) -> None:
    from fanops.controlio import write_json_atomic
    write_json_atomic(p, obj)

def capture_canary_baseline(cfg: Config, *, output: str) -> ActionResult:
    try:
        manifest = _build_manifest(cfg)
    except Exception as exc:
        get_logger(cfg)("canary", "baseline", "capture_failed", level="error", err=str(exc)[:140])
        return _err(f"baseline capture failed: {str(exc)[:140]}")
    out = Path(output).expanduser()
    _write_json_atomic(out, manifest)
    return _ok({"output": str(out), "status": _BASELINE_STATUS,
                                 "raw_posts": manifest["digests"]["raw_posts"],
                                 "post_count": manifest["post_count"]})


def _validate_layer(rid: str, name: str, blob, keys: frozenset) -> Optional[str]:
    if not isinstance(blob, str):
        return f"per_post_layers[{rid!r}].{name} is not a string"
    try:
        obj = json.loads(blob)
    except (ValueError, TypeError):
        return f"per_post_layers[{rid!r}].{name} is not JSON"
    if not isinstance(obj, dict):
        return f"per_post_layers[{rid!r}].{name} is not a JSON object"
    if set(obj) != set(keys):
        return (f"per_post_layers[{rid!r}].{name} field-set {sorted(obj)} != pinned {sorted(keys)}")
    if _canon(obj) != blob:
        return f"per_post_layers[{rid!r}].{name} is not byte-canonical JSON"
    for k, v in obj.items():
        if k.endswith("_sha256") and not _is_hex64(v):
            return f"per_post_layers[{rid!r}].{name}.{k} is not a lowercase sha256"
        if name == "safe" and k in _SAFE_BOOL_KEYS and not isinstance(v, bool):
            return f"per_post_layers[{rid!r}].safe.{k} is not a boolean"
        if name == "safe" and k in _SAFE_CATEGORICAL_KEYS and not (v is None or isinstance(v, str)):
            return f"per_post_layers[{rid!r}].safe.{k} is not a categorical string"
    return None


def _validate_baseline_shape(prior) -> Optional[str]:
    """Hold a supplied baseline to the PINNED contract with EXACT equality (not mere field presence). A null /
    missing / extra / renamed / mistyped key, a non-hash hash field, an inconsistent post_count, an altered
    canonicalization block, a missing frozen incident, or a recomputed-aggregate mismatch is an ERROR — never
    an apparently-clean comparison."""
    if not isinstance(prior, dict):
        return "baseline is not a JSON object"
    if set(prior) != set(_BASELINE_TOP_KEYS):
        missing, extra = sorted(_BASELINE_TOP_KEYS - set(prior)), sorted(set(prior) - _BASELINE_TOP_KEYS)
        return f"baseline top-level keys mismatch (missing={missing}, extra={extra})"
    if prior["format_version"] != BASELINE_FORMAT_VERSION:
        return f"baseline format_version {prior['format_version']!r} != {BASELINE_FORMAT_VERSION!r}"
    if prior["status"] != _BASELINE_STATUS:
        return f"baseline status {prior['status']!r} != {_BASELINE_STATUS!r}"
    if prior["canonicalization"] != _BASELINE_CANONICALIZATION:
        return "baseline canonicalization block does not equal the pinned metadata"
    if not (prior["schema_version"] is None or isinstance(prior["schema_version"], (int, str))):
        return "baseline schema_version is not an int/str/null"
    rc = prior["repo_commit"]
    if not (isinstance(rc, str) and (rc == "unknown" or _COMMIT_RE.match(rc))):
        return "baseline repo_commit is not a 40-hex commit or 'unknown'"
    digests, man, lay = prior["digests"], prior["per_post_manifest"], prior["per_post_layers"]
    if not isinstance(digests, dict) or set(digests) != set(_BASELINE_DIGEST_KEYS):
        return f"baseline digests key-set != pinned {sorted(_BASELINE_DIGEST_KEYS)}"
    for k, v in digests.items():
        if not _is_hex64(v):
            return f"baseline digest {k!r} is not a lowercase sha256"
    if not isinstance(man, dict) or not isinstance(lay, dict):
        return "baseline per_post_manifest / per_post_layers is not a map"
    if set(man) != set(lay):
        return "baseline per_post_layers keys != per_post_manifest keys"
    pc = prior["post_count"]
    if not (isinstance(pc, int) and not isinstance(pc, bool) and pc >= 0):
        return "baseline post_count is not a non-negative integer"
    if pc != len(man):
        return f"baseline post_count {pc} != len(per_post_manifest) {len(man)}"
    dist = prior["state_distribution"]
    if not isinstance(dist, dict):
        return "baseline state_distribution is not a map"
    total = 0
    for k, v in dist.items():
        if not isinstance(k, str) or not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            return "baseline state_distribution is not a string -> non-negative-integer map"
        total += v
    if total != pc:
        return f"baseline state_distribution sums to {total}, not post_count {pc}"
    for rid, hv in man.items():
        if not isinstance(rid, str) or not rid:
            return "baseline per_post_manifest has a non-string/empty Post ID"
        if not _is_hex64(hv):
            return f"baseline per_post_manifest[{rid!r}] is not a lowercase sha256"
    for rid, entry in lay.items():
        if not isinstance(entry, dict) or set(entry) != {"safe", "sched", "content"}:
            return f"baseline per_post_layers[{rid!r}] does not have exactly {{safe, sched, content}}"
        for name, keys in (("safe", _SAFE_LAYER_KEYS), ("sched", _SCHED_LAYER_KEYS), ("content", _CONTENT_LAYER_KEYS)):
            e = _validate_layer(rid, name, entry[name], keys)
            if e is not None:
                return e
    inc = prior["frozen_incident"]
    if not isinstance(inc, dict):
        return "baseline frozen_incident is not a map"
    for iid, entry in inc.items():
        if iid not in _FROZEN_INCIDENT_IDS:
            return f"baseline frozen_incident has an unexpected id {iid!r}"
        if not isinstance(entry, dict) or set(entry) != set(_FROZEN_ENTRY_KEYS):
            return f"baseline frozen_incident[{iid!r}] field-set != pinned {sorted(_FROZEN_ENTRY_KEYS)}"
        for k, v in entry.items():
            if k.endswith("_sha256") and not _is_hex64(v):
                return f"baseline frozen_incident[{iid!r}].{k} is not a lowercase sha256"
        if not isinstance(entry["has_public_url"], bool):
            return f"baseline frozen_incident[{iid!r}].has_public_url is not a boolean"
    present_incidents = [i for i in _FROZEN_INCIDENT_IDS if i in man]
    if sorted(inc) != sorted(present_incidents):
        return (f"baseline frozen_incident ids {sorted(inc)} != the incident posts present in the manifest "
                f"{sorted(present_incidents)}")
    # INTERNAL CONSISTENCY: the stored aggregates must equal a recomputation from the per-post maps alone.
    recomputed = _layer_digests(man, lay)
    for k, v in recomputed.items():
        if digests[k] != v:
            return f"baseline digest {k!r} does not match a recomputation from per_post_layers/manifest"
    return None


def compare_canary_baseline(cfg: Config, *, baseline: str) -> ActionResult:
    try:
        prior = json.loads(Path(baseline).expanduser().read_text())
    except (OSError, ValueError) as exc:
        return _err(f"cannot read baseline: {str(exc)[:120]}")
    shape_err = _validate_baseline_shape(prior)
    if shape_err is not None:
        return _err(f"invalid baseline — refusing to report a clean comparison: {shape_err}")
    try:
        cur = _build_manifest(cfg)
    except Exception as exc:
        get_logger(cfg)("canary", "baseline", "compare_manifest_failed", level="error", err=str(exc)[:140])
        return _err(f"current manifest failed: {str(exc)[:140]}")
    p_man, c_man = prior["per_post_manifest"], cur["per_post_manifest"]
    p_lay, c_lay = prior["per_post_layers"], cur["per_post_layers"]
    added = sorted(set(c_man) - set(p_man))
    removed = sorted(set(p_man) - set(c_man))
    both = set(p_man) & set(c_man)
    raw_changed = sorted(r for r in both if p_man[r] != c_man[r])
    def _layer_changed(key):
        return sorted(r for r in both if p_lay[r].get(key) != c_lay[r].get(key))
    safety_changed = _layer_changed("safe")
    sched_changed = _layer_changed("sched")
    content_changed = _layer_changed("content")
    safety_fields = {}
    for r in safety_changed:
        pf, cf = json.loads(p_lay[r]["safe"]), json.loads(c_lay[r]["safe"])
        safety_fields[r] = sorted(k for k in (set(pf) | set(cf)) if pf.get(k) != cf.get(k))
    # mismatch is TRUE for ANY divergence — raw, any layer, an added/removed id, OR an aggregate-digest
    # inequality (a modified baseline digest must never return a clean exit).
    digests_equal = {k: prior["digests"].get(k) == cur["digests"][k] for k in cur["digests"]}
    mismatch = bool(added or removed or raw_changed or safety_changed or sched_changed or content_changed
                    or not all(digests_equal.values()))
    return _ok({
        "mismatch": mismatch, "added": added, "removed": removed, "raw_changed": raw_changed,
        "safety_critical_changed": safety_fields, "scheduling_changed": sched_changed,
        "content_changed": content_changed, "digests_equal": digests_equal,
    })
