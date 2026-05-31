from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.tagging import should_tag, decide_tag, ARTIST_HANDLE

def test_should_tag_minority_and_deterministic():
    n = sum(should_tag(f"clip{i}", "@a", rate=0.25) for i in range(100))
    assert 10 <= n <= 45
    assert should_tag("c", "@a", rate=0.25) == should_tag("c", "@a", rate=0.25)

def test_decide_tag_respects_no_sync_window(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    t0 = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    ok1 = decide_tag(led, account="@a", when=t0, force=True, min_gap_minutes=120)
    assert ok1 is True and "@a" in led.tag_log
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
