# src/fanops/ledger.py
"""Single source of truth: one JSON doc, four id->unit maps, git-versioned.
Writes are ATOMIC (temp file + os.replace) under a file lock so the 're-run advance()'
model cannot corrupt or lose updates. Provides reconcile (upsert+cascade) and retire."""
from __future__ import annotations
import json, os, time
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import ControlFileError, reason as _reason
from fanops.models import (Source, Moment, Clip, Post,
                           SourceState, MomentState, ClipState, PostState)


@contextmanager
def _file_lock(lock_path: Path, timeout: float = 30.0):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd); break
        except FileExistsError:
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"ledger lock held > {timeout}s: {lock_path}")
            time.sleep(0.1)
    try:
        yield
    finally:
        try: os.unlink(str(lock_path))
        except FileNotFoundError: pass


class Ledger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sources: dict[str, Source] = {}
        self.moments: dict[str, Moment] = {}
        self.clips: dict[str, Clip] = {}
        self.posts: dict[str, Post] = {}
        self.tag_log: dict[str, str] = {}     # account -> ISO time of last artist tag

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

    def save(self) -> None:
        doc = {
            "sources": {k: v.model_dump() for k, v in self.sources.items()},
            "moments": {k: v.model_dump() for k, v in self.moments.items()},
            "clips": {k: v.model_dump() for k, v in self.clips.items()},
            "posts": {k: v.model_dump() for k, v in self.posts.items()},
            "tag_log": self.tag_log,
        }
        with _file_lock(self.cfg.lock_path):
            self.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cfg.ledger_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, indent=2, default=str))
            os.replace(str(tmp), str(self.cfg.ledger_path))   # atomic on POSIX

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
            if mid in self.moments:
                # in-place update (FIX: setdefault blocked updates in v1)
                self.moments[mid] = m
            else:
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
