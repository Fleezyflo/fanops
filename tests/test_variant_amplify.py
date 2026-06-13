# tests/test_variant_amplify.py
import ast
import json
import pathlib
from fanops.agentstep import request_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Source, Moment, Clip, SourceState
from fanops.variant_amplify import update_streaks, amplify_candidates, apply_variant_amplify


def _post(pid, acct, hook, lift, state=PostState.analyzed):
    return Post(id=pid, parent_id="c1", account=acct, account_id="1", platform=Platform.instagram,
                caption="x", state=state, variant_key=f"vk_{pid}", variant_hook=hook,
                metrics={"lift_score": lift})


def _led(cfg, posts):
    led = Ledger.load(cfg)
    for p in posts:
        led.add_post(p)
    return led


def _winset(n, hook, lift, start=1):
    # n analyzed posts of `hook` at `lift` + a runner-up far below so best_hooks fires.
    posts = [_post(str(start + i), "@a", hook, lift) for i in range(n)]
    posts += [_post(str(start + n + i), "@a", "LOSE", 1.0) for i in range(3)]
    return posts


def _seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))


# ---- Task 4: update_streaks — deterministic, idempotent streak tracker ----------------------------

def test_first_sighting_sets_streak_one(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_POSTS default 8
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN" and e["streak"] == 1


def test_same_winner_new_evidence_increments(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    led.add_post(_post("99", "@a", "WIN", 90.0))   # NEW analyzed evidence (new post id)
    update_streaks(led, cfg)               # streak 2
    assert led.variant_streaks["@a|instagram"]["streak"] == 2


def test_same_evidence_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    snap = dict(led.variant_streaks["@a|instagram"])
    update_streaks(led, cfg)               # SAME evidence -> no change
    update_streaks(led, cfg)               # and again
    assert led.variant_streaks["@a|instagram"] == snap


def test_winner_change_resets_to_one(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # WIN streak 1
    # Now make a DIFFERENT hook the clear leader: add 9 NEW posts of "WIN2" at a lift that beats the
    # WIN runner-up (mean 90) by >= variant_min_gap (10) — else best_hooks would see no comparative
    # winner and return []. 110 mean -> WIN2 leads WIN by 20 (clears the floor gate).
    for i in range(9):
        led.add_post(_post(f"2{i}", "@a", "WIN2", 110.0))
    update_streaks(led, cfg)
    e = led.variant_streaks["@a|instagram"]
    assert e["hook"] == "WIN2" and e["streak"] == 1     # reset, not continued


def test_winner_disappears_resets_to_zero(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)               # streak 1
    # Drop below the floor: make the gap tiny so best_hooks now returns [] (raise the losers).
    for p in led.posts.values():
        if p.variant_hook == "LOSE":
            p.metrics["lift_score"] = 89.0
    update_streaks(led, cfg)
    assert led.variant_streaks["@a|instagram"]["streak"] == 0


def test_update_streaks_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led, cfg)
    a = dict(led.variant_streaks["@a|instagram"])
    led2 = _led(cfg, _winset(8, "WIN", 90.0))
    update_streaks(led2, cfg)
    b = dict(led2.variant_streaks["@a|instagram"])
    assert a == b                          # same ledger state -> identical streak entry


# ---- Task 5: amplify_candidates — the pure, fully-gated decision --------------------------------

def test_below_floor_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, [_post("1", "@a", "WIN", 90.0), _post("2", "@a", "WIN", 90.0)])  # < floor min_posts
    _seed_lineage(led)
    assert amplify_candidates(led, cfg) == []


def test_floor_met_but_below_min_posts(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_POSTS=8; best_hooks floor min_posts=3
    led = _led(cfg, _winset(5, "WIN", 90.0))   # 5 >= floor(3) but < amplify(8)
    _seed_lineage(led)
    # streak alone can't rescue it — posts < 8 must veto regardless of streak
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 9}
    assert amplify_candidates(led, cfg) == []


def test_gap_too_small_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)            # AMPLIFY_MIN_GAP=25
    posts = [_post(str(i), "@a", "WIN", 60.0) for i in range(8)]
    posts += [_post(str(20 + i), "@a", "LOSE", 50.0) for i in range(8)]   # gap 10 < 25
    led = _led(cfg, posts)
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 9}
    assert amplify_candidates(led, cfg) == []


def test_streak_too_small_no_candidate(tmp_path):
    # ALL of best_hooks-floor + min_posts + min_gap met, but streak < min_streak -> [].
    # THIS is the single-window guard — the core new safety property.
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))   # 8 posts, gap 89 -> floor+posts+gap all met
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 2}  # < 3
    assert amplify_candidates(led, cfg) == []


def test_all_gates_met_returns_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    cands = amplify_candidates(led, cfg)
    assert len(cands) == 1
    c = cands[0]
    assert c["source_id"] == "s1" and c["winning_hook"] == "WIN"
    assert c["post_id"] in {p.id for p in led.posts.values() if p.variant_hook == "WIN"}


def test_source_at_amplify_budget_skipped(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    led.sources["s1"].meta["amplify_count"] = 3        # E1 cap reached (max_amplify_per_source=3)
    assert amplify_candidates(led, cfg) == []


def test_empty_ledger_no_candidate(tmp_path):
    cfg = Config(root=tmp_path)
    assert amplify_candidates(Ledger.load(cfg), cfg) == []


def test_amplify_candidates_deterministic(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    assert amplify_candidates(led, cfg) == amplify_candidates(led, cfg)


# ---- Task 6: apply_variant_amplify (fail-SAFE actuator) + retire-isolation AST + mutation proof --

def _frozen(led):
    """A comparable snapshot of the ledger's CONTENT state — sources/moments/clips/posts — for
    'nothing was amplified/retired/deleted' assertions. Deliberately EXCLUDES variant_streaks: the
    streak map is SUPPOSED to advance/reset every pass (that's the feature), so including it would
    make a legitimate streak update look like a content mutation. The real safety invariant v3 must
    never violate is that no source is amplified (state flip / request file / amplify_count) and no
    post or clip is retired/deleted — i.e. the CONTENT state is untouched. That is what this freezes."""
    return json.dumps({
        "sources": {k: v.model_dump() for k, v in led.sources.items()},
        "moments": {k: v.model_dump() for k, v in led.moments.items()},
        "clips": {k: v.model_dump() for k, v in led.clips.items()},
        "posts": {k: v.model_dump() for k, v in led.posts.items()},
    }, sort_keys=True, default=str)


def test_apply_amplifies_when_fully_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    _validate(cfg)                                   # Phase 2: live-validation precondition met
    apply_variant_amplify(led, cfg)
    # the source was amplified: state flipped + the moment-request carries the winning hook.
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WIN" in payload["guidance"]
    # G2: the winning analyzed post survives, state unchanged.
    win_posts = [p for p in led.posts.values() if p.variant_hook == "WIN"]
    assert win_posts and all(p.state is PostState.analyzed for p in win_posts)


def test_apply_noop_when_gate_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 1}  # < 3
    _validate(cfg)                                   # Phase 2: isolate the STREAK gate, not validation
    before = _frozen(led)
    apply_variant_amplify(led, cfg)
    assert _frozen(led) == before          # nothing changed — no amplify, no state flip
    assert not request_path(cfg, "moments", "s1").exists()


def test_apply_inert_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_AMPLIFY", raising=False)
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 9}
    before = _frozen(led)
    apply_variant_amplify(led, cfg)        # flag OFF -> kill switch, fully inert
    assert _frozen(led) == before
    assert not request_path(cfg, "moments", "s1").exists()


def test_apply_failsafe_on_internal_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    _validate(cfg)                                   # Phase 2: reach the try-body, not the validation gate
    # Make the candidate computation raise -> the whole pass must swallow it, no partial mutation.
    monkeypatch.setattr("fanops.variant_amplify.amplify_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = _frozen(led)
    apply_variant_amplify(led, cfg)        # must NOT raise
    assert _frozen(led) == before


def _validate(cfg):
    # Phase 2: mark the live-validation precondition (a real metrics row reconciled by cutover) so
    # the amplify actuator is allowed to act. Tests that exercise the amplify LOGIC must establish it.
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})


def test_apply_amplify_inert_until_learning_validated(tmp_path, monkeypatch):
    """OFF-until-proven (Phase 2): the kill switch is ON and every gate is met, but with NO confirmed
    live metrics row, amplify must stay inert — re-mining a source on a lift_score whose field shape
    has never been confirmed against live Blotato is the over-build trap — and it must LOG
    skipped_unvalidated (not silently), so the operator knows to run `fanops cutover`."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    before = _frozen(led)
    apply_variant_amplify(led, cfg)                      # no cutover.json -> inert despite full gate
    assert _frozen(led) == before
    assert not request_path(cfg, "moments", "s1").exists()
    assert "skipped_unvalidated" in cfg.log_path.read_text()


def test_apply_amplifies_once_learning_validated(tmp_path, monkeypatch):
    """Symmetric proof the gate OPENS (not a permanent block): the SAME fully-gated candidate DOES
    amplify once a real metrics row is confirmed."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    _validate(cfg)
    apply_variant_amplify(led, cfg)
    assert led.sources["s1"].state is SourceState.moments_requested   # amplified once validated


def test_apply_failsafe_logs_the_error_detail(tmp_path, monkeypatch):
    """FAIL-SAFE must not be FAIL-SILENT: when the swallowed pass hits an internal error, the log
    line must carry WHY (err=...), not a bare 'error' outcome. Without the detail an autonomous run
    that silently stops amplifying is indistinguishable from one with nothing to amplify — exactly
    the silent-mass-failure the run logger (FIX F51) exists to surface."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(8, "WIN", 90.0))
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    _validate(cfg)                                   # Phase 2: reach the try-body, not the validation gate
    monkeypatch.setattr("fanops.variant_amplify.amplify_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("AMPLIFY-BOOM-SENTINEL")))
    apply_variant_amplify(led, cfg)        # swallowed (must NOT raise) — but must record the reason
    log = cfg.log_path.read_text()
    assert "AMPLIFY-BOOM-SENTINEL" in log and "err=" in log


# --- The retire-isolation invariant (v3's C1 safety, mechanized — mirrors test_variant_learning's
#     AST approach but asserts the REVERSE direction: variant_amplify must be BLIND to the
#     retire/delete surface). HARDENED after adversarial review (2026-06-04): the original walked a
#     FIXED list of function names and only matched literal Attribute.attr / Name.id, so it was blind
#     to (a) a forbidden call placed in a NEW helper not in the list, (b) getattr(led, "retire"+...)
#     string dispatch, and (c) an aliased import (`from ledger import retire_clip as rc`). The
#     hardened version scans EVERY function in the module (module-level + nested), every string
#     LITERAL (catching getattr dispatch), and every import alias (asname). All three evasions are
#     mutation-proven to trip it (see the test docstrings). -------------------------------------
_FORBIDDEN_IN_VARIANT_AMPLIFY = ("retire", "_delete_moment_cascade", "retire_clip",
                                 "set_moment_state", "set_clip_state")


def _all_referenced_names(src_path):
    """Every identifier, attribute, import alias, AND string literal referenced ANYWHERE in the
    module — across all functions (module-level + nested), not a fixed list. String literals are
    included so getattr(obj, "retire_clip") string dispatch can't smuggle a forbidden call past the
    name-based check. Import aliases (asname) are included so `import retire_clip as rc` is caught."""
    tree = ast.parse(src_path.read_text())
    found = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            found.add(sub.value)                       # catches getattr(led, "retire_clip")
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for n in sub.names:
                found.add(n.name)                      # the real imported symbol
                if n.asname:
                    found.add(n.asname)                # ... and any alias it was bound to
    return found


def test_variant_amplify_never_touches_retire_or_cascade():
    """G1 (STRUCTURAL): variant_amplify is amplify-only. NOWHERE in the module may it reference
    retire / _delete_moment_cascade / retire_clip / set_moment_state / set_clip_state — as a call, a
    string for getattr dispatch, or an import alias — so a wrong 'this won' signal can never reach a
    delete/retire. A future edit wiring any of those in (even via a helper, getattr, or alias) goes
    RED and names the offender. MUTATION-PROVEN against all three evasion shapes."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    names = _all_referenced_names(root / "variant_amplify.py")
    leaked = sorted(names & set(_FORBIDDEN_IN_VARIANT_AMPLIFY))
    assert not leaked, f"variant_amplify must never reference retire/cascade; found: {leaked}"


# --- The MUTATION PROOF: the streak gate must be load-bearing. With the streak requirement
#     removed, a SINGLE-window signal would amplify — this test asserts that today it does NOT,
#     so when an implementer weakens the gate the suite goes red here. --------------------------
def test_single_window_signal_does_not_amplify(tmp_path, monkeypatch):
    """ADVERSARIAL: a strong but SINGLE-window signal (streak 1) must NEVER amplify. This is the
    mutation sentinel — if amplify_candidates ever stops requiring min_streak, this goes RED."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    led = _led(cfg, _winset(20, "WIN", 99.0))      # overwhelming evidence in ONE window
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 1}
    _validate(cfg)                                   # Phase 2: isolate the STREAK gate, not validation
    before = _frozen(led)
    apply_variant_amplify(led, cfg)
    assert amplify_candidates(led, cfg) == []       # gate holds despite huge single-window evidence
    assert _frozen(led) == before                   # and nothing was amplified/retired/deleted


def test_amplify_budget_constant_is_shared_with_adjust():
    """B1/E1 (review): variant_amplify's E1 budget pre-check and adjust.amplify's default must use ONE
    constant so they can never drift. Assert amplify_candidates rejects at exactly the amplify()
    default boundary (MAX_AMPLIFY_PER_SOURCE), and that the constant is the single shared source."""
    import inspect
    from fanops.adjust import MAX_AMPLIFY_PER_SOURCE, amplify
    import fanops.variant_amplify as va
    # the module imports the SAME constant object (not a re-declared literal)
    assert va.MAX_AMPLIFY_PER_SOURCE is MAX_AMPLIFY_PER_SOURCE
    # and amplify()'s default equals it (so the pre-check boundary matches what amplify enforces)
    assert inspect.signature(amplify).parameters["max_amplify_per_source"].default == MAX_AMPLIFY_PER_SOURCE


# ---- variation v3 (Task 5): the load-bearing UCB-vs-amplify safety invariant --------------------
def test_ucb_flag_does_not_change_amplify_candidates(tmp_path, monkeypatch):
    """Turning FANOPS_VARIANT_UCB on must NOT change which candidates amplify authorizes. Amplify's
    floor is best_hooks (conservative/comparative/noise-guarded), NEVER ucb_rank (exploratory). A
    bandit pick can nudge a caption; it can NEVER become an amplify (C1) authorization. Seed a real
    amplify candidate (WIN clears the full gate) PLUS a sparse challenger hook that ucb_rank would
    prefer — then assert amplify_candidates is identical with UCB off vs on."""
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    posts = _winset(8, "WIN", 90.0)                       # 8 WIN@90 + 3 LOSE@1 -> clears amplify gate
    posts.append(_post("90", "@a", "SPARSE", 5.0))        # 1 under-sampled challenger ucb would explore
    led = _led(cfg, posts)
    _seed_lineage(led)
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "x", "streak": 3}
    monkeypatch.delenv("FANOPS_VARIANT_UCB", raising=False)
    off = amplify_candidates(led, cfg)
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    on = amplify_candidates(led, cfg)
    assert off == on, "FANOPS_VARIANT_UCB must not affect amplify authorization (floor stays best_hooks)"
    assert len(off) == 1 and off[0]["winning_hook"] == "WIN"   # a REAL candidate (not two empties), still WIN
