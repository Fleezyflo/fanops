# src/fanops/hashtag_migrate.py
"""R4 migration — retire the polluted corpora and upgrade the evidence store IN PLACE, once, reversibly.

Why a migration and not a fix-forward: the corpora live in `00_control/personas.json`, which is LIVE
BRAND DATA, not repo data. It cannot be corrected by a code change or a deploy; something has to rewrite
the operator's file. So this does the smallest such rewrite it can, and makes it reviewable:

  * SNAPSHOT first (`<file>.r4-bak-<utc>`), always, before a single byte changes — rollback is a copy back.
  * IDEMPOTENT: re-running changes nothing (`changed: 0`). The migration is not a state machine; it is a
    convergence to a declared target, so a half-finished or repeated run is safe.
  * HONEST provenance: a legacy bare `reach` number becomes `source: "unknown", measured_at: None`. It is
    NOT back-dated into `graph-reach` evidence. We genuinely do not know where those numbers came from —
    `research_corpus` therefore refuses to curate on them, which is the correct consequence of not knowing.
  * NO FABRICATED REACH: not one number is invented, adjusted, or carried across tags. Measurements that
    survive are copied verbatim; the rest stay absent.

The curated target below is a HUMAN CALL derived from repository evidence, not an inference the code
makes at runtime — which is exactly the separation R4 exists to draw."""
from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.hashtags import _norm, load_store_evidence
from fanops.hashtag_hygiene import screen_corpus, tag_defect

# The curated corpora, derived 2026-07-16 from the live catalogue in 00_control/ledger.sqlite (347 clips /
# 347 moments / 7 sources) — NOT from the store, and NOT from tag popularity.
#
# What the catalogue actually is: long-form interviews with a SYRIAN rapper based in the Gulf ("I'm a
# Syrian passport holder and I live in the Gulf"; "written and recorded five, six records in Arabic
# already"), talking about craft, money, artistry and the industry. Recurring subject tokens across all
# 347 transcripts: music 94, money 65, song/life 33, artists 27, record 26, art 22, meaning 21, timeless
# 19, business 19, arab 30, syria 16, bars 13, album 13.
#
# What was there instead, and why each had to go:
#   * #methodman #wuwear #90shiphop #rza #wutang #ghostfacekillah #wutangclan #cappadonna #wutangbrand —
#     the Wu-Tang Clan. A DIFFERENT ARTIST. Not "generic": categorically wrong, and it was 93% of one
#     handle's shipped output.
#   * #taylorswift #80s — wrong artist, wrong era, wrong genre.
#   * #instagood #love #art #explore #explorepage #highlights #post #trending #missviralchallenge
#     #spotify — engagement bait; describes no clip (now refused structurally by hashtag_hygiene).
#   * #fypppppppppp…(73 p's) — malformed keysmash that shipped live (now refused structurally).
#   * #viral #reels — real, but the platform DISCOVERY floor already grants one per platform; a curated
#     copy burns a brand slot for reach the selector gives away (now refused structurally).
#   * #celebritygossip #drama #popculture #entertainment #facts #science — off-catalogue for a rap artist;
#     these rode in from DORMANT personas (no linked account) yet still seeded the store.
#
# Deliberately SMALL and overlapping. Post-#679 the corpus no longer carries differentiation: it holds 2
# slots of brand identity (_CORPUS_LEAD_MAX) while the clip's own vetted picks hold the other 2, and H2's
# LRU rotates within the corpus. Padding these to a quota would only re-crowd the clip out of its own line.
#
# Region: NOT forced here. 6 of 7 sources are English; `_ARABIC` is already floored by `vet_hashtags` on
# ar-language clips, where it is evidenced. Putting #arabicmusic on an English interview would mis-describe
# the clip — the same defect as #taylorswift, wearing a better coat. Escalate to the operator to reposition.
CURATED: dict[str, list[str]] = {
    # ACTIVE — these ship (markmakmouly, hrmny-blog)
    "craft-curator":     ["#bars", "#lyrics", "#hiphopmusic"],            # voice: "champions craft — a clean bar, a lyric that lands"
    # ACTIVE — (perca.late, backlikeineverleft)
    "underground-zine":  ["#undergroundhiphop", "#freestyle", "#rap"],    # voice: "blunt underground zine, raw/unfiltered"
    # ACTIVE — (cisumwolfhom)
    "burner-bold":       ["#hiphop", "#rapmusic", "#rapper"],             # voice: "unfiltered, no shame, no brand-safety filter"
    # DORMANT (no linked account) — cleaned anyway because _seed_tags seeds the store from EVERY persona,
    # which is how #science and #celebritygossip reached a rap artist's tag menu.
    "credibility-first": ["#podcast", "#interview"],                      # the catalogue IS long-form interviews
    "controversy":       ["#hiphop", "#rap"],                             # the arguments are about music, not celebrities
    "edutainment":       ["#hiphop", "#lyrics", "#newmusic"],
    "cliffhanger":       ["#podcast", "#storytime"],
    "hype-vibe":         ["#hiphop", "#rap", "#bars", "#undergroundhiphop"],   # already catalogue-valid; kept
}


# Why a specific tag left, where the reason is a real catalogue finding rather than a structural rule.
# Only asserted where it is TRUE — a tag absent from a persona's curated set is not thereby "off-catalogue"
# (#trap and #newmusic are perfectly valid for this artist; they simply lost a slot to a better one, and
# the corpus is deliberately small). Claiming otherwise in a report about someone's own brand data would be
# a confident falsehood, which is worse than a vague truth.
_CURATION_NOTES: dict[str, str] = {
    "#taylorswift": "different artist and genre — not this catalogue",
    "#80s": "wrong era — the catalogue is contemporary",
    "#methodman": "Wu-Tang Clan — a different artist", "#wuwear": "Wu-Tang Clan merch — a different artist",
    "#rza": "Wu-Tang Clan — a different artist", "#wutang": "Wu-Tang Clan — a different artist",
    "#ghostfacekillah": "Wu-Tang Clan — a different artist", "#wutangclan": "Wu-Tang Clan — a different artist",
    "#cappadonna": "Wu-Tang Clan — a different artist", "#wutangbrand": "Wu-Tang Clan — a different artist",
    "#90shiphop": "wrong era — the catalogue is contemporary",
    "#celebritygossip": "off-catalogue — this is a music artist, not a gossip feed",
    "#drama": "off-catalogue — this is a music artist, not a gossip feed",
    "#popculture": "off-catalogue — too broad, and not what the interviews are about",
    "#entertainment": "off-catalogue — too broad to describe any clip",
    "#facts": "off-catalogue — inherited from a dormant science persona",
    "#science": "off-catalogue — inherited from a dormant science persona",
    "#music": "too broad to describe any clip", "#artist": "too broad to describe any clip",
    "#songs": "too broad to describe any clip",
}
_DROPPED_BY_CURATION = "dropped by curation — valid, but the corpus is deliberately small (2 of 4 slots)"


def _drop_reason(tag: str) -> str:
    """The honest reason `tag` is not in the curated target: structural defect > known catalogue finding >
    'curation kept something better'. Never asserts off-catalogue for a tag we have no such finding on."""
    return tag_defect(tag) or _CURATION_NOTES.get(tag) or _DROPPED_BY_CURATION


def _snapshot(path, stamp: str) -> str | None:
    """Copy `path` to `<path>.r4-bak-<stamp>` before it is touched. Returns the backup path, or None when
    there was nothing to back up. shutil.copy2 preserves mtime, so a rollback restores the throttle state
    the store's mtime encodes (refresh_store_if_due reads it) rather than forcing a spurious refresh."""
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + f".r4-bak-{stamp}")
    shutil.copy2(path, bak)
    return str(bak)


def migrate_corpora(cfg: Config, *, now=None, apply: bool = True) -> dict:
    """Converge every persona corpus onto CURATED, and upgrade the store's reach map to evidence records.
    `apply=False` is a dry run: it computes and returns the identical report and writes NOTHING (no
    snapshot either), so the diff can be reviewed before any live byte moves.

    Idempotent by construction: it compares against the declared target and returns `changed: 0` when
    already converged. Never fabricates a measurement."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    report: dict = {"changed": 0, "personas": [], "backups": [], "store": {}, "applied": bool(apply),
                    "missing": []}

    # A migration that cannot FIND its data must say so, not report a clean "0 changes". Pointed at the
    # wrong root this returned success while touching nothing — silent-success on a no-op is the same
    # failure shape as the refresh that erased `reach` for months without a word. Record the miss.
    for label, path in (("personas.json", cfg.personas_path), ("hashtags.json", cfg.hashtags_path)):
        if not path.exists():
            report["missing"].append(f"{label} not found at {path}")

    # ---- personas -------------------------------------------------------------------------------
    p = cfg.personas_path
    if p.exists():
        raw = json.loads(p.read_text())
        plist = raw.get("personas") if isinstance(raw, dict) else None
        if isinstance(plist, list):
            dirty = False
            for d in plist:
                if not isinstance(d, dict):
                    continue
                pid = d.get("id")
                before = [t for t in (d.get("hashtag_corpus") or []) if isinstance(t, str)]
                target = CURATED.get(pid)
                if target is None:                       # a persona we have no curated opinion on: strip
                    after, rejected = screen_corpus(before)   # structural junk only, keep the operator's taste
                else:
                    after = [_norm(t) for t in target]
                    rejected = {_norm(t): _drop_reason(_norm(t))
                                for t in before if _norm(t) not in set(after)}
                if after == before:
                    continue
                dirty = True
                report["changed"] += 1
                report["personas"].append({"id": pid, "before": before, "after": after,
                                           "removed": sorted(rejected), "reasons": rejected})
                d["hashtag_corpus"] = after
                # Provenance: every curated tag is `pinned` — a HUMAN put it there. Pinned is also what stops
                # refresh_persona_corpus reclaiming the slot as an `auto` entry (_partition_corpus/_is_pinned),
                # so the curated set cannot be silently rewritten by a daemon tick. reach stays None: we have
                # measured nothing for these, and saying so is the point.
                meta = d.get("hashtag_corpus_meta") if isinstance(d.get("hashtag_corpus_meta"), dict) else {}

                def _added(t, _meta=meta):
                    m = _meta.get(t)
                    prior = m.get("added") if isinstance(m, dict) else None
                    return prior or f"{stamp} (r4-curated)"    # keep a real prior date; never re-stamp a surviving tag

                d["hashtag_corpus_meta"] = {t: {"source": "pinned", "reach": None, "added": _added(t)}
                                            for t in after}
            if dirty and apply:
                b = _snapshot(p, stamp)
                if b: report["backups"].append(b)
                write_json_atomic(p, raw)

    # ---- store: bare numbers -> honest evidence records ------------------------------------------
    s = cfg.hashtags_path
    if s.exists():
        d = json.loads(s.read_text())
        if isinstance(d, dict) and isinstance(d.get("reach"), dict):
            legacy = {k: v for k, v in d["reach"].items() if not isinstance(v, dict)}
            if legacy:
                ev = load_store_evidence(cfg)            # already normalizes legacy -> source "unknown"
                d["reach"] = {t: ev[t] for t in ev}      # verbatim values; NO back-dated measured_at
                report["store"] = {"upgraded": len(legacy), "marked_unknown": len(legacy)}
                report["changed"] += 1
                if apply:
                    b = _snapshot(s, stamp)
                    if b: report["backups"].append(b)
                    write_json_atomic(s, d)
            else:
                report["store"] = {"upgraded": 0, "marked_unknown": 0}
    return report


def cmd_hashtags_migrate(cfg: Config, *, apply: bool = False) -> int:
    """`fanops hashtags migrate [--apply]` — DRY RUN by default. Prints the exact per-persona diff and the
    reason each tag is dropped, so the operator reviews their own brand data before it moves. Never raises:
    a torn control file reports and exits 2 rather than half-writing."""
    from fanops.log import get_logger
    try:
        r = migrate_corpora(cfg, apply=apply)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
        print(f"migrate failed (nothing written): {e}")
        return 2
    head = "APPLIED" if apply else "DRY RUN — nothing written (re-run with --apply)"
    print(f"R4 hashtag migration — {head}")
    for m in r.get("missing", []):
        print(f"  ! {m}")
    if r.get("missing") and not r["changed"]:
        print("\n  NOTHING TO MIGRATE — the control files were not found (wrong --root?). Not a success.")
        return 2
    for p in r["personas"]:
        print(f"\n  {p['id']}")
        print(f"    before ({len(p['before']):2d}): {' '.join(p['before'])}")
        print(f"    after  ({len(p['after']):2d}): {' '.join(p['after'])}")
        for t in p["removed"]:
            print(f"      - {t:22s} {p['reasons'].get(t, '')}")
    if r["store"]:
        print(f"\n  store: {r['store'].get('upgraded', 0)} legacy reach number(s) -> evidence records, "
              f"marked source=unknown (provenance is genuinely unknown; NOT back-dated)")
    for b in r["backups"]:
        print(f"\n  rollback snapshot: {b}")
    print(f"\n{r['changed']} change(s). Re-running is a no-op (idempotent).")
    if apply:
        get_logger(cfg)("hashtags", "-", "r4_migrated", changed=r["changed"], backups=len(r["backups"]))
    return 0
