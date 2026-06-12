from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.tagging import should_tag, decide_tag, ARTIST_HANDLE, _parse

def test_should_tag_minority_and_deterministic():
    n = sum(should_tag(f"clip{i}", "@a", rate=0.25) for i in range(100))
    assert 10 <= n <= 45
    assert should_tag("c", "@a", rate=0.25) == should_tag("c", "@a", rate=0.25)

def test_decide_tag_respects_no_sync_window(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    t0 = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    ok1 = decide_tag(led, account="@a", when=t0, force=True, min_gap_minutes=120)
    assert ok1 is True and len(led.tag_log) == 1     # the tag was recorded (key scheme is internal)
    ok2 = decide_tag(led, account="@b", when=t0 + timedelta(minutes=30),
                     force=True, min_gap_minutes=120)
    assert ok2 is False         # another account tagged within the window

def test_decide_tag_multi_post_serializes_across_accounts(tmp_path):
    # FIX F62: stateful invariant across MANY posts, not a single call.
    led = Ledger.load(Config(root=tmp_path))
    base = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    accepted = []
    for i in range(6):
        when = base + timedelta(minutes=i * 40)     # 0,40,80,120,160,200
        if decide_tag(led, account=f"@acct{i}", when=when, force=True, min_gap_minutes=120):
            accepted.append(i)
    # with a 120-min gap and 40-min spacing, only ~every 3rd post may tag
    for a, b in zip(accepted, accepted[1:]):
        assert (b - a) * 40 >= 120

def test_same_account_retag_does_not_erase_window_for_others(tmp_path):
    # AUDIT H3: tag_log was keyed per ACCOUNT (one timestamp each), so when the SAME account
    # tagged again its earlier tag time was OVERWRITTEN — opening a hole where another account
    # could tag within the de-cluster window of the now-erased earlier tag.
    # Scenario (min_gap=120): @a tags 10:00; @a tags again 14:30 (outside its own window, allowed)
    # which would overwrite 10:00; then @b at 11:30 is only 90min after @a's REAL 10:00 tag and
    # MUST be blocked. With per-account overwrite it was wrongly allowed.
    led = Ledger.load(Config(root=tmp_path))
    def t(h, m): return datetime(2026, 6, 2, h, m, tzinfo=timezone.utc)
    assert decide_tag(led, account="@a", clip_id="c1", when=t(10, 0), force=True, min_gap_minutes=120) is True
    assert decide_tag(led, account="@a", clip_id="c2", when=t(14, 30), force=True, min_gap_minutes=120) is True
    # @b at 11:30 is within 120min of @a's 10:00 tag — must be blocked, not allowed by the overwrite.
    assert decide_tag(led, account="@b", clip_id="c3", when=t(11, 30), force=True, min_gap_minutes=120) is False


def test_tag_log_does_not_prune_by_when_out_of_order_safe(tmp_path):
    # decide_tag must NOT prune by `when`: crosspost evaluates surfaces out of chronological order,
    # so a later call with an EARLIER `when` still needs older tag times. Pruning relative to `when`
    # dropped a time that a subsequent earlier-`when` decision needed and produced a false allow.
    # Guard against re-introducing that prune: an out-of-window tag is retained, not discarded.
    led = Ledger.load(Config(root=tmp_path))
    def t(h, m): return datetime(2026, 6, 2, h, m, tzinfo=timezone.utc)
    decide_tag(led, account="@a", clip_id="c1", when=t(10, 0), force=True, min_gap_minutes=120)
    decide_tag(led, account="@b", clip_id="c2", when=t(20, 0), force=True, min_gap_minutes=120)
    assert len(led.tag_log) == 2   # both retained — no when-relative pruning that could open a hole


def test_artist_handle_value():
    assert ARTIST_HANDLE == "@mohflow"

def test_parse_handles_z_suffix_round_trip():
    from datetime import timezone
    t = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    s = t.isoformat().replace("+00:00", "Z")
    assert _parse(s) == t                       # round-trips and stays tz-aware
    # also tolerate a literal +00:00 form
    assert _parse("2026-06-02T18:00:00+00:00") == t

def test_decide_tag_probabilistic_path_varies_by_clip(tmp_path):
    # force=False exercises the real gate. With clip_id threaded through, the SAME account
    # gets DIFFERENT tag decisions for different clips (per-clip variation, not per-account constant).
    from datetime import timezone
    led = Ledger.load(Config(root=tmp_path))
    base = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    # Find a clip that tags and one that doesn't for the same account, proving variation exists.
    decisions = {cid: should_tag(cid, "@a", rate=0.25) for cid in (f"clip{i}" for i in range(50))}
    assert any(decisions.values()) and not all(decisions.values())   # both True and False occur
    # And decide_tag with force=False honors should_tag: pick a known-True clip, far-future time (empty log)
    true_clip = next(cid for cid, v in decisions.items() if v)
    false_clip = next(cid for cid, v in decisions.items() if not v)
    assert decide_tag(led, account="@a", clip_id=true_clip, when=base, force=False) is True
    led.tag_log.clear()
    assert decide_tag(led, account="@a", clip_id=false_clip, when=base, force=False) is False
