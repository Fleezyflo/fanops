# src/fanops/ledger.py
"""Single source of truth: one JSON doc, four id->unit maps, git-versioned.
Writes are ATOMIC (temp file + os.replace) under a file lock so the 're-run advance()'
model cannot corrupt or lose updates. Provides reconcile (upsert+cascade) and retire."""
from __future__ import annotations
import fcntl, json, os, time
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import ControlFileError, LockBusyError, reason as _reason
from fanops.models import (Source, Moment, Clip, Post,
                           SourceState, MomentState, ClipState, PostState)


_DEFAULT_LOCK_TIMEOUT = 30.0


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

    @classmethod
    def load(cls, cfg: Config) -> "Ledger":
        led = cls(cfg)
        p = cfg.ledger_path
        if p.exists():
            text = p.read_text()                       # an I/O error here is a real problem, not "invalid"
            try:
                raw = json.loads(text)
                led.sources = {k: Source(**v) for k, v in raw.get("sources", {}).items()}
                led.moments = {k: Moment(**v) for k, v in raw.get("moments", {}).items()}
                led.clips = {k: Clip(**v) for k, v in raw.get("clips", {}).items()}
                led.posts = {k: Post(**v) for k, v in raw.get("posts", {}).items()}
                led.tag_log = raw.get("tag_log", {})
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
        (tmp + os.replace)."""
        doc = {
            "sources": {k: v.model_dump() for k, v in self.sources.items()},
            "moments": {k: v.model_dump() for k, v in self.moments.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
            "tag_log": self.tag_log,
        }
        self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg.ledger_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        os.replace(str(tmp), str(self.cfg.ledger_path))   # atomic on POSIX

    def save(self) -> None:
        """Standalone save for callers OUTSIDE a transaction (e.g. cmd_ingest, cmd_gc). Acquires
        the lock, then delegates the write to _save_unlocked. A caller already inside
        Ledger.transaction() must NOT call this (it would self-deadlock/LockBusyError) — it gets the
        single exit-save instead; publish_due takes an in_transaction flag for its mid-loop saves."""
        with _file_lock(self.cfg.lock_path):
            self._save_unlocked()

    # ---- idempotent adds (by id) ----
    def add_source(self, s: Source) -> None: self.sources.setdefault(s.id, s)
    def add_moment(self, m: Moment) -> None: self.moments.setdefault(m.id, m)
    def add_clip(self, c: Clip) -> None: self.clips.setdefault(c.id, c)
    def add_post(self, p: Post) -> None: self.posts.setdefault(p.id, p)

    # ---- typed state setters (FIX F65 — no cross-unit scan) ----
    def set_source_state(self, uid: str, st: SourceState) -> None: self.sources[uid].state = st
    def set_moment_state(self, uid: str, st: MomentState) -> None: self.moments[uid].state = st
    def set_clip_state(self, uid: str, st: ClipState) -> None: self.clips[uid].state = st
    def set_post_state(self, uid: str, st: PostState) -> None: self.posts[uid].state = st

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

    # ---- reconcile (FIX F08/F32): upsert keep-set, cascade-delete the rest for this source ----
    def reconcile_moments(self, source_id: str, keep: dict[str, Moment]) -> None:
        existing = {m.id for m in self.moments_of(source_id)}
        for mid in existing - set(keep):
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

    # Clip/Post states that mean "live on the platform / carries the performance record" —
    # these are NEVER cascade-deleted (deleting them would orphan a live post: untrackable by
    # track, unreclaimable by gc, and destroys the lift signal). A dropped moment that still has
    # any such descendant is RETIRED (suppressed from future work) rather than deleted.
    _LIVE_CLIP_STATES = (ClipState.published, ClipState.analyzed)
    # needs_reconcile included (AUDIT C1): such a post MAY be live on the platform (ambiguous
    # publish), so deleting its ledger record would orphan a possibly-live post — preserve + retire.
    _LIVE_POST_STATES = (PostState.published, PostState.analyzed, PostState.submitted,
                         PostState.submitting, PostState.needs_reconcile)

    def _delete_moment_cascade(self, moment_id: str) -> None:
        survived = False
        for c in self.clips_of(moment_id):
            clip_live = c.state in self._LIVE_CLIP_STATES
            for p in self.posts_of(c.id):
                if clip_live or p.state in self._LIVE_POST_STATES:
                    survived = True                      # preserve live posts (the performance record)
                else:
                    self.posts.pop(p.id, None)
            if clip_live:
                survived = True                          # preserve the live clip + its file
            else:
                # only drop the clip if no live post hangs off it
                if not any(p.state in self._LIVE_POST_STATES for p in self.posts_of(c.id)):
                    self.clips.pop(c.id, None)
                else:
                    survived = True
        if survived:
            # keep the moment but suppress it from future rendering/crossposting
            if moment_id in self.moments:
                self.moments[moment_id].state = MomentState.retired
        else:
            self.moments.pop(moment_id, None)

    # ---- retire (FIX F55 — now observable) ----
    def retire_clip(self, clip_id: str) -> None:
        if clip_id in self.clips:
            self.clips[clip_id].state = ClipState.retired
    def is_retired_clip(self, clip_id: str) -> bool:
        c = self.clips.get(clip_id)
        return bool(c and c.state is ClipState.retired)
    def is_retired_moment(self, moment_id: str) -> bool:
        m = self.moments.get(moment_id)
        return bool(m and m.state is MomentState.retired)
