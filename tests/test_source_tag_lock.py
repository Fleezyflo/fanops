# tests/test_source_tag_lock.py
"""lock_from_pile is positive play_count, play_rank_key order, cap 12. ship_from_lock is picks ∩ lock."""
from fanops.hashtags import lock_from_pile, play_rank_key, ship_from_lock, size_rank_key

# Same mid band (10k–2M) so size_rank_key is media_count DESC, not a band flip.
BIG_FOLDER = {"media_count": 1_500_000, "play_count": 10, "current_top_reel_play_max_7d": 1,
              "graph_metric": 5}
HIGH_PLAY = {"media_count": 50_000, "play_count": 9_000, "current_top_reel_play_max_7d": 100,
             "graph_metric": 5}


def test_high_media_low_play_loses_to_high_play():
    names = ["#bigfolder", "#highplay"]
    measurements = {"#bigfolder": BIG_FOLDER, "#highplay": HIGH_PLAY}
    assert lock_from_pile(names, measurements) == ["#highplay", "#bigfolder"]


def test_unmeasured_names_excluded():
    names = ["#missing", "#noplayfield", "#zeroplay", "#kept"]
    measurements = {
        "#noplayfield": {"media_count": 1000, "graph_metric": 9},
        "#zeroplay": {"play_count": 0, "media_count": 999, "graph_metric": 9},
        "#kept": {"play_count": 5, "graph_metric": 2},
    }
    assert lock_from_pile(names, measurements) == ["#kept"]


def test_like_count_only_is_excluded():
    # play_count is the admit key — like_count alone must not enter the lock.
    names = ["#likes", "#plays", "#graphonly", "#both"]
    measurements = {
        "#likes": {"like_count": 99_000, "media_count": 1_000_000},
        "#plays": {"play_count": 1},
        "#graphonly": {"graph_metric": 80},
        "#both": {"like_count": 10, "graph_metric": 3},
    }
    assert lock_from_pile(names, measurements) == ["#plays"]


def test_like_count_plus_graph_still_excluded():
    names = ["#likes", "#plays"]
    measurements = {
        "#likes": {"like_count": 99_000, "graph_metric": 4},
        "#plays": {"play_count": 1, "graph_metric": 4},
    }
    assert lock_from_pile(names, measurements) == ["#plays"]


def test_equal_play_higher_reel_max_wins():
    names = ["#a", "#b"]
    measurements = {
        "#a": {"play_count": 100, "current_top_reel_play_max_7d": 10, "graph_metric": 1},
        "#b": {"play_count": 100, "current_top_reel_play_max_7d": 50, "graph_metric": 1},
    }
    assert lock_from_pile(names, measurements) == ["#b", "#a"]


def test_cap_twelve_highest_play_first():
    names = [f"#t{i}" for i in range(13)]
    measurements = {f"#t{i}": {"play_count": i + 1, "graph_metric": 1} for i in range(13)}
    lock = lock_from_pile(names, measurements)
    assert len(lock) == 12
    assert lock[0] == "#t12"
    assert "#t0" not in lock


def test_incomplete_and_nondict_recs_do_not_raise():
    names = ["#ok", "#none", "#str", "#list", "#partial"]
    measurements = {
        "#ok": {"play_count": 8, "graph_metric": 1},
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


def test_ship_from_lock_invented_and_off_lock_die():
    lock = ["#keep", "#also"]
    assert ship_from_lock(["#invented", "#keep", "#offlock"], lock) == ["#keep"]


def test_ship_from_lock_order_is_picks_not_lock():
    lock = ["#a", "#b", "#c", "#d"]
    assert ship_from_lock(["#c", "#a", "#d"], lock) == ["#c", "#a", "#d"]


def test_ship_from_lock_cap_four():
    lock = [f"#t{i}" for i in range(10)]
    picks = [f"#t{i}" for i in range(10)]
    assert ship_from_lock(picks, lock) == picks[:4]


def test_ship_from_lock_empty_lock_is_empty():
    assert ship_from_lock(["#keep", "#also"], []) == []
    assert ship_from_lock(["#keep"], None) == []


def test_ship_from_lock_no_arabic_floor():
    lock = ["#keep"]
    ar = ["#arabicmusic", "#arabtiktok", "#arabicmusiclovers"]
    assert ship_from_lock(["#keep"] + ar, lock) == ["#keep"]
    assert "#arabicmusic" not in ship_from_lock(ar + ["#keep"], lock)
