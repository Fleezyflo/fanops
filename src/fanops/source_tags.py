# src/fanops/source_tags.py
"""Source hashtag lock producer.

Safari scrape completes the per-source lock. Graph may cache/confirm; Graph
never vetoes membership, never reorders the lock, and never withholds
researched_at after scrape finished. Empty lock = scrape finished with zero
admits. Caption waits on researched_at.

`shortlist_source_tags` keeps a subset of a closed catalog (off-catalog dies).
Empty catalog: names the pile from the video. Search verifies the exact name
(no siblings on the pile). Lock is keep ∩
positive play_count admits in keep order, cap 12. Optional
`hydrate_locks_from_known` may write `hydrated_at` + lock from already-used
tags (zero network) but must never write `researched_at` or open the caption
gate. Sidecar is cfg.control / source_tag_locks.json — not a Config field, not
hashtags.json.

Graph node id + graph_metric cache by tag name lives in a dedicated sidecar
(graph_hashtag_cache.json). Never mix with scrape `graph_id` in hashtags.json.
"""
from fanops.source_tags_scrape import ensure_source_lock
from fanops.source_tags_shortlist import (hydrate_locks_from_known, known_lock,
                                          shortlist_source_tags, used_tags_for_source)
from fanops.source_tags_sidecar import (GRAPH_TAG_CACHE_NAME, SOURCE_TAG_LOCKS_NAME,
                                        _note_graph_id, graph_search_quota_status,
                                        graph_tag_cache_path, load_graph_tag_cache,
                                        load_source_tag_locks, source_tag_locks_path)
from fanops.source_tags_walk import _iter_lock_clients, lock_ready_sources

__all__ = [
    "GRAPH_TAG_CACHE_NAME",
    "SOURCE_TAG_LOCKS_NAME",
    "_iter_lock_clients",
    "_note_graph_id",
    "ensure_source_lock",
    "graph_search_quota_status",
    "graph_tag_cache_path",
    "hydrate_locks_from_known",
    "known_lock",
    "load_graph_tag_cache",
    "load_source_tag_locks",
    "lock_ready_sources",
    "shortlist_source_tags",
    "source_tag_locks_path",
    "used_tags_for_source",
]
