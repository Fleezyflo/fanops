# src/fanops/ledger.py
"""Single source of truth: one JSON doc of top-level maps (SCHEMA_VERSION-stamped), git-versioned.
Persists 10 id->unit (model) maps — sources, moments, clips, posts, stitch_plans, batches, renders,
selection_facts, account_selections, imported_media — plus 2 non-unit scalar/plain-dict maps: tag_log
("account|clip_id" -> ISO tag time) and variant_streaks ("account|platform" -> streak dict). Keep this
inventory in sync with _save_unlocked's `doc` (the serialization truth) whenever a map is added/removed.
Writes are ATOMIC (temp file + os.replace) under a file lock so the 're-run advance()'
model cannot corrupt or lose updates. Provides reconcile (upsert+cascade) and retire."""
from __future__ import annotations
import fcntl, json, os, re, secrets, sys, time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import ControlFileError, LockBusyError, reason as _reason
from fanops.models import (Source, Moment, Clip, Post, Render, SelectionFact, AccountSelection,
                           SelectionMethod, account_selection_id, normalize_account_handle,
                           StitchPlan, StitchState, Batch, ImportedMedia,
                           SourceState, MomentState, ClipState, PostState)


_DEFAULT_LOCK_TIMEOUT = 30.0

# On-disk ledger shape version. BUMP when a model field changes shape (rename/retype/required) and
# add a _MIGRATIONS[N] step transforming v(N-1) raw -> vN BEFORE unit construction. A pre-versioning
# ledger (no schema_version key) reads as v0; v0->v1 is the no-op baseline stamp (the shape was
# already stable). The point is forward safety: a future field change becomes a migration step, not
# a silent field-drop (pydantic extra="ignore") or a load crash.
def _migrate_v3_created_at(raw: dict) -> dict:
    """v2->v3 backfill (content-lifecycle): stamp created_at on Source + Post rows lacking it. Source <- file
    mtime (true ingest day) when source_path exists, else a single migration-time stamp; Post <- scheduled_time
    when tz-aware parseable, else the stamp. Pure on the RAW dict (runs before unit construction); NEVER raises
    (any OSError/parse/type error -> the stamp). Idempotent: an existing created_at is kept. Does NOT touch
    published_at (a pre-existing published row has no true publish time; the grouper falls back to
    scheduled_time)."""
    from fanops.timeutil import iso_z, parse_iso       # local: cycle-safe (timeutil imports only stdlib)
    stamp = iso_z(datetime.now(timezone.utc))
    out = dict(raw)
    srcs = dict(out.get("sources", {}))
    for sid, s in list(srcs.items()):
        if not isinstance(s, dict) or s.get("created_at"): continue
        ts = stamp; sp = s.get("source_path")
        if sp and isinstance(sp, str):
            try: ts = iso_z(datetime.fromtimestamp(os.path.getmtime(sp), tz=timezone.utc))
            except (OSError, ValueError, OverflowError, TypeError): ts = stamp
        srcs[sid] = {**s, "created_at": ts}
    out["sources"] = srcs
    posts = dict(out.get("posts", {}))
    for pid, p in list(posts.items()):
        if not isinstance(p, dict) or p.get("created_at"): continue
        ts = stamp; st = p.get("scheduled_time")
        if st and isinstance(st, str):
            try:
                dt = parse_iso(st)
                ts = iso_z(dt) if dt.tzinfo is not None else stamp   # naive on-disk time -> stamp, never a local guess
            except (ValueError, TypeError, AttributeError): ts = stamp
        posts[pid] = {**p, "created_at": ts}
    out["posts"] = posts
    return out


def _migrate_v4_metrics_series(raw: dict) -> dict:
    """v3->v4 (P3): back-fill a single 'legacy'-tagged metrics_series row for every Post that already
    carries metrics but has no series yet, so a pre-P3 analyzed post keeps its one data point as the
    series' first entry. Pure on the RAW dict (runs before unit construction); NEVER raises (a non-dict
    post row is skipped, mirroring _migrate_v3_created_at); idempotent (a post with a non-empty
    metrics_series is untouched). A post with empty/absent metrics gets no row (the model default [] —
    we never fabricate a point from nothing). 'legacy' is deliberately NOT a CADENCE_OFFSETS member, so
    it never blocks a real future poll (due_offset only consults cadence offsets) and stays
    distinguishable downstream. Does NOT touch Post.metrics (the LATEST snapshot) — purely additive."""
    from fanops.timeutil import iso_z                   # local: cycle-safe (timeutil imports only stdlib)
    stamp = iso_z(datetime.now(timezone.utc))
    out = dict(raw)
    posts = dict(out.get("posts", {}))
    for pid, p in list(posts.items()):
        if not isinstance(p, dict) or p.get("metrics_series"): continue   # torn row / already migrated
        metrics = p.get("metrics")
        if isinstance(metrics, dict) and metrics:
            posts[pid] = {**p, "metrics_series": [{**metrics, "offset": "legacy", "captured_at": stamp}]}
    out["posts"] = posts
    return out


_ACCTSEL_METHOD_RANK = {SelectionMethod.operator: 5, SelectionMethod.llm: 4, SelectionMethod.migrated: 3,
                        SelectionMethod.heuristic: 2, SelectionMethod.fan_all_default: 1, SelectionMethod.pending: 0}


def _pick_account_selection(rows: list[dict]) -> dict:
    """Choose the surviving row for a duplicate (source_id, account) group — prefer chosen methods over
    fan_all_default, then more moment_ids, then newer created_at."""
    def _key(s: dict):
        method = SelectionMethod(s.get("method") or SelectionMethod.fan_all_default.value)
        return (_ACCTSEL_METHOD_RANK.get(method, 0), len(s.get("moment_ids") or []), s.get("created_at") or "")
    return max(rows, key=_key)


def _dedupe_account_selections(raw: dict) -> dict:
    """Collapse @-alias duplicates to one canonical row per (source_id, account). Idempotent."""
    out = dict(raw)
    selections = dict(out.get("account_selections") or {})
    if not selections: return out
    groups: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for sid, s in selections.items():
        if not isinstance(s, dict): continue
        src_id = s.get("source_id")
        acct = normalize_account_handle(s.get("account") or "")
        if not src_id or not acct: continue
        norm = dict(s)
        norm["account"] = acct
        norm["id"] = account_selection_id(src_id, acct)
        groups.setdefault((src_id, acct), []).append((sid, norm))
    deduped: dict[str, dict] = {}
    for (_src, _acct), rows in groups.items():
        winner = _pick_account_selection([r for _, r in rows])
        deduped[winner["id"]] = winner
    out["account_selections"] = deduped
    return out


def prune_orphan_account_selections(led: "Ledger") -> int:
    """Drop AccountSelections whose source_id is absent from sources (stale re-ingest lineage). Returns count dropped."""
    live = set(led.sources.keys())
    dropped = 0
    for sid in list(led.account_selections.keys()):
        sel = led.account_selections[sid]
        if sel.source_id not in live:
            del led.account_selections[sid]
            dropped += 1
    return dropped


def selection_index_for_source(led: "Ledger", source_id: str) -> dict[str, list]:
    """MOL-82: the SCOPED cast-handles index for ONE source — moment_id -> sorted cast handles, built by ONE
    scan of the source's selections instead of rescanning the whole ledger-wide account_selections map per
    moment/cell. Exactly cast_handles_for's per-moment truth (a handle is cast on a moment iff it owns a CHOSEN
    selection listing that moment_id; fan_all_default/pending rows carry no moment_ids -> excluded), lifted to a
    single pass so review_matrix/account_lanes look up O(1) per cell. A moment absent from the index == [] cast
    (same as cast_handles_for returning []). Pure read; the sorted lists match cast_handles_for byte-for-byte."""
    idx: dict[str, list] = {}
    for s in led.selections_of_source(source_id):
        for mid in (s.moment_ids or []):
            idx.setdefault(mid, []).append(s.account)
    for mid in idx: idx[mid].sort()
    return idx


def _migrate_v8_account_selections(raw: dict) -> dict:
    """v8->v9 (RF1): lift the legacy, non-durable Moment.affinities into durable AccountSelection rows."""
    out = dict(raw)
    selections = dict(out.get("account_selections") or {})       # idempotent: keep any already-migrated rows
    facts = out.get("selection_facts") or {}
    llm_pairs = {(f["source_id"], f["account"]) for f in facts.values()
                 if isinstance(f, dict) and f.get("method") == "llm" and f.get("source_id") and f.get("account")}
    groups: dict = {}
    for mid, m in (out.get("moments") or {}).items():
        if not isinstance(m, dict): continue                     # torn row -> skip, never raise
        affs = m.get("affinities") or []
        sid = m.get("parent_id")
        if not affs or not sid: continue                         # empty affinities / no parent -> no record
        for handle in affs:
            groups.setdefault((sid, handle), []).append(mid)
    for (sid, handle), mids in groups.items():
        acct = normalize_account_handle(handle)
        asid = account_selection_id(sid, acct)
        if asid in selections: continue                          # idempotent: never overwrite an existing row
        method = "llm" if (sid, handle) in llm_pairs else "migrated"
        selections[asid] = {"id": asid, "source_id": sid, "account": acct,
                            "moment_ids": sorted(mids), "method": method,
                            "batch_id": None, "created_at": None}
    out["account_selections"] = selections
    return _dedupe_account_selections(out)


SCHEMA_VERSION = 10
# version N <- transform from N-1. v0 (pre-versioning) -> v1: shape unchanged, identity stamp.
# v1 -> v2 (M3): inject the new top-level stitch_plans map (additive; old ledgers had no such key).
# v2 -> v3 (content-lifecycle): backfill created_at on every Source + Post (Source <- file mtime, Post <-
# scheduled_time, else a single migration-time stamp). Additive + idempotent. published_at is NOT backfilled.
# v3 -> v4 (P3): back-fill a single 'legacy' metrics_series row for posts that already carry metrics.
# v4 -> v5 (Account-First Studio): inject the new top-level batches map (additive; batch_id rides the
# pydantic default on Source/Post, so NO row backfill — byte-for-byte the v1->v2 stitch_plans injection).
# v5 -> v6 (per-account Render foundation): inject the new top-level renders map (additive; render_id rides
# the pydantic default on Post, so NO row backfill — same injection shape). The Render entity is the
# per-account shippable artifact; old ledgers load with renders={} and render_id None (byte-identical).
# v6 -> v7 (M4 filing/naming/tracking): inject the new top-level selection_facts map (additive; the durable
# audit record of WHICH account got WHICH moment and WHY — Moment.affinities is non-durable). NO row backfill
# (nothing wrote facts pre-v7); old ledgers load with selection_facts={} — same injection shape as v5/v6.
# v7 -> v8 (P3 shipped-provenance): the 2 new Render scalars (hook_source, cut_seconds) ride pydantic defaults
# on the Render model — NO top-level map to inject, NO row backfill. IDENTITY stamp (the v0->v1 baseline shape);
# registered so _migrate's hop-chain has no gap. Old renders load with hook_source=none / cut_seconds=None.
# v8 -> v9 (RF1 account-first differentiation): inject the new top-level account_selections map (additive; the
# durable, account-owned crosspost-gate input — (source, account) -> moment_ids + method, replacing the
# non-durable Moment.affinities filter-tag). NO row backfill in Task 1 (scaffold); the decided default for
# legacy affinities lands in Task 4. Old ledgers load with account_selections={} — same injection shape as v5/v6/v7.
# v9 -> v10 (ledger-rebuild, Instagram is the source of truth): inject the new top-level imported_media map
# (additive; the ImportedMedia entity — a live IG post PROBED from the platform with NO clip lineage, keyed by
# its Graph media_id). Old ledgers load with imported_media={} — same injection shape as v5/v6/v7/v9. A naive
# add that skips this step AND the load/save lines silently DROPS the map on the next save (pydantic doesn't
# own a top-level map); the round-trip test pins it.
# Additive + idempotent + never-raising. The ledger is NEVER wiped — every migration is copy-on-write.
_MIGRATIONS = {1: lambda raw: raw,
               2: lambda raw: {**raw, "stitch_plans": raw.get("stitch_plans", {})},
               3: _migrate_v3_created_at,
               4: _migrate_v4_metrics_series,
               5: lambda raw: {**raw, "batches": raw.get("batches", {})},
               6: lambda raw: {**raw, "renders": raw.get("renders", {})},
               7: lambda raw: {**raw, "selection_facts": raw.get("selection_facts", {})},
               8: lambda raw: raw,
               9: _migrate_v8_account_selections,
               10: lambda raw: {**raw, "imported_media": raw.get("imported_media", {})}}

# M1: an ingested source file is named "{sid}{ext}" where sid = make_id("src", sha) = "src_" + sha1[:12]
# (lowercase hex). rebuild_catalog uses this shape to tell a genuinely-orphaned source file from junk
# (.gitkeep / .DS_Store / a hand-dropped misnamed file) — only a matching stem becomes a discovered row.
_SID_RE = re.compile(r"^src_[0-9a-f]{12}$")


class _NewerSchema(ControlFileError):
    """A ledger written by a NEWER fanops than this code understands. A ControlFileError subtype so
    cli.main exits 2 cleanly, but raised as its own type inside load() so the generic 'invalid'
    rewrap doesn't reword it — the operator must see 'upgrade fanops', not 'invalid: ...'."""
    def __init__(self, on_disk: int):
        super().__init__(f"ledger.json is schema v{on_disk} but this fanops understands only "
                         f"v{SCHEMA_VERSION} — upgrade fanops (refusing to load a newer ledger and "
                         f"silently drop its fields on save)")


def _migrate(raw: dict, from_version: int) -> dict:
    """Hop-chain an older on-disk ledger up to SCHEMA_VERSION, applying each registered step in
    order. A gap in the chain (no step to the next version) is a fatal, typed error — better than
    constructing units from a half-migrated dict."""
    v = from_version
    while v < SCHEMA_VERSION:
        step = _MIGRATIONS.get(v + 1)
        if step is None:
            raise ControlFileError(
                f"ledger.json schema v{from_version}: no migration path to v{v + 1} — upgrade fanops")
        raw = step(raw)
        v += 1
    return raw


@contextmanager
def _file_lock(lock_path: Path, timeout: float | None = None):
    """Mutual exclusion for ledger writes via fcntl.flock — chosen over an O_EXCL sentinel
    because the kernel RELEASES an flock when the holding process dies (H6). So a writer killed
    -9 mid-write leaves no held lock: the leftover file is inert and the next process acquires
    immediately. This SELF-HEALS the orphaned-lock outage (the old sentinel wedged every command
    for the timeout, then crashed, until a human rm'd the file). The only remaining wait is
    GENUINE contention — a second LIVE process (overlapping cron) — which we bound by `timeout`
    and surface as a typed LockBusyError the CLI catches, never an uncaught traceback.

    timeout=None reads the module-level _DEFAULT_LOCK_TIMEOUT at CALL time (not bound as a
    default arg), so callers and tests can tune it without re-importing."""
    if timeout is None:
        timeout = _DEFAULT_LOCK_TIMEOUT
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:                          # held by another LIVE process
                if time.monotonic() - start > timeout:
                    raise LockBusyError(
                        f"ledger lock busy > {timeout}s (another fanops process is writing): {lock_path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _fallback_iso(suggested_iso: str | None, now_iso: str) -> str:
    """approve_post's no-future-operator-time fallback. None (legacy/CLI caller) -> now_iso EXACTLY
    (back-compat). A supplied suggestion that parses STRICTLY future -> use it. A supplied-but-degenerate
    suggestion (<= now, e.g. surface_time's seed%50==0 && jitter==0) -> now_iso + 1s, so the system-chosen
    fallback NEVER equals/precedes now and re-opens the publish-now hole. Unparseable -> now_iso."""
    if suggested_iso is None: return now_iso
    from datetime import timezone, timedelta
    from fanops.timeutil import parse_iso, iso_z
    try:
        n = parse_iso(now_iso); s = parse_iso(suggested_iso)
        if n.tzinfo is None: n = n.replace(tzinfo=timezone.utc)
        if s.tzinfo is None: s = s.replace(tzinfo=timezone.utc)
        return suggested_iso if s > n else iso_z(n + timedelta(seconds=1))
    except (ValueError, TypeError): return now_iso


def _snapshot_dest(cfg: Config, now: datetime) -> Path:
    """Mint the pre-wipe snapshot destination path (MOL-75). The filename keeps the second-granularity UTC
    timestamp as a SORTABLE/recognizable prefix, then appends a short random uniqueness token so two
    snapshots taken within the same wall-clock second can never target the same path — the collision class
    that could silently clobber the sole pre-wipe rollback point is eliminated by construction."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    token = secrets.token_hex(4)                    # 8 hex chars: unique per call, keeps the name short
    return cfg.control / f"ledger.snapshot.{stamp}.{token}.json"


class Ledger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sources: dict[str, Source] = {}
        self.moments: dict[str, Moment] = {}
        self.clips: dict[str, Clip] = {}
        self.posts: dict[str, Post] = {}
        self.tag_log: dict[str, str] = {}     # "account|clip_id" -> ISO time of an artist tag (H3:
                                              # keyed per-tag, not per-account, so a re-tag can't
                                              # overwrite a time the cross-account de-cluster window
                                              # still needs)
        self.variant_streaks: dict[str, dict] = {}   # "account|platform" -> {hook, fingerprint, streak}
                                              # (variant-amplify v3: sustained-win streak per surface;
                                              # deterministic, idempotent on unchanged evidence; inert
                                              # when FANOPS_VARIANT_AMPLIFY off)
        self.stitch_plans: dict[str, StitchPlan] = {}   # M3: structural-hook plans (suggested->approved->
                                              # in_use / dismissed / error); additive top-level map, the
                                              # operator-approval spine. Empty until a format (M4+) emits one.
        self.batches: dict[str, Batch] = {}   # Account-First Studio: named, account-targeted ingest groups
                                              # (5th id->unit map; additive). Empty until a named ingest mints one.
        self.renders: dict[str, Render] = {}  # per-account Render foundation: the per-account shippable artifacts
                                              # (6th id->unit map; additive). Empty until crosspost mints one under
                                              # creative_variation. Content-addressed by (clip_id, hook_text).
        self.selection_facts: dict[str, SelectionFact] = {}   # M4: durable per-(moment, account) selection audit
                                              # (7th id->unit map; additive). Empty until casting writes one (M4b).
        self.account_selections: dict[str, AccountSelection] = {}   # RF1: the durable, account-owned crosspost-gate
                                              # input (8th id->unit map; additive). One-per-(source, account),
                                              # content-addressed. Empty until casting writes one; the gate falls
                                              # back to Moment.affinities for a source that has none (pre-v9).
        self.imported_media: dict[str, ImportedMedia] = {}   # ledger-rebuild: live IG posts PROBED from the platform
                                              # with NO clip lineage (9th id->unit map; additive). Keyed by the Graph
                                              # media_id (the natural key). Empty until the projection imports one (M2);
                                              # old ledgers load with {} (pre-v10) — the OFF/baseline shape is byte-identical.

    @classmethod
    def load(cls, cfg: Config) -> "Ledger":
        led = cls(cfg)
        p = cfg.ledger_path
        if p.exists():
            text = p.read_text()                       # an I/O error here is a real problem, not "invalid"
            try:
                raw = json.loads(text)
                on_disk = raw.get("schema_version", 0)     # absent key => pre-versioning ledger (v0)
                if on_disk > SCHEMA_VERSION:
                    # A ledger written by a NEWER fanops. Loading then saving would silently DROP its
                    # future fields (pydantic extra="ignore"), corrupting a forward-version state store
                    # on downgrade. Refuse loudly — raised OUTSIDE the except below so it isn't reworded.
                    raise _NewerSchema(on_disk)
                if on_disk < SCHEMA_VERSION:
                    raw = _migrate(raw, on_disk)
                raw = _dedupe_account_selections(raw)
                led.sources = {k: Source(**v) for k, v in raw.get("sources", {}).items()}
                led.moments = {k: Moment(**v) for k, v in raw.get("moments", {}).items()}
                led.clips = {k: Clip(**v) for k, v in raw.get("clips", {}).items()}
                # dryrun-boundary M3: the R1 migration-on-read back-fill that healed pre-R1 ghost rows
                # (state=published + public_url='') by writing 'dryrun://<id>' or parking needs_reconcile
                # is DELETED. Post-boundary nothing produces a ghost row (a dryrun post halts `queued`,
                # never terminal-without-url); the 29 legacy rows were pruned outright (M4). A terminal
                # row with no url now fails the R1 invariant at construction below — which is correct: it
                # would be a genuine defect, not a dryrun artifact to paper over.
                led.posts = {k: Post(**v) for k, v in raw.get("posts", {}).items()}
                led.tag_log = raw.get("tag_log", {})
                led.variant_streaks = raw.get("variant_streaks", {})
                led.stitch_plans = {k: StitchPlan(**v) for k, v in raw.get("stitch_plans", {}).items()}
                led.batches = {k: Batch(**v) for k, v in raw.get("batches", {}).items()}
                led.renders = {k: Render(**v) for k, v in raw.get("renders", {}).items()}
                led.selection_facts = {k: SelectionFact(**v) for k, v in raw.get("selection_facts", {}).items()}
                led.account_selections = {k: AccountSelection(**v) for k, v in raw.get("account_selections", {}).items()}
                led.imported_media = {k: ImportedMedia(**v) for k, v in raw.get("imported_media", {}).items()}
            except ControlFileError:
                raise                                  # _NewerSchema / _migrate gap: pass through, unreworded
            except Exception as e:
                # Malformed JSON or schema-violating field (hand-edit typo). Surface a clear
                # one-line reason instead of a raw JSONDecodeError/ValidationError traceback.
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
        return led

    @classmethod
    @contextmanager
    def transaction(cls, cfg: Config, *, timeout: float | None = None):
        """Hold the ledger lock across the WHOLE load-mutate-save cycle (AUDIT B4). Acquiring the
        lock here — BEFORE load — closes the lost-update window that the save()-only lock left open
        (two overlapping passes both loaded a stale snapshot, last save() won, the other's updates
        vanished — silently dropping/duplicating real posts under cron). On exit the ledger is saved
        ONCE under the still-held lock. A second live process is excluded for the duration and gets a
        typed LockBusyError (bounded by timeout), never a silent overwrite.

        The save runs only on a CLEAN exit: if the with-block raises, the lock is still released
        (the _file_lock contextmanager's finally), but we do NOT save partial in-memory state — the
        on-disk ledger keeps the last committed snapshot. Callers wanting to persist progress despite
        a per-unit failure must catch+continue inside the block (mirroring advance()'s per-unit
        quarantine), exactly as they do today; an UNCAUGHT raise rolls back to the prior save."""
        with _file_lock(cfg.lock_path, timeout=timeout):
            led = cls.load(cfg)
            yield led
            led._save_unlocked()

    def _save_unlocked(self) -> None:
        """The write half of save(), WITHOUT re-acquiring the lock (the caller — transaction() —
        already holds it; flock is per-fd, so a nested acquire on a NEW fd from the same process
        would block against our own held lock and, under the LOCK_NB+timeout loop, raise
        LockBusyError after the timeout — a self-inflicted failure). Atomic write preserved
        (tmp + os.replace).

        PERF (audit, accepted tradeoff — do NOT 'fix' to incremental writes): this dumps the WHOLE
        ledger every transaction (O(all-time records)). That is DELIBERATE — the atomic whole-file
        tmp+replace under a single flock is exactly what closes the B4 lost-update window; an
        append-only/partial-write scheme would trade that correctness away. The working-set size is
        bounded instead by `fanops gc --keep-days` (cfg.gc_keep_days), which prunes aged records — so
        the file stays small in practice. Revisit only if a real, measured ledger ever outgrows GC."""
        doc = {
            "schema_version": SCHEMA_VERSION,          # stamp the on-disk shape (Phase 4a)
            "sources": {k: v.model_dump() for k, v in self.sources.items()},
            "moments": {k: v.model_dump() for k, v in self.moments.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
            "tag_log": self.tag_log,
            "variant_streaks": self.variant_streaks,
            "stitch_plans": {k: v.model_dump() for k, v in self.stitch_plans.items()},
            "batches": {k: v.model_dump() for k, v in self.batches.items()},
            "renders": {k: v.model_dump() for k, v in self.renders.items()},
            "selection_facts": {k: v.model_dump() for k, v in self.selection_facts.items()},
            "account_selections": {k: v.model_dump() for k, v in self.account_selections.items()},
            "imported_media": {k: v.model_dump() for k, v in self.imported_media.items()},
        }
        self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg.ledger_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        try: os.chmod(tmp, 0o600)            # owner-only at rest (audit): the ledger carries captions/URLs/submission ids
        except OSError: pass                 # best-effort — a non-POSIX FS must never break persistence
        os.replace(str(tmp), str(self.cfg.ledger_path))   # atomic on POSIX (the 0600 mode rides the replace)

    def save(self) -> None:
        """Standalone save for callers OUTSIDE a transaction (e.g. cmd_ingest, cmd_gc). Acquires
        the lock, then delegates the write to _save_unlocked. A caller already inside
        Ledger.transaction() must NOT call this (it would self-deadlock/LockBusyError) — it gets the
        single exit-save instead. (publish now runs its network OUTSIDE the lock via per-post
        claim->network->finalize transactions, so it no longer needs a mid-loop unlocked save.)"""
        with _file_lock(self.cfg.lock_path):
            self._save_unlocked()

    # ---- ledger-rebuild M4 (MOL-32): pre-wipe snapshot + rollback ---------------------------------
    # A snapshot is a BYTE copy of ledger.json (which is already a complete atomic whole-file dump), named
    # with a UTC timestamp under 00_control so it sits alongside the pre-R1 backups (the dryrun R1 decision:
    # any snapshot in 00_control stays LOADABLE — construction semantics unchanged). It is the mandatory,
    # verified-restorable rollback point BEFORE the M4 wipe removes anything (PRD risk table).
    @classmethod
    def snapshot(cls, cfg: Config, *, now: "datetime | None" = None) -> Path:
        """Write a timestamped byte-copy of the live ledger.json to 00_control and RETURN its path. Taken
        under the ledger lock (a consistent whole-file image, no half-written mid-transaction state). A
        missing ledger.json first materializes an empty ledger so the snapshot is always a loadable file."""
        import shutil
        now = now or datetime.now(timezone.utc)
        if not cfg.ledger_path.exists():
            cls.load(cfg).save()                       # materialize an empty (but valid) ledger to snapshot
        dest = _snapshot_dest(cfg, now)                # MOL-75: structurally-unique dest (timestamp + token)
        with _file_lock(cfg.lock_path):
            if dest.exists():                          # never silently overwrite a pristine pre-wipe image
                raise ControlFileError(f"ledger snapshot already exists: {dest}")
            shutil.copy2(str(cfg.ledger_path), str(dest))   # byte-identical image under the lock
        return dest

    @classmethod
    def restore_snapshot(cls, cfg: Config, snapshot_path: "Path | str") -> None:
        """Atomically restore ledger.json FROM a snapshot (tmp + os.replace, same as _save_unlocked). The
        wipe is thereby REVERSIBLE — a restore brings every removed row back byte-identically."""
        import shutil
        src = Path(snapshot_path)
        if not src.exists():
            raise ControlFileError(_reason("ledger snapshot not found", str(src)))
        with _file_lock(cfg.lock_path):
            tmp = cfg.ledger_path.with_suffix(".json.tmp")
            shutil.copy2(str(src), str(tmp))
            os.replace(str(tmp), str(cfg.ledger_path))   # atomic swap-in of the restored image

    # ---- idempotent adds (by id) ----
    def add_source(self, s: Source) -> None: self.sources.setdefault(s.id, s)
    def add_moment(self, m: Moment) -> None: self.moments.setdefault(m.id, m)
    def add_clip(self, c: Clip) -> None: self.clips.setdefault(c.id, c)
    def add_post(self, p: Post) -> None: self.posts.setdefault(p.id, p)
    def add_render(self, r: Render) -> None: self.renders.setdefault(r.id, r)   # first-write-wins (content-addressed dedup)
    def get_render(self, rid: str): return self.renders.get(rid)
    def add_selection_fact(self, f: SelectionFact) -> None: self.selection_facts[f.id] = f   # M4: OVERWRITE — a re-cast updates the why (latest selection wins; NOT render's content-dedup)
    def get_selection_fact(self, fid: str): return self.selection_facts.get(fid)
    def add_imported_media(self, im: ImportedMedia) -> None: self.imported_media[im.media_id] = im   # ledger-rebuild: UPSERT by media_id — a live re-pull carries fresher metrics that WIN (latest snapshot; NOT render's first-write dedup)
    def get_imported_media(self, media_id: str): return self.imported_media.get(media_id)
    def add_account_selection(self, s: AccountSelection) -> None:
        acct = normalize_account_handle(s.account)
        canon_id = account_selection_id(s.source_id, acct)
        if acct != s.account or s.id != canon_id:
            s = s.model_copy(update={"id": canon_id, "account": acct})
        for stale_id, stale in list(self.account_selections.items()):
            if stale_id != canon_id and stale.source_id == s.source_id and normalize_account_handle(stale.account) == acct:
                del self.account_selections[stale_id]              # @-alias / legacy duplicate id
        self.account_selections[canon_id] = s                      # RF1: OVERWRITE — one-per-(source, account)
    def account_selection_for(self, source_id: str, account: str):
        return self.account_selections.get(account_selection_id(source_id, account))
    def selections_of_source(self, source_id: str) -> list[AccountSelection]:
        return [s for s in self.account_selections.values() if s.source_id == source_id]
    def drop_account_selection(self, source_id: str, account: str) -> None:
        self.account_selections.pop(account_selection_id(source_id, account), None)   # RF1: operator removed the last pick -> no record (gate denies on a cast source)
    def moment_ids_selected_for(self, source_id: str, account: str) -> set:
        # "which specific moments did this account get?" — for the Review READ-MODEL ONLY, NEVER the gate. The
        # name says it: this is the SELECTED-ids set for display, not an admit/deny predicate. It returns set()
        # for BOTH "no selection written" (gate must fall back to affinities) AND fan_all_default (gate must
        # admit ALL) — two OPPOSITE gate decisions, indistinguishable here. The crosspost gate calls
        # account_selection_for() and branches on sel.method instead (casting.account_selection_admits).
        sel = self.account_selection_for(source_id, account)
        return set(sel.moment_ids) if sel else set()
    def cast_handles_for(self, source_id: str, moment_id: str) -> list:
        # MOM-3 ROOT: the handles CAST on this moment, DERIVED from the durable AccountSelection map — the Review
        # READ-MODEL ONLY (matrix display), NEVER the gate. Replaces reading the legacy Moment.affinities tag,
        # which the operator override (cast_add/cast_remove writes AccountSelection, not affinities) never touched,
        # so the matrix could diverge from the lanes/gate. A handle is "cast on this moment" iff it owns a CHOSEN
        # selection listing this moment_id; a fan_all_default/pending TAG row (no moment_ids) is NOT a per-moment
        # cast and is excluded. [] = no chosen selection for this moment -> the matrix treats it as uncast/all
        # (exactly today's affinities==[] behavior). Sorted for deterministic display.
        return sorted(s.account for s in self.selections_of_source(source_id) if moment_id in set(s.moment_ids))

    # ---- typed state setters (FIX F65 — no cross-unit scan) ----
    # ECC fix #10: immutable update (model_copy + dict reassignment) instead of in-place `.state =`.
    # Aligns with the project's immutable-data rule and is safe if a model is ever frozen; the dict
    # reassignment makes the new object the one every later reader (and serialization) sees.
    def set_source_state(self, uid: str, st: SourceState) -> None: self.sources[uid] = self.sources[uid].model_copy(update={"state": st})
    def set_moment_state(self, uid: str, st: MomentState) -> None: self.moments[uid] = self.moments[uid].model_copy(update={"state": st})
    def set_clip_state(self, uid: str, st: ClipState) -> None: self.clips[uid] = self.clips[uid].model_copy(update={"state": st})
    def set_post_state(self, uid: str, st: PostState) -> None: self.posts[uid] = self.posts[uid].model_copy(update={"state": st})

    # ---- post-approval gate (caller holds the transaction; in-lock guard => contended/wrong-state is a clean no-op) ----
    def approve_post(self, uid: str, *, now_iso: str, suggested_iso: str | None = None) -> None:
        from datetime import timezone
        from fanops.timeutil import parse_iso
        p = self.posts.get(uid)
        if p is None or p.state is not PostState.awaiting_approval: return   # only an unapproved post promotes
        # bump a stale (<=now / missing / unparseable) stagger-time to now so approval never machine-guns a backlog
        # onto a live backend; a still-future schedule is the operator's intent and is preserved. now_iso is INJECTED
        # (no clock in the ledger) so the transition is deterministic in tests. A tz-naive on-disk time (hand-edit)
        # is read AS UTC — consistent with iso_z — so a legit far-future naive schedule is NOT silently zeroed.
        keep = False
        if p.scheduled_time:
            try:
                sched = parse_iso(p.scheduled_time)
                if sched.tzinfo is None: sched = sched.replace(tzinfo=timezone.utc)
                keep = sched > parse_iso(now_iso)
            except (ValueError, TypeError): keep = False                    # truly malformed -> treat as stale, bump to now
        self.posts[uid] = p.model_copy(update={"state": PostState.queued, "scheduled_time": p.scheduled_time if keep else _fallback_iso(suggested_iso, now_iso)})
    def reject_post(self, uid: str) -> None:
        p = self.posts.get(uid)
        if p is not None and p.state is PostState.awaiting_approval:        # only discard an unapproved post
            self.posts[uid] = p.model_copy(update={"state": PostState.rejected})
    def unapprove_post(self, uid: str) -> None:
        p = self.posts.get(uid)
        if p is not None and p.state is PostState.queued:                   # send an approved-but-unsent post back to review
            self.posts[uid] = p.model_copy(update={"state": PostState.awaiting_approval})

    # ---- queries ----
    def already_seen(self, *, sha256: str | None = None) -> bool:
        return any(s.sha256 == sha256 for s in self.sources.values()) if sha256 else False
    def sources_in_state(self, st: SourceState) -> list[Source]:
        return [s for s in self.sources.values() if s.state is st]
    def clips_in_state(self, st: ClipState) -> list[Clip]:
        return [c for c in self.clips.values() if c.state is st]
    def posts_in_state(self, st: PostState) -> list[Post]:
        return [p for p in self.posts.values() if p.state is st]
    def moments_of(self, source_id: str) -> list[Moment]:
        return [m for m in self.moments.values() if m.parent_id == source_id]
    def clips_of(self, moment_id: str) -> list[Clip]:
        return [c for c in self.clips.values() if c.parent_id == moment_id]
    def posts_of(self, clip_id: str) -> list[Post]:
        return [p for p in self.posts.values() if p.parent_id == clip_id]
    # M4 'account index': account-keyed accessors — the per-account direct-lookup API (a scan today; the ledger
    # is small enough that an indexed cache would be speculative, so these stay simple scans with a clean name).
    def posts_of_account(self, handle: str) -> list[Post]:
        return [p for p in self.posts.values() if p.account == handle]
    # NB: a per-account `renders_of_account` was removed (audit) — it had ZERO production callers and
    # under-reported a render shared via cross-account REUSE (a reused render keeps its origin `account`,
    # so a naive `r.account == handle` scan misses it). Re-add a CORRECT (surface-keyed) version if a
    # consumer ever needs it; don't resurrect the buggy scan.
    def selection_facts_of_account(self, handle: str) -> list[SelectionFact]:
        return [f for f in self.selection_facts.values() if f.account == handle]
    def selection_facts_of_moment(self, moment_id: str) -> list[SelectionFact]:
        return [f for f in self.selection_facts.values() if f.moment_id == moment_id]

    # ---- reconcile (FIX F08/F32): upsert keep-set, cascade-delete the rest for this source ----
    def reconcile_moments(self, source_id: str, keep: dict[str, Moment]) -> None:
        from fanops.router import CLEAN_AWAITING       # local import: router has no ledger dep (avoid a cycle)
        existing = {m.id for m in self.moments_of(source_id)}
        for mid in existing - set(keep):
            mom = self.moments.get(mid)                # M2: a clean clip reserved for a not-yet-built strategy
            if mom is not None and (mom.hook_strategy or "").startswith(CLEAN_AWAITING):
                continue                               # is GC-preserved (its future strategy must still find it)
            self._delete_moment_cascade(mid)
        for mid, m in keep.items():
            prior = self.moments.get(mid)
            if prior is not None and prior.state is MomentState.retired:
                # AUDIT M1: never resurrect a retired moment. adjust.retire set it to `retired`
                # (deliberately suppressed from future work); a fresh `decided` copy from a later
                # decision would otherwise overwrite that, re-rendering + re-posting a retired
                # lineage. Skip the upsert — the retirement stands. (A NON-retired prior is still
                # upserted in place, so legitimate re-decision keeps working.)
                continue
            self.moments[mid] = m
        self._prune_orphan_selection_ids(source_id)   # MOM-4: drop selection ids for moments no longer render-targeting

    def _prune_orphan_selection_ids(self, source_id: str) -> None:
        """MOM-4: after a reconcile, an AccountSelection may list a moment id that was cascade-DELETED or RETIRED
        (so it can never post). Prune those ids; if a CHOSEN selection empties, DROP the record (the sum-type
        validator forbids an empty CHOSEN row, and an absent record correctly DENIES the account on a cast source).
        A TAG row (fan_all_default/pending) carries no ids and is untouched. Idempotent. Distinct from Task-5's
        re-pick drop (which clears the WHOLE source); this closes the retire/adjust paths that drop ONE moment
        without going through ingest_moments, leaving an orphan id in an otherwise-valid selection."""
        live = {m.id for m in self.moments_of(source_id) if m.state is not MomentState.retired}
        for sel in list(self.selections_of_source(source_id)):
            if not sel.moment_ids:
                continue                                   # TAG row (fan_all_default/pending) -> nothing to prune
            pruned = sorted(set(sel.moment_ids) & live)
            if pruned == sorted(sel.moment_ids):
                continue                                   # no orphan -> leave it
            if pruned:
                self.add_account_selection(sel.model_copy(update={"moment_ids": pruned}))   # re-validates (still CHOSEN)
            else:
                self.drop_account_selection(source_id, sel.account)   # emptied CHOSEN row -> drop, never an illegal []

    # Clip/Post states that mean "live on the platform / carries the performance record" —
    # these are NEVER cascade-deleted (deleting them would orphan a live post: untrackable by
    # track, unreclaimable by gc, and destroys the lift signal). A dropped moment that still has
    # any such descendant is RETIRED (suppressed from future work) rather than deleted.
    _LIVE_CLIP_STATES = (ClipState.published, ClipState.analyzed)
    # needs_reconcile included (AUDIT C1): such a post MAY be live on the platform (ambiguous
    # publish), so deleting its ledger record would orphan a possibly-live post — preserve + retire.
    _LIVE_POST_STATES = (PostState.published, PostState.analyzed, PostState.submitted,
                         PostState.submitting, PostState.needs_reconcile)
    # Cascade-protection superset (content-lifecycle Phase 1). _LIVE_POST_STATES is referenced ONLY here in
    # _delete_moment_cascade (grep-verified: no reconcile/track/learning reader) — the separate tuple is for
    # EXPLICITNESS + an independent pin test, NOT because an external caller depends on the narrow set. A
    # re-decided source's cascade must NEVER silently delete the operator's awaiting_approval (un-reviewed) /
    # queued (approved, not-yet-shipped) / retired (M4 stitch-superseded base) posts — deliberate human/stitch
    # records. PRESERVE-and-RETIRE exactly like a live post, at BOTH checks below (post-loop AND clip-drop).
    _PROTECTED_POST_STATES = _LIVE_POST_STATES + (PostState.awaiting_approval, PostState.queued, PostState.retired)

    def _delete_moment_cascade(self, moment_id: str) -> None:
        survived = False
        for c in self.clips_of(moment_id):
            clip_live = c.state in self._LIVE_CLIP_STATES
            for p in self.posts_of(c.id):
                if clip_live or p.state in self._PROTECTED_POST_STATES:
                    survived = True                      # preserve live + operator/stitch-worklist posts
                else:
                    self.posts.pop(p.id, None)
            if clip_live:
                survived = True                          # preserve the live clip + its file
            else:
                # only drop the clip if no live / worklist post hangs off it (else the post is orphaned)
                if not any(p.state in self._PROTECTED_POST_STATES for p in self.posts_of(c.id)):
                    # MOL-77: unlink the .mp4 in the same breath as dropping the row — cmd_gc only sweeps
                    # clips still in retired/analyzed state, so a row-less file is unreachable by gc forever
                    # (a permanent orphan). Fail-open + surface like cmd_gc: a bad unlink never aborts the cascade.
                    if c.path and os.path.exists(c.path):
                        try: os.remove(c.path)
                        except OSError as exc: print(f"cascade: could not remove {c.path}: {exc}", file=sys.stderr)
                    self.clips.pop(c.id, None)
                else:
                    survived = True
        if survived:
            # keep the moment but suppress it from future rendering/crossposting
            if moment_id in self.moments:
                self.moments[moment_id] = self.moments[moment_id].model_copy(update={"state": MomentState.retired})  # ECC fix #10
        else:
            self.moments.pop(moment_id, None)

    # ---- retire (FIX F55 — now observable) ----
    def retire_clip(self, clip_id: str) -> None:
        if clip_id in self.clips:
            self.clips[clip_id] = self.clips[clip_id].model_copy(update={"state": ClipState.retired})  # ECC fix #10
    def is_retired_clip(self, clip_id: str) -> bool:
        c = self.clips.get(clip_id)
        return bool(c and c.state is ClipState.retired)
    def is_retired_moment(self, moment_id: str) -> bool:
        m = self.moments.get(moment_id)
        return bool(m and m.state is MomentState.retired)

    # ---- M1 (structural-hooks): asset memory — retire-with-cascade + disk<->ledger rebuild ----
    def retire_source(self, source_id: str) -> None:
        # Remove a source: cascade-drop its moments/clips via reconcile with an EMPTY keep-set (a live
        # descendant is preserved + retired, NEVER deleted — the performance record survives), then mark
        # the source retired. The source FILE is LEFT on disk (a live post's media_url may point at it);
        # rebuild_catalog will not re-add it (its retired row remains, blocking resurrection).
        self.reconcile_moments(source_id, {})
        if source_id in self.sources:
            self.sources[source_id] = self.sources[source_id].model_copy(update={"state": SourceState.retired})  # ECC fix #10
    def is_retired_source(self, source_id: str) -> bool:
        s = self.sources.get(source_id)
        return bool(s and s.state is SourceState.retired)

    def rebuild_catalog(self, cfg: Config) -> None:
        # Reconcile the on-disk sources dir against the ledger: an orphaned source file (a src_*.<ext>
        # with no ledger row) is surfaced as a `discovered` source — INERT to clip-production until an
        # operator confirms it; a `retired` source is never resurrected; a ledger source whose file is
        # missing is never dropped. Idempotent. Iterates the DIR (not self.sources) -> no mutate-in-iter.
        # WIPE-SAFETY INVARIANT (content-lifecycle Phase 1): ADDS orphans only — NEVER retires a missing-file
        # source (retire_source is the explicit operator path). A future "warn on missing file" must LOG, never
        # retire. Locked by test_rebuild_idempotent_and_keeps_missing_file_sources.
        from fanops.ingest import MEDIA_EXT            # local import: ingest imports ledger (avoid a cycle)
        from fanops.timeutil import iso_z              # local: keep the timeutil dep cycle-safe
        if not cfg.sources.exists():
            return
        for f in sorted(cfg.sources.iterdir()):
            if not f.is_file() or f.suffix.lower() not in MEDIA_EXT or not _SID_RE.match(f.stem):
                continue                               # junk / non-source-named file -> ignore
            if f.stem not in self.sources:             # orphan on disk -> surface as discovered (inert)
                self.sources[f.stem] = Source(id=f.stem, state=SourceState.discovered, source_path=str(f),
                                              created_at=iso_z(datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)))

    # ---- M3 (structural-hooks): stitch_plan ops (operator-approval spine; caller holds the transaction) ----
    def add_stitch_plan(self, plan: StitchPlan) -> None:
        self.stitch_plans.setdefault(plan.id, plan)    # idempotent by content-addressed id (dedup re-emit)
    def approve_stitch_plan(self, plan_id: str) -> None:
        p = self.stitch_plans.get(plan_id)             # in-lock re-check: ONLY a suggested plan approves, so a
        if p is not None and p.state is StitchState.suggested:   # second/contended approval is a clean no-op
            self.stitch_plans[plan_id] = p.model_copy(update={"state": StitchState.approved})  # ECC fix #10 (never a second render in M4)
    def dismiss_stitch_plan(self, plan_id: str) -> None:
        p = self.stitch_plans.get(plan_id)             # suggested|approved -> dismissed (terminal); an in_use
        if p is not None and p.state in (StitchState.suggested, StitchState.approved):   # plan is forward-only
            self.stitch_plans[plan_id] = p.model_copy(update={"state": StitchState.dismissed})  # ECC fix #10

    # ---- Account-First Studio: batch ops (named, account-targeted ingest groups; caller holds the transaction) ----
    def add_batch(self, b: Batch) -> None:
        self.batches.setdefault(b.id, b)               # idempotent by content-addressed id (re-submit dedup)
    def get_batch(self, bid: str) -> Batch | None:
        return self.batches.get(bid)
    def batches_for_account(self, handle: str) -> list[Batch]:
        return [b for b in self.batches.values() if not b.target_accounts or handle in b.target_accounts]
