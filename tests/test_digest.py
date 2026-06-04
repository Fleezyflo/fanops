from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Clip, Post, SourceState, ClipState, PostState, Platform
from fanops.agentstep import write_request, response_path
from fanops.digest import render_digest

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

def test_digest_shows_lift_by_variant(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, Platform, PostState
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, variant_key="vk_a", variant_hook="HOOK A",
                      metrics={"lift_score": 80.0}))
    led.add_post(Post(id="p2", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram,
                      caption="y", state=PostState.analyzed, variant_key="vk_b", variant_hook="HOOK B",
                      metrics={"lift_score": 30.0}))
    out = render_digest(led, cfg)
    assert "Lift by variant" in out
    assert "HOOK A" in out and "80" in out          # the winning variant + its lift surface
    assert "HOOK B" in out
    # ORDERING is load-bearing: the winner (HOOK A, lift 80) MUST rank ABOVE the loser (HOOK B,
    # lift 30) — the whole point is "which creative is winning". Pin the descending sort so a
    # regression that drops/reverses it is caught (the presence-only checks above would not).
    section = out.split("Lift by variant", 1)[1]
    assert section.index("HOOK A") < section.index("HOOK B")

def test_digest_no_variant_section_when_none(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path)
    out = render_digest(Ledger.load(cfg), cfg)
    assert "Lift by variant" not in out             # absent when no variant posts
