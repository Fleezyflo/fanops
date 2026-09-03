"""Shared comparative reach ranking for P4 bias actuators (p4_dim_bias, timing_bias). Pure read — no I/O."""
from __future__ import annotations


def format_top_bias_value(winning_value: str, *, with_framing_suffix: bool = False) -> str:
    """Render a top_bias aggregate winning_value string as human-readable framing text."""
    if winning_value == "True":
        return "top-anchored framing" if with_framing_suffix else "top-anchored"
    return "centered framing" if with_framing_suffix else "centered"


def comparative_reach_leader(agg: dict, min_gap: float) -> tuple[str, dict] | None:
    """Return (leader_value, leader_row) when agg has >=2 values and the reach leader beats the
    runner-up by at least min_gap. None on any doubt (thin, tie, or insufficient gap)."""
    if len(agg) < 2:
        return None
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["reach_mean"], kv[0]))
    leader_value, leader_row = ranked[0]
    diff = leader_row["reach_mean"] - ranked[1][1]["reach_mean"]
    if diff <= 0 or diff < min_gap:
        return None
    return leader_value, leader_row
