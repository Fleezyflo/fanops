# tests/test_source_tag_lock.py
"""play_rank_key + lock_from_pile — lock is plays, not folder size. No network, no Config, no ledger."""
from fanops.hashtags import lock_from_pile, play_rank_key, size_rank_key

# Same mid band (10k–2M) so size_rank_key is media_count DESC, not a band flip.
BIG_FOLDER = {"media_count": 1_500_000, "play_count": 10, "current_top_reel_play_max_7d": 1}
HIGH_PLAY = {"media_count": 50_000, "play_count": 9_000, "current_top_reel_play_max_7d": 100}


def test_high_media_low_play_loses_to_high_play():
    names = ["#bigfolder", "#highplay"]
    measurements = {"#bigfolder": BIG_FOLDER, "#highplay": HIGH_PLAY}
    assert lock_from_pile(names, measurements) == ["#highplay", "#bigfolder"]


def test_unmeasured_names_excluded():
    names = ["#missing", "#noplayfield", "#zeroplay", "#kept"]
    measurements = {
        "#noplayfield": {"media_count": 1000},
        "#zeroplay": {"play_count": 0, "media_count": 999},
        "#kept": {"play_count": 5},
    }
    assert lock_from_pile(names, measurements) == ["#kept"]


def test_like_count_only_is_excluded():
    # play_count is the choose key — `_metric` would admit like_count and must not.
    names = ["#likes", "#plays"]
    measurements = {
        "#likes": {"like_count": 99_000, "media_count": 1_000_000},
        "#plays": {"play_count": 1},
    }
    assert lock_from_pile(names, measurements) == ["#plays"]


def test_equal_play_higher_reel_max_wins():
    names = ["#a", "#b"]
    measurements = {
        "#a": {"play_count": 100, "current_top_reel_play_max_7d": 10},
        "#b": {"play_count": 100, "current_top_reel_play_max_7d": 50},
    }
    assert lock_from_pile(names, measurements) == ["#b", "#a"]


def test_cap_twelve_highest_play_first():
    names = [f"#t{i}" for i in range(13)]
    measurements = {f"#t{i}": {"play_count": i + 1} for i in range(13)}
    lock = lock_from_pile(names, measurements)
    assert len(lock) == 12
    assert lock[0] == "#t12"            # play_count 13
    assert "#t0" not in lock            # play_count 1, the leftover


def test_incomplete_and_nondict_recs_do_not_raise():
    names = ["#ok", "#none", "#str", "#list", "#partial"]
    measurements = {
        "#ok": {"play_count": 8},
        "#none": None,
        "#str": "garbage",
        "#list": [1, 2],
        "#partial": {"media_count": 10},
    }
    assert lock_from_pile(names, measurements) == ["#ok"]
    assert lock_from_pile([], None) == []
    assert lock_from_pile(None, "garbage") == []
    assert play_rank_key("#x", None) == (0.0, 0.0, "#x")
    assert play_rank_key("#x", "nope") == (0.0, 0.0, "#x")
    assert play_rank_key("#x", []) == (0.0, 0.0, "#x")


def test_size_rank_key_still_orders_by_media_count():
    # loser-on-plays still wins on size — the two keys disagree and size is unchanged.
    assert size_rank_key("#bigfolder", BIG_FOLDER) < size_rank_key("#highplay", HIGH_PLAY)
    assert play_rank_key("#highplay", HIGH_PLAY) < play_rank_key("#bigfolder", BIG_FOLDER)
