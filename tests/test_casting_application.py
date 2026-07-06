# tests/test_casting_application.py — Phase 1: make per-account moment casting REACH the output.
# The casting BRAIN works (a live probe returned DISJOINT per-persona sets); the bug is APPLICATION timing:
# ingest only applied affinities to `decided` moments, but render flips decided->clipped in the same advance
# the casting request fires, so the answer (a cycle later) found the moment already `clipped` and skipped it
# forever -> permanent fan-to-all. The fix: (1) ingest applies to `clipped` too; (2) crosspost WAITS for the
# casting gate (mirrors how captions gate crosspost); (3) `awaiting` counts moment_casting; (4) the request
# gate re-opens once for a stranded clipped-uncast source. Overlap stays LEGAL (fan-accounts-repost-freely).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, ClipState, MomentState, Fmt, MomentCastingDecision,
                           MomentDecision, MomentPick, AccountSelection, SelectionMethod, account_selection_id)
from fanops.accounts import Accounts
from fanops.casting import request_moment_casting, ingest_moment_casting, casting_gate_pending
from fanops.crosspost import crosspost_clips, _seed_clips
from fanops.moments import ingest_moments
from fanops.agentstep import latest_request_id, response_path, write_request


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, *, persona="x", aid="1", platforms=("instagram", "youtube")):
    return {"handle": handle, "account_id": aid, "platforms": list(platforms), "status": "active", "persona": persona}

def _clipped_moment(*, affinities=None, state=MomentState.clipped):
    return Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                  transcript_excerpt="they slept on me", state=state,
                  affinities=list(affinities) if affinities else [])

def _captioned_clip():
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {f"{h}/{p}": {"caption": "impact.", "hashtags": ["#x"]}
                          for h in ("@a", "@b") for p in ("instagram", "youtube")}
    return clip

def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)

def _src(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080, language="en"))
    return led


# ---- Task 1 + overlap-legal: ingest applies affinities to a CLIPPED moment ----
def test_ingest_applies_affinities_to_clipped_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[])); led.save(); led = Ledger.load(cfg)
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # Task 4: opens for a clipped-uncast moment
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections={"@a": ["mom_1"], "@b": ["mom_1"]}).model_dump_json())
    led = ingest_moment_casting(led, cfg, "src_1", accts)
    assert led.moments["mom_1"].affinities == ["@a", "@b"]            # applied to CLIPPED + overlap stays legal


# ---- Task 2: the casting_gate_pending predicate ----
def test_casting_gate_pending_states(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[]))
    accts = Accounts.load(cfg)
    assert casting_gate_pending(cfg, "src_1") is False                # no request yet -> nothing to wait for
    led = request_moment_casting(led, cfg, "src_1", accts)
    assert casting_gate_pending(cfg, "src_1") is True                 # request open, unanswered -> WAIT
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid, selections={"@a": ["mom_1"]}).model_dump_json())
    assert casting_gate_pending(cfg, "src_1") is False                # answered -> proceed
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    assert casting_gate_pending(cfg, "src_1") is False                # OFF short-circuit


# ---- MOL-149: crosspost uses affinity_admits only — no casting-pending defer; affinities govern in one pass ----
def test_crosspost_fans_scoped_by_affinities_without_casting_defer(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=["@a"])); led.add_clip(_captioned_clip())
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # gate OPEN, unanswered — must NOT defer
    assert latest_request_id(cfg, "moment_casting", "src_1") is not None
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a"}         # affinity gate admits owner only, one pass
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "casting_pending_skip" not in log


# ---- Task 2/OFF firewall: pending is inert + fan-to-all is byte-identical when casting OFF ----
def test_off_firewall_pending_inert_and_fans_all(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    write_request(cfg, kind="moment_casting", key="src_1", payload={"source_id": "src_1", "moments": [], "personas": []})
    assert casting_gate_pending(cfg, "src_1") is False                # OFF -> never waits, even with a request on disk
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=["@a"])); led.add_clip(_captioned_clip())
    accts = Accounts.load(cfg); _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    assert {p.account for p in led.posts.values()} == {"@a", "@b"}    # OFF ignores affinities -> fans to ALL


# ---- Task 3: awaiting counts moment_casting so `fanops run` waits for the gate ----
def test_awaiting_includes_moment_casting(tmp_path):
    from fanops.pipeline import advance
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert "moment_casting" in s["awaiting"] and s["awaiting"]["moment_casting"] == 0


# ---- Task 4: the request gate re-opens ONCE for a stranded clipped-uncast source (idempotent) ----
def test_request_opens_for_clipped_uncast_and_is_write_once(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg); led.add_moment(_clipped_moment(affinities=[]))
    accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # clipped-uncast -> gate opens (was: skipped)
    rid1 = latest_request_id(cfg, "moment_casting", "src_1")
    assert rid1 is not None
    led = request_moment_casting(led, cfg, "src_1", accts)            # write-once: no re-cast storm
    assert latest_request_id(cfg, "moment_casting", "src_1") == rid1


# ---- The HEADLINE promise, pinned END-TO-END: disjoint per-account casts -> disjoint posts ----
def _moment_n(mid, lo, hi):
    return Moment(id=mid, parent_id="src_1", content_token=f"{lo}-{hi}", start=lo, end=hi, reason="r",
                  transcript_excerpt="x", state=MomentState.clipped, affinities=[])

def _captioned_clip_for(mid, cid):
    clip = Clip(id=cid, parent_id=mid, path=f"/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {f"{h}/{p}": {"caption": "impact.", "hashtags": ["#x"]}
                          for h in ("@a", "@b") for p in ("instagram", "youtube")}
    return clip

def test_disjoint_account_selections_yield_disjoint_posts_end_to_end(tmp_path):
    # The product's headline promise, characterized end-to-end (the one gap the audit found): a source cast so
    # @a gets {mom_1,mom_2} and @b gets {mom_3,mom_4} must mint posts on DISJOINT parent clips — @a never posts
    # on @b's moments and vice versa (the RF1 no-fan-leak contract AT THE OUTPUT). Until now this rested on two
    # SEPARATELY-tested halves: account_selection_admits (unit, SimpleNamespace) + the crosspost enforcer (proved
    # only via the legacy single-moment affinities path). This drives the DURABLE AccountSelection multi-moment
    # path through the real crosspost_clips so the promise can't silently regress to fan-to-all.
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg)
    bands = {"mom_1": (0, 7), "mom_2": (8, 15), "mom_3": (16, 23), "mom_4": (24, 31)}
    for i, (mid, (lo, hi)) in enumerate(bands.items(), 1):
        m = _moment_n(mid, lo, hi)
        m.affinities = ["@a"] if mid in ("mom_1", "mom_2") else ["@b"]
        led.add_moment(m); led.add_clip(_captioned_clip_for(mid, f"clip_{i}"))
    led.save(); led = Ledger.load(cfg)
    accts = Accounts.load(cfg)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    posts = list(led.posts.values())
    assert posts, "crosspost minted no posts at all"                  # guard: a silent zero-post run isn't a pass
    def moment_of(p): return led.clips[p.parent_id].parent_id        # post -> parent clip -> its moment
    a_moments = {moment_of(p) for p in posts if p.account == "@a"}
    b_moments = {moment_of(p) for p in posts if p.account == "@b"}
    assert a_moments == {"mom_1", "mom_2"}                            # @a posts ONLY on its cast moments
    assert b_moments == {"mom_3", "mom_4"}                            # @b posts ONLY on its cast moments
    assert a_moments.isdisjoint(b_moments)                           # no cross-account fan leak reached the output
    assert all(p.account in ("@a", "@b") for p in posts)             # no third surface leaked in


# ---- MOM-1: a same-window re-pick must not be governed by a STALE selection ----
def test_re_pick_drops_stale_account_selection(tmp_path, monkeypatch):
    # ROOT: a re-pick (amplify / re-request) changes a source's moment set. ingest_moments discards the casting
    # GATE but (pre-fix) NOT the durable AccountSelection, so a surviving captioned clip could fan on stale
    # casting intent. The fix drops every selection of the re-picked source here (symmetric to the gate discard).
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = _src(cfg)
    led.add_account_selection(AccountSelection(id=account_selection_id("src_1", "@a"), source_id="src_1",
                              account="@a", moment_ids=["mom_stale"], method=SelectionMethod.llm))
    assert led.selections_of_source("src_1")                          # precondition: a prior durable cast exists
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})   # re-pick through the real gate
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(
        MomentDecision(source_id="src_1", request_id=rid,
                       picks=[MomentPick(start=0, end=7, reason="fresh window")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    assert led.selections_of_source("src_1") == []                    # MOM-1: stale selection dropped on re-pick
    assert any(m.parent_id == "src_1" and m.state is MomentState.picked
               for m in led.moments.values())                        # proves it went through the reconcile path


def test_casting_gate_pending_defers_picked_moment_with_led(tmp_path, monkeypatch):
    # MOM-1 (belt): a source with a re-picked (state==picked) moment has had its selections dropped + the gate
    # discarded; a FRESH cast is incoming. With `led` passed, casting_gate_pending treats it as pending so
    # crosspost DEFERS. Legacy callers (no `led`) keep today's gate-file-only behavior (no regression).
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          transcript_excerpt="x", state=MomentState.picked))
    assert casting_gate_pending(cfg, "src_1", led=led) is True        # picked -> re-cast incoming -> defer
    assert casting_gate_pending(cfg, "src_1") is False                # legacy (no led): no gate on disk -> not pending
    led.moments["mom_1"] = led.moments["mom_1"].model_copy(update={"state": MomentState.decided})
    assert casting_gate_pending(cfg, "src_1", led=led) is False       # re-decided -> no longer deferred


def test_seed_clips_excludes_picked_moment_clip(tmp_path):
    # MOM-1 (belt): a surviving captioned clip whose moment is no longer a live render target (re-pick -> picked)
    # must NOT seed a crosspost. A MISSING moment keeps the existing fail-open; a decided/clipped moment seeds.
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          transcript_excerpt="x", state=MomentState.picked))   # re-picked: NOT a render target
    led.add_clip(_captioned_clip())                                  # surviving captioned clip (parent mom_1)
    assert "clip_1" not in {c.id for c in _seed_clips(led)}          # excluded while its moment is `picked`
    led.moments["mom_1"] = led.moments["mom_1"].model_copy(update={"state": MomentState.decided})
    assert "clip_1" in {c.id for c in _seed_clips(led)}              # re-decided -> seeds again


# ---- MOM-4: reconcile prunes orphaned moment_ids from selections (drops a record that empties) ----
def test_orphan_moment_id_pruned_from_selection_on_reconcile(tmp_path):
    # A reconcile that drops mom_2 (e.g. a retire/adjust path that doesn't go through ingest_moments' whole-source
    # drop) must prune it from @a's selection while keeping mom_1 — an orphan id can never post.
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg)
    for mid, (lo, hi) in {"mom_1": (0, 7), "mom_2": (8, 15)}.items():
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token=f"{lo}-{hi}", start=lo, end=hi,
                              reason="r", transcript_excerpt="x", state=MomentState.decided))
    led.add_account_selection(AccountSelection(id=account_selection_id("src_1", "@a"), source_id="src_1",
                              account="@a", moment_ids=["mom_1", "mom_2"], method=SelectionMethod.llm))
    led.reconcile_moments("src_1", {"mom_1": led.moments["mom_1"]})  # keep ONLY mom_1 -> mom_2 cascade-deleted
    sel = led.account_selection_for("src_1", "@a")
    assert sel is not None and sel.moment_ids == ["mom_1"]           # orphan mom_2 pruned, mom_1 kept


def test_selection_dropped_when_all_its_moments_orphaned(tmp_path):
    # A selection whose every moment is dropped EMPTIES -> the record is dropped, never left as an illegal
    # empty-CHOSEN row the sum-type validator would reject on the next load.
    cfg = Config(root=tmp_path); _seed_accounts(cfg, [_acct("@a")])
    led = _src(cfg)
    led.add_moment(Moment(id="mom_2", parent_id="src_1", content_token="8-15", start=8, end=15,
                          reason="r", transcript_excerpt="x", state=MomentState.decided))
    led.add_account_selection(AccountSelection(id=account_selection_id("src_1", "@a"), source_id="src_1",
                              account="@a", moment_ids=["mom_2"], method=SelectionMethod.llm))
    led.reconcile_moments("src_1", {})                              # drop everything
    assert led.account_selection_for("src_1", "@a") is None         # emptied CHOSEN row dropped, not left invalid
    led.save(); assert Ledger.load(cfg) is not None                 # and the ledger reloads (no validator raise)


# ---- coverage gap: the headline disjoint-posts promise driven through the REAL casting GATE (not injected) ----
def test_casting_gate_drives_disjoint_posts_end_to_end(tmp_path, monkeypatch, mocker):
    # The disjoint-posts e2e above INJECTS AccountSelections; this drives the gate path itself —
    # request_moment_casting -> write the LLM decision -> ingest_moment_casting (writes the durable selections +
    # affinities) -> crosspost_clips — so a regression in request/ingest can't hide behind an injected fixture.
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@a", persona="hype"), _acct("@b", persona="lyric", aid="2")])
    led = _src(cfg)
    bands = {"mom_1": (0, 7), "mom_2": (8, 15), "mom_3": (16, 23), "mom_4": (24, 31)}
    for i, (mid, (lo, hi)) in enumerate(bands.items(), 1):
        led.add_moment(_moment_n(mid, lo, hi)); led.add_clip(_captioned_clip_for(mid, f"clip_{i}"))
    led.save(); led = Ledger.load(cfg); accts = Accounts.load(cfg)
    led = request_moment_casting(led, cfg, "src_1", accts)            # open the gate
    rid = latest_request_id(cfg, "moment_casting", "src_1")
    response_path(cfg, "moment_casting", "src_1").write_text(
        MomentCastingDecision(request_id=rid,
                              selections={"@a": ["mom_1", "mom_2"], "@b": ["mom_3", "mom_4"]}).model_dump_json())
    led = ingest_moment_casting(led, cfg, "src_1", accts)             # the gate writes the durable selections
    assert led.account_selection_for("src_1", "@a").moment_ids == ["mom_1", "mom_2"]
    assert led.account_selection_for("src_1", "@b").moment_ids == ["mom_3", "mom_4"]
    _fake_ffmpeg(mocker)
    led = crosspost_clips(led, cfg, accts, base_time="2026-06-02T18:00:00Z")
    posts = list(led.posts.values())
    assert posts, "casting-gate-driven crosspost minted no posts"
    def moment_of(p): return led.clips[p.parent_id].parent_id
    a_moments = {moment_of(p) for p in posts if p.account == "@a"}
    b_moments = {moment_of(p) for p in posts if p.account == "@b"}
    assert a_moments == {"mom_1", "mom_2"}                           # @a posts ONLY on its gate-cast moments
    assert b_moments == {"mom_3", "mom_4"}                           # @b posts ONLY on its gate-cast moments
    assert a_moments.isdisjoint(b_moments)                          # no cross-account fan leak through the gate path
