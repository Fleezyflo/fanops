from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Clip, Post, SourceState, ClipState, PostState, Platform
from fanops.agentstep import write_request, response_path
from fanops.digest import render_digest, write_digest


def test_write_digest_failopen_on_oserror(tmp_path, monkeypatch):
    # OPERATIONAL: the digest is a convenience artifact written AFTER the ledger is already committed.
    # An OSError on its write (disk full / permissions) must NOT abort advance()/the CLI verb — it
    # fails open like _archive_published. Otherwise the daemon exits non-zero and launchd respins it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    def boom(self, *a, **k): raise OSError("No space left on device")
    monkeypatch.setattr(Path, "write_text", boom)
    write_digest(led, cfg)                                # must NOT raise (fail-open)

def test_counts_holds_failures(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x", state=SourceState.transcribed))
    led.add_source(Source(id="s2", source_path="/y", state=SourceState.error, error_reason="bad codec"))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c", state=ClipState.held, held=True, held_reason="begging"))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      error_reason="blotato 422"))
    md = render_digest(led, cfg)
    assert "# FAN OPS Ledger Digest" in md
    assert "Sources" in md and "transcribed" in md
    assert "Brand-risk holds" in md and "begging" in md
    assert "Failures" in md and "blotato 422" in md and "bad codec" in md

def test_failures_include_stitch_plan_errors(tmp_path):
    # M3: a stitch_plan in error must surface in the digest's Failures section (operator visibility,
    # required from this milestone) — not only posts/sources/moments/clips.
    from fanops.models import StitchPlan, StitchState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut",
                                   state=StitchState.error, error_reason="compose failed"))
    md = render_digest(led, cfg)
    assert "Failures" in md and "sp1" in md and "compose failed" in md

def test_lists_pending_agent_steps(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_request(cfg, kind="moments", key="s1", payload={"source_id": "s1"})
    write_request(cfg, kind="captions", key="c1", payload={"clip_id": "c1"})
    md = render_digest(led, cfg)
    assert "Awaiting agent" in md and "moments: s1" in md and "captions: c1" in md

def test_write_digest_creates_file(tmp_path):
    from fanops.digest import write_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x", state=SourceState.transcribed))
    write_digest(led, cfg)
    assert cfg.digest_path.exists()
    assert "# FAN OPS Ledger Digest" in cfg.digest_path.read_text()

def test_empty_ledger_digest_has_no_sections(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    md = render_digest(led, cfg)
    assert "# FAN OPS Ledger Digest" in md
    assert "(none)" in md                          # empty stores render (none)
    assert "Brand-risk holds" not in md and "Failures" not in md and "Awaiting" not in md

def test_none_reason_renders_fallback(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed))  # error_reason None
    md = render_digest(led, cfg)
    assert "Failures" in md and "(no reason given)" in md and "None" not in md.split("Failures")[1]

def test_published_unmeasured_surfaced(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # a published post with NO metrics -> surfaced; a published post WITH metrics -> not
    led.add_post(Post(id="pm", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))  # no metrics
    led.add_post(Post(id="pok", parent_id="c", account="@a", account_id="1",
                      platform=Platform.tiktok, caption="y", state=PostState.published,
                      metrics={"saves": 5, "lift_score": 20.0}))
    md = render_digest(led, cfg)
    assert "Published but unmeasured" in md
    assert "`pm`" in md.split("Published but unmeasured")[1]
    assert "`pok`" not in md.split("Published but unmeasured")[1]   # measured one not listed

def test_digest_surfaces_pending_gates(tmp_path):
    # E3: a pending agent gate (request written, no response) MUST surface in the WRITTEN digest
    # under a section whose text contains the literal word "pending" — the existing "Awaiting
    # agent" header does NOT contain "pending", so the header presence is a genuine strengthening.
    #
    # HARDENING (mutation-proven): the E3 section must list the kind+KEY, not a bare count, AND it
    # must EXCLUDE gates a responder has already cleared. We bind both *inside the E3 section slice*
    # — a plain `"moments: s1" in text` would be satisfied by the pre-existing "Awaiting agent"
    # line (digest.py emits the identical `- moments: s1` there), so weakening the E3 list line to a
    # count would still pass. Slicing at the "Pending agent gates" header pins the property to E3.
    import json
    from fanops.digest import write_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # one pending moments gate, plus a captions gate that we then CLEAR (matching response).
    write_request(cfg, kind="moments", key="s1",
                  payload={"source_id": "s1", "transcript_path": "/t", "title": "x"})
    cap_rid = write_request(cfg, kind="captions", key="c1", payload={"clip_id": "c1"})
    # cleared gate: a response echoing the latest request_id -> pending() excludes it.
    response_path(cfg, "captions", "c1").write_text(json.dumps({"clip_id": "c1", "request_id": cap_rid}))
    write_digest(led, cfg)
    text = cfg.digest_path.read_text()
    # the E3 section exists (its header carries the searchable word "pending").
    assert "pending" in text.lower() and "Pending agent gates" in text
    # SCOPE every body assertion to the E3 section, not the whole digest. `[1]` would IndexError if
    # the header were removed — that, plus the substring check, fails the bare-count mutation.
    section = text.split("Pending agent gates")[1]
    assert "moments: s1" in section          # the kind+key listed in E3, not just the bare word
    # a CLEARED gate must NOT appear in the E3 section (it's the gates not-yet-cleared).
    assert "captions: c1" not in section
    # sanity: the cleared key is genuinely absent everywhere downstream of this header too.
    assert "c1" not in section

def test_digest_shows_lift_by_variant(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # Insert the LOSER (HOOK B, lift 30) FIRST and the winner (HOOK A, lift 80) SECOND. Python's
    # sorted() is stable, so a no-op/constant sort key would preserve insertion order — if the winner
    # were inserted first, a silently-broken sort could still render A-above-B and pass. With the
    # loser inserted first, ONLY a genuine descending-by-lift sort yields HOOK A above HOOK B, so the
    # order assertion below kills BOTH a reverse-removal AND a constant-key (no-op) sort mutation.
    led.add_post(Post(id="p2", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram,
                      caption="y", state=PostState.analyzed, variant_key="vk_b", variant_hook="HOOK B",
                      metrics={"lift_score": 30.0}))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, variant_key="vk_a", variant_hook="HOOK A",
                      metrics={"lift_score": 80.0}))
    out = render_digest(led, cfg)
    assert "Lift by variant" in out
    assert "HOOK A" in out and "80" in out          # the winning variant + its lift surface
    assert "HOOK B" in out
    # RANK (not just presence): the winner (HOOK A, lift 80) must render ABOVE the loser
    # (HOOK B, lift 30). A substring-only check let a reversed sort (loser-first) slip through; and
    # because sorted() is stable, the fixture inserts the loser first so a no-op/constant-key sort
    # ALSO fails this — only a real descending-by-lift sort satisfies it.
    assert out.index("HOOK A") < out.index("HOOK B")

def test_digest_flags_lift_degraded_variant(tmp_path):
    # T4: a degraded lift (a high-weight metric was absent from the row) must be VISIBLE next to the
    # number in the "Lift by variant" decision surface — never a write-only field the operator can't see.
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, variant_key="vk_a", variant_hook="HOOK A",
                      metrics={"lift_score": 80.0, "lift_degraded": True, "lift_missing_keys": ["retention", "saves"]}))
    out = render_digest(led, cfg)
    assert "DEGRADED" in out                          # the partial-objective signal is surfaced
    assert "retention" in out and "saves" in out      # names which primary metrics were missing

def test_digest_no_variant_section_when_none(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path)
    out = render_digest(Ledger.load(cfg), cfg)
    assert "Lift by variant" not in out             # absent when no variant posts

def test_digest_variant_shows_gate_state(tmp_path):
    # variation v2 (Task 5): the "Lift by variant" section annotates each surface's LOOP STATE so the
    # operator sees where the learning gate stands — "learning ACTIVE" once a surface crossed the
    # trust gate (>= MIN_POSTS analyzed posts AND beating the runner-up by >= MIN_GAP), else
    # "gathering data". The gate verdict comes from variant_learning.best_hooks (ONE home for the
    # gate logic — same scorer request_captions uses), proving the digest and the caption-bias agree.
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # @a/instagram: PAST GATE — 3 WIN @ lift 90 + 3 LOSE @ lift 10 (gap 80 >= 10, leader has 3 >= 3).
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        led.add_post(Post(id=f"a{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_a{i}", variant_hook=hook, metrics={"lift_score": lift}))
    # @b/instagram: BELOW GATE — a single analyzed post (too few to trust).
    led.add_post(Post(id="b0", parent_id="c1", account="@b", account_id="2",
                      platform=Platform.instagram, caption="y", state=PostState.analyzed,
                      variant_key="vk_b0", variant_hook="LONE", metrics={"lift_score": 50.0}))
    out = render_digest(led, cfg)
    section = out.split("Lift by variant")[1]
    assert "learning ACTIVE" in section and "gathering data" in section   # both states render
    # bind each state to the RIGHT surface line (not just "both words appear somewhere"): the @a line
    # is ACTIVE, the @b line is gathering. A constant/no-op gate would put the same label on both.
    a_line = [ln for ln in section.splitlines() if "@a/instagram" in ln][0]
    b_line = [ln for ln in section.splitlines() if "@b/instagram" in ln][0]
    assert "learning ACTIVE" in a_line and "gathering data" not in a_line
    assert "gathering data" in b_line and "learning ACTIVE" not in b_line

def test_digest_variant_gate_state_failopen(tmp_path, monkeypatch):
    # FAIL-OPEN: a raising best_hooks must NOT lose the whole "Lift by variant" section (a learning
    # failure can never degrade the operator's observability). The lift rows still render; the
    # gate-state annotation simply degrades to "gathering data" (the safe/closed default).
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="a0", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_a0", variant_hook="HOOK A", metrics={"lift_score": 80.0}))
    monkeypatch.setattr("fanops.digest.best_hooks",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = render_digest(led, cfg)                       # must NOT raise
    assert "Lift by variant" in out and "HOOK A" in out and "80" in out   # rows survive the error
    assert "gathering data" in out.split("Lift by variant")[1]            # safe default annotation

def test_needs_reconcile_surfaced(tmp_path):
    # AUDIT C1: a post parked in needs_reconcile (ambiguous publish failure — may be live on the
    # platform) MUST surface so a human verifies via GET /v2/posts/:id before any resubmit. It is
    # NOT a plain failure (re-queueing it could double-post), so it gets its own section.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="prec", parent_id="c", account="@a", account_id="1",
                      platform=Platform.twitter, caption="x", state=PostState.needs_reconcile,
                      error_reason="blotato 503: ambiguous, may be live"))
    md = render_digest(led, cfg)
    assert "Needs reconcile" in md
    section = md.split("Needs reconcile")[1]
    assert "`prec`" in section and "may be live" in section
    # and it must NOT be lumped into the plain Failures bucket
    assert "Failures" not in md or "`prec`" not in md.split("Failures")[1].split("##")[0]


def test_digest_shows_variant_amplify_streak(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for i in range(8):
        led.add_post(Post(id=str(i), parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"v{i}", variant_hook="WIN", metrics={"lift_score": 90.0}))
    for i in range(3):
        led.add_post(Post(id=f"l{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vl{i}", variant_hook="LOSE", metrics={"lift_score": 1.0}))
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 2}
    out = render_digest(led, cfg)
    assert "Variant amplification" in out
    assert "2/3" in out or "streak" in out.lower()      # building-streak state shown


def test_digest_no_amplify_section_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_AMPLIFY", raising=False)
    cfg = Config(root=tmp_path)
    out = render_digest(Ledger.load(cfg), cfg)
    assert "Variant amplification" not in out            # flag off -> section absent


def test_digest_variant_ucb_shows_pick(tmp_path, monkeypatch):
    # variation v3: with UCB on, the per-surface line reports the bandit's PICK ('UCB -> "<hook>"'),
    # not the greedy "learning ACTIVE"/"gathering data" wording. Thin lead (8x LEAD@60 + 1x NEW@59)
    # -> UCB explores the under-sampled NEW (greedy would emit nothing). Operator sees the real pick.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(1, 9):
        led.add_post(Post(id=f"L{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_L{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="N1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_N1", variant_hook="NEW", metrics={"lift_score": 59.0}))
    section = render_digest(led, cfg).split("Lift by variant")[1]
    assert "UCB" in section and "NEW" in section            # the bandit verdict is surfaced
    a_line = [ln for ln in section.splitlines() if "@a/instagram" in ln][0]
    assert "UCB" in a_line and "NEW" in a_line              # on the right surface line

def test_digest_variant_ucb_failopen(tmp_path, monkeypatch):
    # FAIL-OPEN: a raising ucb_rank must not lose the "Lift by variant" section -> degrade to
    # "gathering data" (safe default); the lift rows still render.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setattr("fanops.digest.ucb_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="a0", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_a0", variant_hook="HOOK A", metrics={"lift_score": 80.0}))
    out = render_digest(led, cfg)                          # must NOT raise
    assert "Lift by variant" in out and "HOOK A" in out and "80" in out   # rows survive
    assert "gathering data" in out.split("Lift by variant")[1]            # safe default on error

def test_digest_variant_ucb_off_keeps_v2_wording(tmp_path, monkeypatch):
    # UCB OFF -> the v2 "learning ACTIVE"/"gathering data" wording is UNCHANGED (no "UCB ->" string).
    monkeypatch.delenv("FANOPS_VARIANT_UCB", raising=False)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        led.add_post(Post(id=f"a{i}", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_a{i}", variant_hook=hook, metrics={"lift_score": lift}))
    section = render_digest(led, cfg).split("Lift by variant")[1]
    assert "learning ACTIVE" in section and "UCB ->" not in section


def test_digest_marks_cold_surface_borrowing(monkeypatch, tmp_path):
    # A cold recipient receiving a transferred prior is annotated "borrowing platform signal" — the
    # operator sees transfer is active for that surface (distinct from its own "learning ACTIVE").
    from fanops.models import Moment, MomentState
    from fanops.accounts import Account, Accounts, AccountStatus
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c.mp4", state=ClipState.rendered))
    # @a,@b win STYLE (2 donors); @c is a cold recipient with a single analyzed post (so it APPEARS
    # in the "Lift by variant" section) but no own winner.
    def win(acct, hook):
        rows = [(hook, 90.0)] * 3 + [("LOSE", 10.0)] * 3
        for i, (h, lift) in enumerate(rows):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id="x",
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook=h, metrics={"lift_score": lift}))
    win("@a", "STYLE"); win("@b", "STYLE")
    led.add_post(Post(id="c0", parent_id="clip_1", account="@c", account_id="x",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_c0", variant_hook="COLD", metrics={"lift_score": 50.0}))
    accts = Accounts(cfg)
    accts.accounts = [Account(handle=h, account_id="x", platforms=[Platform.instagram],
                              status=AccountStatus.active, persona="hype") for h in ("@a", "@b", "@c")]
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})    # transfer is VALIDATION-FROZEN — open the gate
    out = render_digest(led, cfg, accounts=accts)
    assert "borrowing platform signal" in out
