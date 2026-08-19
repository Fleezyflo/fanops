# tests/test_source_tag_lock.py
"""lock_from_pile is scrape-admit; Graph ranks first among admits. ship_from_lock is picks ∩ lock."""
from fanops.hashtags import lock_from_pile, play_rank_key, ship_from_lock, size_rank_key, _ARABIC

# Same mid band (10k–2M) so size_rank_key is media_count DESC, not a band flip.
BIG_FOLDER = {"media_count": 1_500_000, "play_count": 10, "current_top_reel_play_max_7d": 1,
              "graph_metric": 5}
HIGH_PLAY = {"media_count": 50_000, "play_count": 9_000, "current_top_reel_play_max_7d": 100,
             "graph_metric": 5}


def test_dual_qualify_keeps_llm_order_not_play_rank():
    names = ["#bigfolder", "#highplay"]
    measurements = {"#bigfolder": BIG_FOLDER, "#highplay": HIGH_PLAY}
    assert lock_from_pile(names, measurements) == ["#bigfolder", "#highplay"]


def test_unmeasured_names_excluded():
    names = ["#missing", "#noplayfield", "#zeroplay", "#kept"]
    measurements = {
        "#noplayfield": {"media_count": 1000, "graph_metric": 9},
        "#zeroplay": {"play_count": 0, "media_count": 999, "graph_metric": 9},
        "#kept": {"play_count": 5, "graph_metric": 2},
    }
    assert lock_from_pile(names, measurements) == ["#kept"]


def test_scrape_only_and_graph_only_are_excluded():
    # scrape-only IN; graph-only OUT; unmeasured OUT. Graph-present ranks first (LLM order in-tier).
    names = ["#likes", "#plays", "#graphonly", "#both"]
    measurements = {
        "#likes": {"like_count": 99_000, "media_count": 1_000_000},
        "#plays": {"play_count": 1},
        "#graphonly": {"graph_metric": 80},
        "#both": {"like_count": 10, "graph_metric": 3},
    }
    assert lock_from_pile(names, measurements) == ["#both", "#likes", "#plays"]


def test_graph_present_ranks_first_among_scrape_admitted():
    names = ["#scrape_a", "#both", "#scrape_b", "#graphonly"]
    measurements = {
        "#scrape_a": {"play_count": 99},
        "#both": {"like_count": 10, "graph_metric": 80},
        "#scrape_b": {"like_count": 5},
        "#graphonly": {"graph_metric": 999},
    }
    assert lock_from_pile(names, measurements) == ["#both", "#scrape_a", "#scrape_b"]


def test_like_count_plus_graph_qualifies():
    names = ["#likes", "#plays"]
    measurements = {
        "#likes": {"like_count": 99_000, "graph_metric": 4},
        "#plays": {"play_count": 1, "graph_metric": 4},
    }
    assert lock_from_pile(names, measurements) == ["#likes", "#plays"]


def test_equal_meters_keep_input_order():
    names = ["#a", "#b"]
    measurements = {
        "#a": {"play_count": 100, "current_top_reel_play_max_7d": 10, "graph_metric": 1},
        "#b": {"play_count": 100, "current_top_reel_play_max_7d": 50, "graph_metric": 1},
    }
    assert lock_from_pile(names, measurements) == ["#a", "#b"]


def test_cap_fifteen_llm_order():
    names = [f"#t{i}" for i in range(20)]
    measurements = {f"#t{i}": {"play_count": i + 1, "graph_metric": 1} for i in range(20)}
    lock = lock_from_pile(names, measurements)
    assert len(lock) == 15
    assert lock == names[:15]
    assert "#t19" not in lock
    assert lock[0] == "#t0"


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
    picks = ["#keep"] + list(_ARABIC)
    assert ship_from_lock(picks, lock) == ["#keep"]
    assert "#arabicmusic" not in ship_from_lock(list(_ARABIC) + ["#keep"], lock)
