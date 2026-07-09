import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Clip, Post, Moment, MomentState, SourceState, Platform,
                           MomentDecision, MomentPick, MomentHookDecision, PostState, ClipState)
from fanops.agentstep import response_path, request_path, latest_request_id, pending
from fanops.models import PERSONA_PICK_SPEC_KEYS
from fanops.moments import (request_moments, ingest_moments, request_moment_hooks,
                            ingest_moment_hooks, validate_pick, _drop_overlaps, _owned_moment_id,
                            _pick_personas)
from fanops.adjust import amplify
from fanops.ids import child_id

# M1b (frame-seeing two-pass): the moment gate is split. PASS 1 (request_moments/ingest_moments) picks
# the WINDOWS -> moments are born `picked` (NOT renderable) and the source lands `picks_decided`. PASS 2
# (request_moment_hooks/ingest_moment_hooks) authors the on-screen hook seeing each picked window's frames
# -> promotes picked -> decided and the source to `moments_decided`. The is_weak_hook/narration floor that
# used to live in ingest_moments now runs in ingest_moment_hooks on the window-grounded hooks.

def _mp(s, e, reason="r"):
    return MomentPick(start=s, end=e, reason=reason)

def _ingest_picks(led, cfg, source_id, picks):
    """PASS 1: write a MomentDecision response + ingest -> `picked` moments / `picks_decided`."""
    from fanops.responder import screen_model_text
    rid = latest_request_id(cfg, "moments", source_id)
    dec = screen_model_text(MomentDecision(source_id=source_id, request_id=rid, picks=picks))
    response_path(cfg, "moments", source_id).write_text(dec.model_dump_json())
    return ingest_moments(led, cfg, source_id)

def _decide_hooks(led, cfg, source_id, hooks=None, accounts=None):
    """PASS 2 for every `picked` moment of the source: open the per-pick hook gates, answer each from
    `hooks` (token -> hook str, or token -> (hook, hooks_by_persona)), then ingest -> `decided`. A token
    absent from `hooks` is answered with hook=null (a clean clip)."""
    hooks = hooks or {}
    led = request_moment_hooks(led, cfg, source_id, accounts=accounts)
    for m in [m for m in led.moments.values()
              if m.parent_id == source_id and m.state is MomentState.picked]:
        spec = hooks.get(m.content_token)
        hook = spec[0] if isinstance(spec, tuple) else spec
        key = f"{source_id}.{m.content_token}"
        rid = latest_request_id(cfg, "moment_hooks", key)
        from fanops.responder import screen_model_text
        dec = screen_model_text(MomentHookDecision(request_id=rid, hook=hook))
        response_path(cfg, "moment_hooks", key).write_text(dec.model_dump_json())
    return ingest_moment_hooks(led, cfg, source_id, accounts=accounts)

def _src(led, cfg, dur=20.0):
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.signalled, duration=dur, language="en",
                          transcript=[{"start": 0, "end": 3, "text": "intro"},
                                      {"start": 14, "end": 18, "text": "they slept on me"}],
                          signal_peaks=[{"t": 16.0, "kind": "scene_cut", "score": 0.6}],
                          meta={"transcribed": True}))

# --- PASS 1: picks ----------------------------------------------------------------------------------
def test_drop_overlaps_keeps_first_drops_near_dupe():
    kept = _drop_overlaps([_mp(0, 18), _mp(5, 20), _mp(40, 58)])
    assert [(p.start, p.end) for p in kept] == [(0, 18), (40, 58)]

def test_drop_overlaps_all_overlap_keeps_first():
    kept = _drop_overlaps([_mp(0, 18), _mp(3, 20), _mp(5, 19)])
    assert len(kept) == 1 and (kept[0].start, kept[0].end) == (0, 18)

def test_drop_overlaps_disjoint_all_kept():
    assert len(_drop_overlaps([_mp(0, 15), _mp(20, 35), _mp(40, 55)])) == 3

def _mpo(s, e, owner, reason="r"):
    return MomentPick(start=s, end=e, reason=reason, personas=[owner])

# MOL-169 (RF-D4): overlap-dedup is a WITHIN-owner near-duplicate filter. Two DIFFERENT owners at the
# same timecode are two LEGITIMATE moments (single-owner rebuild) — dropping one is the ghost the
# picking rebuild killed. Cross-owner overlap must never drop a pick.
def test_within_persona_overlap_deduped():
    kept = _drop_overlaps([_mpo(0, 18, "a"), _mpo(5, 20, "a"), _mpo(40, 58, "a")])
    assert [(p.start, p.end) for p in kept] == [(0, 18), (40, 58)]   # within-owner dedup unchanged

def test_cross_persona_overlap_kept():
    # a's window and b's window overlap in time but are DIFFERENT owners -> BOTH kept (the fix).
    kept = _drop_overlaps([_mpo(0, 18, "a"), _mpo(5, 20, "b")])
    assert len(kept) == 2
    assert {(p.personas[0], p.start, p.end) for p in kept} == {("a", 0, 18), ("b", 5, 20)}

def test_drop_overlaps_blind_picks_still_deduped():
    # persona-blind picks (no owner) all share the None owner -> within-that-owner dedup as before.
    kept = _drop_overlaps([_mp(0, 18), _mp(5, 20), _mp(40, 58)])
    assert [(p.start, p.end) for p in kept] == [(0, 18), (40, 58)]

def test_named_threshold_is_sole_overlap_constant():
    # The overlap threshold has exactly ONE named home; no bare 0.5 magic overlap literal elsewhere.
    import fanops.moments as M
    assert M._MAX_OVERLAP_FRAC == 0.5
    # _spans_overlap is the only comparator; it must reference the named constant, never a literal.
    fn = __import__("inspect").getsource(M._spans_overlap)
    assert "_MAX_OVERLAP_FRAC" in fn and "0.5" not in fn

def test_drop_overlaps_single_home():
    # Grep-proof: moments.py is the ONLY timecode overlap-dedup site (one _drop_overlaps, one comparator).
    src = __import__("inspect").getsource(__import__("fanops.moments", fromlist=["x"]))
    assert src.count("def _drop_overlaps(") == 1
    assert src.count("def _spans_overlap(") == 1

def test_ingest_picks_lands_picked_not_decided(tmp_path):
    # A pick is born `picked` (NOT renderable) and the source lands `picks_decided` — the hook gate is
    # still owed. Render keys on `decided`, so a picked moment never renders hookless.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=10.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(0.0, 10.0, "whole short source")])
    moms = led.moments_of("src_1")
    assert len(moms) == 1 and moms[0].state is MomentState.picked and moms[0].hook is None
    assert led.sources["src_1"].state is SourceState.picks_decided   # NOT moments_decided yet, NOT error

def test_ingest_overlapping_picks_deduped(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(0, 18), _mp(5, 20), _mp(40, 58)])
    assert len(led.moments_of("src_1")) == 2          # the near-dupe middle pick dropped

def test_ingest_empty_picks_visible_not_silent_cascade(tmp_path):
    # V2 M1/F8: the model returns [] -> source ends moments_empty (VISIBLE + re-runnable), NOT the
    # look-alike that hid 'nothing was produced'. A PRIOR moment is PRESERVED (no cascade-delete).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(10, 28, "first")])
    assert len(led.moments_of("src_1")) == 1
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [])
    assert led.sources["src_1"].state is SourceState.moments_empty       # visible, non-terminal
    assert len(led.moments_of("src_1")) == 1                             # prior moment preserved

def test_consumer_receives_clean_text(tmp_path):
    # MOL-166: ingest_moments trusts the responder-screened response — no downstream re-sanitize.
    import inspect
    from fanops import moments as moments_mod
    src = inspect.getsource(moments_mod.ingest_moments)
    assert "sanitize_generated_text" not in src
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="punchline, then the beat drops")])
    assert led.moments_of("src_1")[0].reason == "punchline, then the beat drops"

def test_affinity_birth_path_intact(tmp_path):
    # MOL-142: ingest_moments stamps Moment.affinities from pick.personas at birth (single-owner convention).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="punchline", personas=["a"])])
    m = led.moments_of("src_1")[0]
    assert m.affinities == ["a"]

# ---- MOL-146 (P5): atomic whole-source ingest + owner-stamped spec ----
def _seed_owner_spec_accounts(cfg, specs):
    """specs: list of {handle, clip_profile?, framing?} dicts."""
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, s in enumerate(specs):
        rows.append({"handle": s["handle"], "account_id": str(i + 1), "platforms": ["instagram"],
                     "status": "active", "persona": f"voice {s['handle']}", "content_focus": ["punchlines"],
                     "selection_scope": "credibility_first", "hook_angle": "curiosity",
                     "hashtag_corpus": [f"#tag{i}"],
                     **{k: v for k, v in s.items() if k in ("clip_profile", "framing")}})
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))
    return Accounts.load(cfg)

def test_ingest_single_gate_reconciles_whole_source(tmp_path):
    # P5 data-loss guard: N picks across M owners in ONE gate -> all minted, none cascade-deleted.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    picks = [MomentPick(start=10, end=28, reason="a window", personas=["a"]),
             MomentPick(start=40, end=58, reason="b window", personas=["b"])]
    led = _ingest_picks(led, cfg, "src_1", picks)
    moms = led.moments_of("src_1")
    assert len(moms) == 2
    owners = {tuple(m.affinities) for m in moms}
    assert owners == {("a",), ("b",)}

def test_ingest_overlapping_different_owners_both_minted(tmp_path):
    # MOL-169: two owners whose windows OVERLAP in time are two legitimate moments — neither dropped.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    picks = [MomentPick(start=10, end=28, reason="a window", personas=["a"]),
             MomentPick(start=12, end=30, reason="b window (overlaps a)", personas=["b"])]
    led = _ingest_picks(led, cfg, "src_1", picks)
    moms = led.moments_of("src_1")
    assert len(moms) == 2                                   # cross-owner overlap kept BOTH
    assert {tuple(m.affinities) for m in moms} == {("a",), ("b",)}

def test_ingest_logs_owner_pick_counts(tmp_path, mocker):
    # MOL-478 (T5): visibility breadcrumb — per-owner pick counts alongside overlaps_dropped/zero_moments.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    logfn = mocker.patch("fanops.moments.get_logger").return_value
    picks = [MomentPick(start=10, end=28, reason="a1", personas=["a"]),
             MomentPick(start=30, end=48, reason="a2", personas=["a"]),
             MomentPick(start=12, end=30, reason="b1", personas=["b"])]
    led = _ingest_picks(led, cfg, "src_1", picks)
    owner_logs = [c for c in logfn.call_args_list if c.args[2:3] == ("owner_picks",)]
    assert len(owner_logs) == 1
    assert owner_logs[0].kwargs.get("a") == 2 and owner_logs[0].kwargs.get("b") == 1

def test_ingest_owner_becomes_affinities_and_stamps_spec(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    _seed_owner_spec_accounts(cfg, [{"handle": "@a", "clip_profile": "short", "framing": "top"}])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="punchline", personas=["a"])])
    m = led.moments_of("src_1")[0]
    assert m.affinities == ["a"] and m.clip_profile == "short" and m.framing == "top"

def test_ingest_pending_defers_whole_source(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    assert led.sources["src_1"].state is SourceState.moments_requested
    led = ingest_moments(led, cfg, "src_1")   # no response yet
    assert led.sources["src_1"].state is SourceState.moments_requested
    assert len(led.moments_of("src_1")) == 0

def test_ingest_persona_blind_empty_affinities(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(10, 28, "blind pick")])
    m = led.moments_of("src_1")[0]
    assert m.affinities == [] and m.clip_profile is None and m.framing is None

# ---- MOL-147 (P6): one owner, one hook — no hooks_by_persona map-building ---------------------------
def test_hook_request_sends_only_owner(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    accts = _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="a window", personas=["a"])])
    led = request_moment_hooks(led, cfg, "src_1", accounts=accts)
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.10.00-28.00").read_text())
    assert len(req["personas"]) == 1
    assert req["personas"][0]["handle"] == "a"
    assert "b" not in {p["handle"] for p in req["personas"]}

def test_hook_applied_to_m_hook_single(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    accts = _seed_owner_spec_accounts(cfg, [{"handle": "@a"}])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="punchline", personas=["a"])])
    led = _decide_hooks(led, cfg, "src_1", {"10.00-28.00": "the part you'll replay"}, accounts=accts)
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided
    assert m.hook == "the part you'll replay"

def test_persona_blind_hook_falls_back_shared(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    accts = _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(10, 28, "blind pick")])
    led = request_moment_hooks(led, cfg, "src_1", accounts=accts)
    req = json.loads(request_path(cfg, "moment_hooks", "src_1.10.00-28.00").read_text())
    assert req["personas"] == []
    led = _decide_hooks(led, cfg, "src_1", {"10.00-28.00": "wait for the switch"}, accounts=accts)
    m = led.moments_of("src_1")[0]
    assert m.hook == "wait for the switch"

def test_ingest_no_skip_state_fields(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    _seed_owner_spec_accounts(cfg, [{"handle": "@a"}, {"handle": "@b"}])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="a", personas=["a"]),
                         MomentPick(start=40, end=58, reason="b", personas=["b"])])
    src = led.sources["src_1"]
    forbidden = ("moments_wait_cycles", "moments_skipped_handles", "skip_state")
    assert not any(k in (src.meta or {}) for k in forbidden)
    assert not hasattr(src, "moments_wait_cycles")

def _seed_pick_persona_accounts(cfg, handle="a"):
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [{
        "handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active",
        "persona": "underground grit", "content_focus": ["punchlines"],
        "selection_scope": "credibility_first", "hook_angle": "curiosity",
        "hashtag_corpus": ["#detroitrap", "#bars"]}]}))
    return Accounts.load(cfg)

def _seed_multi_pick_persona_accounts(cfg, handles):
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": str(i + 1), "platforms": ["instagram"], "status": "active",
         "persona": f"voice {h}", "content_focus": ["punchlines"],
         "selection_scope": "credibility_first", "hook_angle": "curiosity",
         "hashtag_corpus": [f"#tag{i}"]} for i, h in enumerate(handles)]}))
    return Accounts.load(cfg)

def test_pick_personas_returns_full_spec(tmp_path):
    cfg = Config(root=tmp_path); accts = _seed_pick_persona_accounts(cfg, "raw")
    specs = _pick_personas(cfg, accts)
    assert len(specs) == 1
    assert set(specs[0]) == PERSONA_PICK_SPEC_KEYS
    assert specs[0]["handle"] == "raw"
    assert "punchline" in specs[0]["directive"].lower()
    assert "sensational" in specs[0]["selection_scope"].lower() or specs[0]["selection_scope"]
    assert specs[0]["band"] and "s" in specs[0]["band"]
    assert specs[0]["framing"] in ("top", "center")
    assert specs[0]["hook_angle"] == "curiosity"
    assert specs[0]["corpus"] == ["#detroitrap", "#bars"]

def test_pick_personas_empty_when_casting_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); accts = _seed_pick_persona_accounts(cfg)
    assert _pick_personas(cfg, accts) == []
    led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1", accounts=accts)
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert "personas" not in payload

def test_pick_request_carries_resolved_persona_spec(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_pick_persona_accounts(cfg, "a")
    led = request_moments(led, cfg, "src_1", accounts=accts)
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert len(payload["personas"]) == 1
    assert PERSONA_PICK_SPEC_KEYS <= set(payload["personas"][0])
    assert "signal_peaks" in payload["personas"][0]

def test_request_writes_one_source_gate(tmp_path):
    # P4 (MOL-145): N personas ride ONE source-keyed gate — no per-handle fork, no '#' in the key.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_multi_pick_persona_accounts(cfg, ["a", "b"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    gate_dir = request_path(cfg, "moments", "src_1").parent
    moment_gates = [p.name for p in gate_dir.glob("moments__*.request.json")]
    assert moment_gates == ["moments__src_1.request.json"]
    assert "#" not in moment_gates[0]
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert len(payload["personas"]) == 2
    assert {p["handle"] for p in payload["personas"]} == {"a", "b"}

def test_request_packs_full_persona_spec_list(tmp_path):
    # P4: each persona entry carries the full P4a resolved spec (handle through corpus).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_multi_pick_persona_accounts(cfg, ["x", "y"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    for spec in payload["personas"]:
        assert PERSONA_PICK_SPEC_KEYS <= set(spec)
        assert "signal_peaks" in spec
        assert spec["handle"] in ("x", "y")
        assert spec["directive"] and spec["band"] and spec["framing"]
        assert spec["selection_scope"] and spec["hook_angle"] == "curiosity"
        assert isinstance(spec["corpus"], list)

def test_request_reuses_frames_once(tmp_path, mocker):
    # P4: whole-source frames are extracted ONCE per request (persona-blind visual survey).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    (cfg.sources / "src_1.mp4").parent.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_1.mp4").write_bytes(b"\x00")
    spy = mocker.patch("fanops.moments.extract_keyframes", return_value=["/k/a.jpg"])
    accts = _seed_multi_pick_persona_accounts(cfg, ["a", "b"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    assert spy.call_count == 1
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["frames"] == ["/k/a.jpg"]

def test_request_zero_personas_drops_key(tmp_path, monkeypatch):
    # P4: empty personas -> drop the key; persona-blind path byte-identical to no-accounts call.
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_pick_persona_accounts(cfg)
    request_moments(led, cfg, "src_1")
    payload_blind = json.loads(request_path(cfg, "moments", "src_1").read_text())
    led2 = Ledger.load(cfg); _src(led2, cfg)
    request_moments(led2, cfg, "src_1", accounts=accts)
    payload_cast_off = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert "personas" not in payload_blind and "personas" not in payload_cast_off
    for k in ("duration", "transcript", "signal_peaks", "language", "clip_profile"):
        assert payload_blind[k] == payload_cast_off[k]

def test_request_adds_no_skip_state(tmp_path):
    # P4: request_moments must NOT invent wait-cycle / skip-state machinery on the source.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = _seed_multi_pick_persona_accounts(cfg, ["a", "b"])
    led = request_moments(led, cfg, "src_1", accounts=accts)
    src = led.sources["src_1"]
    assert src.state is SourceState.moments_requested
    forbidden = ("moments_wait_cycles", "moments_skipped_handles", "skip_state")
    assert not any(k in (src.meta or {}) for k in forbidden)

def test_amplify_gate_still_read(tmp_path):
    # P4: amplify keeps writing the source-keyed moments gate (no #handle fork) — path intact.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", state=SourceState.moments_decided,
                          duration=30.0, transcript=[{"start": 14, "end": 18, "text": "they slept on me"}],
                          signal_peaks=[], meta={"transcribed": True}))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="14-21", start=14, end=21,
                          reason="punchline", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      metrics={"lift_score": 400}, public_url="dryrun://1"))
    led = amplify(led, cfg, ["p1"])
    assert request_path(cfg, "moments", "src_1").exists()
    assert not request_path(cfg, "moments", "src_1#@a").exists()
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["source_id"] == "src_1" and "AMPLIFY" in payload["guidance"]
    assert latest_request_id(cfg, "moments", "src_1") is not None

def test_request_moments_writes_pick_request_with_transcript_signals_language(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["duration"] == 20.0
    assert payload["transcript"][1]["text"] == "they slept on me"
    assert payload["signal_peaks"][0]["t"] == 16.0
    assert payload["language"] == "en"
    assert "request_id" in payload
    assert "personas" not in payload                 # persona-blind when no accounts passed
    assert led.sources["src_1"].state is SourceState.moments_requested

def test_request_moments_carries_clip_profile(tmp_path, monkeypatch):
    # The content-type profile (talk/song) must travel IN the request payload so the model is ASKED for
    # band-appropriate picks — moment_pick_prompt reads payload["clip_profile"], it has no cfg.
    monkeypatch.setenv("FANOPS_CLIP_PROFILE", "song")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["clip_profile"] == "song"

def test_validate_pick_rejects_bad_bounds():
    assert validate_pick(MomentPick(start=5, end=3, reason="r"), duration=20.0) is not None  # end<start
    assert validate_pick(MomentPick(start=-1, end=3, reason="r"), duration=20.0) is not None # start<0
    assert validate_pick(MomentPick(start=15, end=25, reason="r"), duration=20.0) is not None# end>dur
    assert validate_pick(MomentPick(start=0, end=5, reason="r"), duration=20.0) is None      # ok

def test_owned_moment_id_includes_handle():
    # P3: two owners at the same timecode yield distinct moment ids; both differ from the bare-token id.
    src, token = "src_1", "14.00-18.00"
    bare = child_id("moment", src, token)
    id_a = _owned_moment_id(src, "a", token)
    id_b = _owned_moment_id(src, "b", token)
    assert id_a != id_b and id_a != bare and id_b != bare

def test_persona_blind_id_is_bare_token():
    # P3 firewall: personas==[] -> bare-token id (byte-identical to today's construction).
    src, token = "src_1", "14.00-18.00"
    assert _owned_moment_id(src, None, token) == child_id("moment", src, token)

def test_ingest_moments_creates_content_addressed_units(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=14.0, end=18.5, reason="punchline + scene cut at 16",
                                    transcript_excerpt="they slept on me", signal_score=0.6)])
    moms = led.moments_of("src_1")
    assert len(moms) == 1
    assert moms[0].content_token == "14.00-18.50"
    assert moms[0].reason.startswith("punchline")
    assert led.sources["src_1"].state is SourceState.picks_decided

def test_amplify_style_reingest_reconciles_not_noop(tmp_path):
    # The v1 bug: a NEW decision must actually replace, update, and cascade-delete.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=0.0, end=2.0, reason="A"),
                         MomentPick(start=14.0, end=18.0, reason="B")])
    # hang a clip+post off moment A so we can prove cascade-delete
    a = next(m for m in led.moments_of("src_1") if m.content_token == "0.00-2.00")
    led.add_clip(Clip(id="c_a", parent_id=a.id, path="/c"))
    # a REJECTED post (deletable) so A's lineage still cascade-deletes
    led.add_post(Post(id="p_a", parent_id="c_a", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.rejected, public_url="dryrun://p_a"))
    # now a fresh request + a NEW decision dropping A, keeping B (updated), adding C
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=14.0, end=18.0, reason="B-better"),
                         MomentPick(start=6.0, end=8.0, reason="C")])
    tokens = {m.content_token: m for m in led.moments_of("src_1")}
    assert set(tokens) == {"14.00-18.00", "6.00-8.00"}     # A gone, C added
    assert tokens["14.00-18.00"].reason == "B-better"       # B updated in place (not blocked)
    assert "c_a" not in led.clips and "p_a" not in led.posts # A's lineage cascade-deleted

def test_ingest_all_invalid_marks_source_error(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=50, end=60, reason="out of bounds")])
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is SourceState.error
    assert "end>" in (led.sources["src_1"].error_reason or "")

def test_ingest_moments_noop_without_matching_response(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = ingest_moments(led, cfg, "src_1")     # no response yet
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is SourceState.moments_requested

def test_ingest_partial_rejection_keeps_valid_drops_invalid(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=14.0, end=18.0, reason="valid keep"),
                         MomentPick(start=5.0, end=3.0, reason="end<start invalid"),
                         MomentPick(start=6.0, end=8.0, reason="valid keep 2")])
    tokens = {m.content_token for m in led.moments_of("src_1")}
    assert tokens == {"14.00-18.00", "6.00-8.00"}              # invalid dropped, two valid kept
    assert led.sources["src_1"].state is SourceState.picks_decided

def test_validate_pick_min_length_and_eof_tolerance():
    assert validate_pick(MomentPick(start=10.0, end=10.3, reason="r"), duration=20.0) is not None  # 0.30s too short
    assert validate_pick(MomentPick(start=10.0, end=10.5, reason="r"), duration=20.0) is None       # 0.50s ok
    assert validate_pick(MomentPick(start=10.0, end=20.5, reason="r"), duration=20.0) is None        # exactly dur+0.5 ok
    assert validate_pick(MomentPick(start=10.0, end=20.6, reason="r"), duration=20.0) is not None     # dur+0.6 invalid
    assert validate_pick(MomentPick(start=10.0, end=999.0, reason="r"), duration=0.0) is None         # unprobed -> no EOF ceiling

def test_validate_pick_rejects_nan_defense_in_depth():
    import math
    p = MomentPick.model_construct(start=math.nan, end=math.nan, reason="r")
    assert validate_pick(p, duration=120.0) is not None

def test_request_moments_attaches_source_frames(tmp_path, mocker):
    # PASS 1: request_moments samples a few SOURCE stills into the payload so the PICKER can judge which
    # windows are visually strong (the hook-grounding frames come in the separate hook pass).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    (cfg.sources / "src_1.mp4").parent.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_1.mp4").write_bytes(b"\x00")              # the source path must exist for extraction
    mocker.patch("fanops.moments.extract_keyframes", return_value=["/k/a.jpg", "/k/b.jpg"])
    led = request_moments(led, cfg, "src_1")
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["frames"] == ["/k/a.jpg", "/k/b.jpg"]

def test_request_moments_frames_empty_when_source_absent(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)   # source_path NOT written
    led = request_moments(led, cfg, "src_1")
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["frames"] == []

# --- PASS 2: the frame-seeing hook gate -------------------------------------------------------------
def test_request_moment_hooks_extracts_frames_over_the_fitted_window(tmp_path, mocker):
    # THE operator's #1 ask, mechanically proven: the hook gate's frames are extracted over the picked +
    # FITTED window (the cut the renderer makes), NOT 0..duration. A 14-18s pick on a talk profile fits to
    # 12s (band floor) -> the frames cover [start, start+12], not a whole-source survey.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    (cfg.sources / "src_1.mp4").parent.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_1.mp4").write_bytes(b"\x00")
    spy = mocker.patch("fanops.moments.extract_keyframes", return_value=["/k/w0.jpg"])
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.0, reason="bar lands")])
    led = request_moment_hooks(led, cfg, "src_1")
    # the extract call for the hook gate must use the FITTED window (start=14.0), not 0.0..duration
    call = spy.call_args
    assert call.args[1] == 14.0                       # window start == pick start (in band-floor reach)
    assert call.args[2] == 26.0                       # fitted end = 14 + 12s talk floor (NOT 18, NOT 60)
    payload = json.loads(request_path(cfg, "moment_hooks", "src_1.14.00-18.00").read_text())
    assert payload["frames"] == ["/k/w0.jpg"] and payload["moment_id"]

def test_request_moment_hooks_is_write_once(tmp_path):
    # A second request_moment_hooks pass must NOT re-stamp an already-open gate (that would invalidate an
    # in-flight answer). The request_id is stable across re-runs while the moment stays picked.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.0, reason="bar lands")])
    led = request_moment_hooks(led, cfg, "src_1")
    rid1 = latest_request_id(cfg, "moment_hooks", "src_1.14.00-18.00")
    led = request_moment_hooks(led, cfg, "src_1")     # second pass — must be a no-op for this gate
    assert latest_request_id(cfg, "moment_hooks", "src_1.14.00-18.00") == rid1

def test_decide_hooks_promotes_picked_to_decided_with_window_hook(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "wait for the beat switch"})
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided and m.hook == "wait for the beat switch"
    assert led.sources["src_1"].state is SourceState.moments_decided

def test_decide_hooks_null_hook_decides_clean(tmp_path):
    # The author legitimately returns hook=null (no honest hook -> better CLEAN than slop): the pick still
    # PROMOTES to decided (renderable, just clean), never wedged forever in `picked`.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": None})
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.decided and m.hook is None
    assert led.sources["src_1"].state is SourceState.moments_decided

def test_ingest_moment_hooks_pending_gate_keeps_moment_picked(tmp_path):
    # No response yet -> the moment STAYS picked (re-checked next pass, VISIBLE in the awaiting count) and
    # the source STAYS picks_decided — never a silent wedge, never a premature promotion.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = request_moment_hooks(led, cfg, "src_1")     # gate opened, NOT answered
    led = ingest_moment_hooks(led, cfg, "src_1")
    assert led.moments_of("src_1")[0].state is MomentState.picked
    assert led.sources["src_1"].state is SourceState.picks_decided

def test_decide_hooks_sanitizes_em_dash_in_hook(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="r")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "the switch — you feel it"})
    assert "—" not in (led.moments_of("src_1")[0].hook or "")

def test_decide_hooks_without_hook_shows_no_onscreen_text(tmp_path):
    # A pick whose gate isn't answered with a hook ends CLEAN — never the transcript first-clause (burning
    # the unreliable auto-transcript is the exact slop the operator rejected).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=14.0, end=18.5, reason="punchline",
                                    transcript_excerpt="This changed everything for me.")])
    led = _decide_hooks(led, cfg, "src_1")            # no hook supplied -> null
    assert led.moments_of("src_1")[0].hook is None    # clean clip — NOT the transcript first-clause

def test_decide_hooks_rejects_mechanical_dup_hook_to_clean_clip(tmp_path):
    # The deterministic MECHANICAL floor (is_weak_hook) applies through the HOOK pass now: a window hook
    # that EXACTLY duplicates another clip's hook is rejected to a CLEAN clip (never burned twice).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led.add_moment(Moment(id="m_other", parent_id="src_other", state=MomentState.decided,
                          start=0.0, end=5.0, reason="x", hook="wait for the drop"))
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "wait for the drop"})   # exact cross-clip dup
    assert led.moments_of("src_1")[0].hook is None    # exact dup -> clean clip, not burned twice

def test_decide_hooks_does_not_strip_perspective_at_ingest(tmp_path):
    # RF5 (viewer-POV at the source): the post-generation PERSPECTIVE strip is REMOVED. The generator now
    # owns perspective (its demos/echoes/voice/learned styles are all viewer-POV, MOL-20..22), so ingest no
    # longer nulls a third-person hook — it SHIPS (any stray one is caught in Studio Review, not stripped
    # here). The mechanical (is_weak_hook) + brand (brand_risk_flag) strips still fire — see the dup/off-brand
    # tests. A third-person hook is intentionally passed to prove ingest does not gate perspective.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1",
                        {"14.00-18.50": "he switches to Arabic when it gets personal"})
    m = led.moments_of("src_1")[0]
    assert m.hook == "he switches to Arabic when it gets personal"     # NOT stripped — generator owns perspective
    assert m.hook_removed is None                                      # nothing removed (no perspective gate)

def test_decide_hooks_keeps_viewer_pov_hook(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "the part you'll replay"})
    assert led.moments_of("src_1")[0].hook == "the part you'll replay"   # viewer-POV ships

def test_decide_hooks_does_not_strip_perspective_from_per_account_hooks(tmp_path):
    # P6: ingest no longer persists hooks_by_persona — only the shared `hook` lands on m.hook.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1",
                        {"14.00-18.50": ("the part you'll replay",
                                         {"a": "you won't expect the switch",
                                          "b": "he flips the whole beat"})})
    m = led.moments_of("src_1")[0]
    assert m.hook == "the part you'll replay"

def test_brand_screen_runs_on_clean_text(tmp_path):
    # MOL-166: brand_risk_flag remains a semantic gate on ALREADY-sanitized hook text.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "sorry but you'll replay this part"})
    m = led.moments_of("src_1")[0]
    assert m.hook is None
    assert m.hook_removed == "sorry but you'll replay this part"

def test_decide_hooks_rejects_off_brand_hook_to_clean_clip(tmp_path):
    # HIGH (audit): the BURNED on-screen hook must get the SAME brand-risk screen EN/AR captions get
    # (brand_risk_flag). A viewer-POV hook (passes the weak/narration floor) that trips the off-brand
    # bravado guardrail ("sorry") is stripped to a clean clip; the stripped text is PRESERVED for Review.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "sorry but you'll replay this part"})
    m = led.moments_of("src_1")[0]
    assert m.hook is None                                              # off-brand -> clean clip, not burned
    assert m.hook_removed == "sorry but you'll replay this part"       # preserved for Review

def test_decide_hooks_rejects_off_brand_per_account_hook_falls_back(tmp_path):
    # P6: off-brand keys in decision hooks_by_persona are ignored — only the shared hook is gated + persisted.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1",
                        {"14.00-18.50": ("the part you'll replay",
                                         {"a": "you won't expect the switch",
                                          "b": "please stream this, link in bio"})})
    m = led.moments_of("src_1")[0]
    assert m.hook == "the part you'll replay"

def test_decide_hooks_drops_and_logs_unknown_persona_handle(tmp_path):
    # P6: decision hooks_by_persona is not ingested; unknown-handle intersection/logging deferred to P7.
    from fanops.accounts import Accounts, Account
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    accts = Accounts(cfg); accts.accounts = [Account(handle="mohflow", platforms=["instagram"])]
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1",
                        {"14.00-18.50": ("the part you'll replay",
                                         {"mohflow": "you won't expect the switch",
                                          "@MohFlow": "you won't expect the switch"})},
                        accounts=accts)
    m = led.moments_of("src_1")[0]
    assert m.hook == "the part you'll replay"

def test_decide_hooks_brand_risk_honors_tuning_override(tmp_path):
    # The hook gate honors the SAME tuning.json offbrand override as captions: clearing both lists
    # disables it, so a would-be-flagged hook ships (operator owns the guardrail vocabulary).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    cfg.control.mkdir(parents=True, exist_ok=True)
    (cfg.control / "tuning.json").write_text('{"offbrand_en": [], "offbrand_ar": []}')
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.5, reason="punchline")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.50": "sorry but you'll replay this part"})
    assert led.moments_of("src_1")[0].hook == "sorry but you'll replay this part"   # override cleared -> ships

def test_decide_hooks_preserves_stripped_hook_for_operator_review(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led.add_source(Source(id="src_other", source_path="/o.mp4", duration=30.0, state=SourceState.moments_decided))
    led.add_moment(Moment(id="m_other", parent_id="src_other", content_token="0.00-5.00", start=0, end=5,
                          reason="r", state=MomentState.decided, hook="made it and lost everything"))
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=0.0, end=14.0, reason="r")])
    led = _decide_hooks(led, cfg, "src_1", {"0.00-14.00": "made it and lost everything"})
    m = led.moments_of("src_1")[0]
    assert m.hook is None                                    # stripped (duplicate) -> clean clip
    assert m.hook_removed == "made it and lost everything"   # PRESERVED for Review

def _seed_cross_source_openers(led):
    led.add_source(Source(id="src_other", source_path="/o.mp4", duration=120.0, state=SourceState.moments_decided))
    for i, h in enumerate(["wait for the beat drop", "wait for the last line", "wait for the hometown bar"]):
        led.add_moment(Moment(id=f"m_other_{i}", parent_id="src_other", content_token=f"{i}.00-5.00",
                              start=i, end=i + 5, reason="r", state=MomentState.decided, hook=h))

def test_cross_source_shared_opener_survives(tmp_path):
    # The per-source scope fix holds through the hook pass: a hook sharing a 3-word opener with hooks on
    # OTHER sources must NOT be blanked — opening-template clustering is a within-ONE-decision tell.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=120.0)
    _seed_cross_source_openers(led)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=0.0, end=14.0, reason="r")])
    led = _decide_hooks(led, cfg, "src_1", {"0.00-14.00": "wait for the final verse"})
    m = led.moments_of("src_1")[0]
    assert m.hook == "wait for the final verse"              # SURVIVES — feed-wide openers don't cluster it
    assert m.hook_removed is None

# --- M1b adversarial-review fixes: re-pick gate hygiene + atomic ingest + honest window frames ---------
def test_redecide_discards_stale_hook_gates(tmp_path):
    # CRITICAL (review): a re-decision (amplify) that re-picks the SAME window must NOT reuse the prior
    # pick's hook — it was authored against the OLD reason/window/frames. ingest_moments discards the
    # source's stale moment_hooks gates so request_moment_hooks re-authors fresh against the new decision.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.0, reason="OLD reason")])
    led = _decide_hooks(led, cfg, "src_1", {"14.00-18.00": "hook for the OLD context"})
    assert led.moments_of("src_1")[0].hook == "hook for the OLD context"
    # amplify-style re-decision: SAME window, NEW reason -> moment upserts in place, resets to picked
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.0, reason="NEW reason")])
    m = led.moments_of("src_1")[0]
    assert m.state is MomentState.picked and m.hook is None and m.reason == "NEW reason"
    # the stale hook gate is GONE -> a FRESH gate is opened, pending an answer (the stale one is not reused)
    led = request_moment_hooks(led, cfg, "src_1")
    assert "src_1.14.00-18.00" in pending(cfg, kind="moment_hooks")
    led = ingest_moment_hooks(led, cfg, "src_1")
    assert led.moments_of("src_1")[0].state is MomentState.picked and led.moments_of("src_1")[0].hook is None

def test_ingest_moment_hooks_is_atomic_per_source(tmp_path):
    # review: hooks ingest ATOMICALLY per source (every pick's gate answered) so the cross-clip/cluster
    # dedup is order-independent. With ONE pick still pending, NO pick of the source promotes.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=120.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10.0, end=24.0, reason="a"),
                         MomentPick(start=40.0, end=54.0, reason="b")])
    led = request_moment_hooks(led, cfg, "src_1")
    key = "src_1.10.00-24.00"                                   # answer ONLY the first pick's gate
    rid = latest_request_id(cfg, "moment_hooks", key)
    response_path(cfg, "moment_hooks", key).write_text(
        MomentHookDecision(request_id=rid, hook="the first hook").model_dump_json())
    led = ingest_moment_hooks(led, cfg, "src_1")
    assert all(m.state is MomentState.picked for m in led.moments_of("src_1"))   # atomic: neither promotes
    assert led.sources["src_1"].state is SourceState.picks_decided

def test_cross_pass_exact_dup_stripped_not_burned_twice(tmp_path):
    # review (under-dedup bug): two same-source picks with the SAME hook must not BOTH ship it. Atomic
    # ingest sees both -> the later (start-order) is stripped, exactly like the old single-pass loop.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=120.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10.0, end=24.0, reason="a"),
                         MomentPick(start=40.0, end=54.0, reason="b")])
    led = _decide_hooks(led, cfg, "src_1",
                        {"10.00-24.00": "wait for the drop", "40.00-54.00": "wait for the drop"})
    hooks = sorted((m.start, m.hook) for m in led.moments_of("src_1"))
    assert hooks[0][1] == "wait for the drop"        # first (start-order) keeps it
    assert hooks[1][1] is None                        # exact dup stripped (never burned twice)

def test_window_frames_empty_no_whole_source_fallback(tmp_path, mocker):
    # review: when the picked-WINDOW frame probe yields nothing, the author gets [] (honest text-only),
    # NOT whole-source frames — the hook prompt asserts the stills ARE this clip's window, so substituting
    # out-of-window footage would mislead the author. extract_keyframes is called exactly ONCE (no fallback).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    (cfg.sources / "src_1.mp4").parent.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_1.mp4").write_bytes(b"\x00")
    spy = mocker.patch("fanops.moments.extract_keyframes", return_value=[])   # window probe yields nothing
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [MomentPick(start=14.0, end=18.0, reason="r")])
    spy.reset_mock()                                   # measure ONLY the hook pass (pick pass also probes)
    led = request_moment_hooks(led, cfg, "src_1")
    assert spy.call_count == 1                          # ONE window probe, no whole-source fallback
    assert spy.call_args.args[1] == 14.0               # ...and it was the WINDOW (start=14), never 0.0..duration
    payload = json.loads(request_path(cfg, "moment_hooks", "src_1.14.00-18.00").read_text())
    assert payload["frames"] == []                     # honest text-only, not wrong footage

def test_within_source_template_cluster_still_strips_surplus(tmp_path):
    # The floor STILL fires when ONE decision goes templated: 4 picks all opening "wait for the X" -> the
    # 4th (>= max sharing the 3-word opener within THIS source) is stripped, preserved on hook_removed.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=120.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [
        MomentPick(start=0.0, end=14.0, reason="r"),
        MomentPick(start=20.0, end=34.0, reason="r"),
        MomentPick(start=40.0, end=54.0, reason="r"),
        MomentPick(start=60.0, end=74.0, reason="r")])
    led = _decide_hooks(led, cfg, "src_1", {
        "0.00-14.00": "wait for the beat drop", "20.00-34.00": "wait for the last line",
        "40.00-54.00": "wait for the hometown bar", "60.00-74.00": "wait for the final verse"})
    hooks = sorted((m.start, m.hook, m.hook_removed) for m in led.moments_of("src_1"))
    assert [h[1] for h in hooks] == ["wait for the beat drop", "wait for the last line",
                                     "wait for the hometown bar", None]      # 4th stripped within-source
    assert hooks[3][2] == "wait for the final verse"                         # preserved for Review


def test_validate_pick_rejects_blank_reason():
    # MOM-6: a pick whose rationale is blank/whitespace is invalid — a rationale-less pick rides the casting
    # fit signal + hook brief blind. A real reason passes; a timing-valid but reason-less pick is rejected.
    from fanops.moments import validate_pick
    from fanops.models import MomentPick
    assert validate_pick(MomentPick(start=0, end=7, reason="strong drop here"), duration=60) is None
    assert validate_pick(MomentPick(start=0, end=7, reason="   "), duration=60) == "blank reason"
    assert validate_pick(MomentPick(start=0, end=7, reason=""), duration=60) == "blank reason"


# ---- AGENT-2: the pick-payload transcript is bounded (sampled near peaks), small inputs byte-identical ----
def test_long_transcript_is_truncated_with_marker():
    from fanops.moments import _bounded_transcript
    segs = [{"start": float(i), "end": float(i) + 1, "text": "x" * 1000} for i in range(200)]
    peaks = [{"t": 50.0, "kind": "scene_cut", "score": 9.0}]
    kept, dropped = _bounded_transcript(segs, peaks)
    assert dropped > 0 and kept and len(kept) < len(segs)
    assert kept == sorted(kept, key=lambda s: s["start"])         # chronological order restored
    assert _bounded_transcript(segs[:2], peaks) == (segs[:2], 0)  # short -> unchanged (byte-identical)


# ---- MOL-159: persona-biased _bounded_transcript (filtered peaks + corpus bonus) ----
def test_bounded_transcript_biases_to_filtered_peaks(monkeypatch):
    from fanops import moments
    from fanops.moments import _bounded_transcript
    monkeypatch.setattr(moments, "_TRANSCRIPT_CHAR_BUDGET", 5000)
    segs = [{"start": float(i), "end": float(i) + 1, "text": "line " * 20} for i in range(200)]
    peaks_early = [{"t": 5.0, "kind": "energy", "score": 9.0}]
    peaks_late = [{"t": 195.0, "kind": "energy", "score": 9.0}]
    kept_early, _ = _bounded_transcript(segs, peaks_early)
    kept_late, _ = _bounded_transcript(segs, peaks_late)
    assert kept_early and kept_late
    assert {s["start"] for s in kept_early} != {s["start"] for s in kept_late}
    assert max(s["start"] for s in kept_early) < min(s["start"] for s in kept_late)


def test_bounded_transcript_corpus_bonus(monkeypatch):
    from fanops import moments
    from fanops.moments import _bounded_transcript
    monkeypatch.setattr(moments, "_TRANSCRIPT_CHAR_BUDGET", 50)
    near_plain = {"start": 48.0, "end": 52.0, "text": "x" * 40}           # mid=50, no corpus hit
    near_corpus = {"start": 49.0, "end": 51.0, "text": "freestyle " * 4}  # mid=50, corpus hit
    far = [{"start": float(i * 10), "end": float(i * 10) + 1, "text": "z" * 200} for i in range(30)]
    segs = sorted(far + [near_plain, near_corpus], key=lambda s: s["start"])
    peaks = [{"t": 50.0}]
    kept, _ = _bounded_transcript(segs, peaks, corpus=["freestyle"])
    assert near_corpus in kept
    assert near_plain not in kept


def test_bounded_transcript_corpus_not_a_filter(monkeypatch):
    from fanops import moments
    from fanops.moments import _bounded_transcript
    monkeypatch.setattr(moments, "_TRANSCRIPT_CHAR_BUDGET", 200)
    segs = [{"start": float(i), "end": float(i) + 1, "text": "off theme " * 10} for i in range(50)]
    peaks = [{"t": 25.0}]
    kept, dropped = _bounded_transcript(segs, peaks, corpus=["freestyle", "drill"])
    assert kept and dropped > 0
    assert all("freestyle" not in s.get("text", "").lower() for s in kept)


def test_bounded_transcript_no_corpus_byte_identical():
    from fanops.moments import _bounded_transcript
    segs = [{"start": float(i), "end": float(i) + 1, "text": "x" * 1000} for i in range(200)]
    peaks = [{"t": 50.0, "kind": "scene_cut", "score": 9.0}]
    baseline = _bounded_transcript(segs, peaks)
    assert _bounded_transcript(segs, peaks, corpus=None) == baseline
    assert _bounded_transcript(segs, peaks, corpus=[]) == baseline


# --- MOL-230: vision finalizer recovers a well-formed MomentDecision on the pick gate ---
def test_vision_finalizer_yields_valid_moment_decision(mocker):
    """Prose-only vision turn -> schema-only finalizer -> valid MomentDecision (not None/degraded)."""
    from fanops.responder import _default_claude_model
    pick = {"start": 10.0, "end": 28.0, "reason": "the bar lands as the beat drops"}
    decision = {"picks": [pick]}
    prose = "I reviewed the attached frames. Strong energy mid-source but returning prose."
    seq = iter([json.dumps({"structured_output": None, "result": prose, "num_turns": 2}),
                json.dumps({"structured_output": decision, "num_turns": 1})])
    def fake(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": next(seq), "stderr": ""})()
    run = mocker.patch("fanops.llm.subprocess.run", side_effect=fake)
    out = _default_claude_model("moments", {"source_id": "src_1", "duration": 60.0,
                                            "frames": ["/f/a.jpg", "/f/b.jpg"],
                                            "transcript": [{"start": 10, "end": 28, "text": "bar"}],
                                            "signal_peaks": [], "language": "en", "guidance": ""})
    dec = MomentDecision(**out)
    assert len(dec.picks) == 1 and dec.picks[0].start == 10.0 and dec.picks[0].end == 28.0
    assert run.call_count == 2
