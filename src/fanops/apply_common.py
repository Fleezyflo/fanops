# src/fanops/apply_common.py
"""Shared apply-phase primitives for in-place clip migrations (reframe, overlay-reburn)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


class PlanStale(RuntimeError):
    """Live state no longer matches the immutable plan. The clip is skipped, never re-planned."""


UNTOUCHED = "untouched"
BACKED_UP = "backed_up"
COMMITTED = "committed"
TORN = "mp4_replaced_sidecar_old"
RESTORED = "restored"
AMBIGUOUS = "ambiguous"


def sha256_file(p) -> str | None:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def stored_fp(sidecar: Path) -> str | None:
    try:
        v = json.loads(sidecar.read_text()).get("fp")
        return v if isinstance(v, str) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def new_run_id(prefix: str, stamp: float | None = None) -> str:
    return prefix + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(stamp if stamp is not None else time.time()))


@dataclass
class RunDirsBase:
    root: Path
    backups: Path
    staging: Path
    journal: Path
    plan: Path
    summary: Path

    def mkdirs(self) -> None:
        for d in (self.root, self.backups, self.staging):
            d.mkdir(parents=True, exist_ok=True)


def inspect_clip(dirs: RunDirsBase, row: dict) -> str:
    """Read ACTUAL disk state and classify it. Ambiguity is a first-class answer."""
    mp4, side = Path(row["media_path"]), Path(row["sidecar_path"])
    bk_mp4 = dirs.backups / f"{row['clip_id']}.mp4"
    pre = row["preimage"]
    cur_mp4 = sha256_file(mp4) if mp4.exists() else None
    cur_fp = stored_fp(side)
    bk_ok = bk_mp4.exists() and sha256_file(bk_mp4) == pre["media_sha256"]

    if cur_mp4 is None:
        return AMBIGUOUS
    if cur_mp4 == pre["media_sha256"] and cur_fp == row["fp_old"]:
        return RESTORED if bk_ok else (BACKED_UP if bk_mp4.exists() else UNTOUCHED)
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_new"]:
        return COMMITTED
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_old"]:
        return TORN
    return AMBIGUOUS


def backup_clip(dirs: RunDirsBase, row: dict) -> dict:
    """Byte-exact backup, verified after copy, never overwritten."""
    cid = row["clip_id"]
    items = [(Path(row["media_path"]), f"{cid}.mp4", row["preimage"]["media_sha256"]),
             (Path(row["sidecar_path"]), f"{cid}.render.json", row["preimage"]["sidecar_sha256"])]
    ass_sha = row["preimage"].get("ass_file_sha256")
    ass_p = Path(row["ass_path"]) if row.get("ass_path") else None
    if ass_sha and ass_p is not None and ass_p.exists():
        items.append((ass_p, f"{cid}.ass", ass_sha))
    out: dict = {}
    for src, name, want in items:
        dst = dirs.backups / name
        if dst.exists():
            got = sha256_file(dst)
            if got != want:
                raise PlanStale(f"{cid}: existing backup {name} sha {got} != planned preimage {want} — "
                                f"refusing to overwrite the only copy of the original")
            out[name] = got
            continue
        if not src.exists():
            continue
        shutil.copy2(src, dst)
        got = sha256_file(dst)
        if got != want:
            raise PlanStale(f"{cid}: backup of {name} verified {got} != {want} — copy is not byte-exact")
        out[name] = got
    return out


def rollback_clip(dirs: RunDirsBase, row: dict) -> dict:
    """Restore the exact original bytes. Verifies backup before trust and after restore."""
    cid = row["clip_id"]
    bk_mp4 = dirs.backups / f"{cid}.mp4"
    bk_side = dirs.backups / f"{cid}.render.json"
    bk_ass = dirs.backups / f"{cid}.ass"
    pre = row["preimage"]
    if not bk_mp4.exists() or not bk_side.exists():
        return {"clip_id": cid, "status": "ROLLBACK_NO_BACKUP"}
    if sha256_file(bk_mp4) != pre["media_sha256"] or sha256_file(bk_side) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_BACKUP_CORRUPT"}
    if (sha256_file(row["media_path"]) == pre["media_sha256"]
            and stored_fp(Path(row["sidecar_path"])) == row["fp_old"]):
        ass_ok = (not pre.get("ass_file_sha256")
                  or (row.get("ass_path") and Path(row["ass_path"]).exists()
                      and sha256_file(row["ass_path"]) == pre["ass_file_sha256"]))
        if ass_ok:
            return {"clip_id": cid, "status": "ROLLBACK_NOOP"}
    pairs = [(bk_mp4, row["media_path"]), (bk_side, row["sidecar_path"])]
    if bk_ass.exists() and pre.get("ass_file_sha256") and row.get("ass_path"):
        pairs.append((bk_ass, row["ass_path"]))
    for bk, dst in pairs:
        tmp = Path(str(dst) + ".rbpart")
        shutil.copy2(bk, tmp)
        os.replace(str(tmp), dst)
    if sha256_file(row["media_path"]) != pre["media_sha256"] or sha256_file(row["sidecar_path"]) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_VERIFY_FAILED"}
    if pre.get("ass_file_sha256") and row.get("ass_path") and sha256_file(row["ass_path"]) != pre["ass_file_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_VERIFY_FAILED"}
    return {"clip_id": cid, "status": "ROLLED_BACK", "media_sha256": pre["media_sha256"]}
