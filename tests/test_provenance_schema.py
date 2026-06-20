"""P1 creative-provenance schema: the dims P3/P4 attribute reach to are STAMPED at selection/
crosspost (one writer per field). Clip/Moment carry what the renderer + responder know; Post carries
the full attribution key. All validate-or-default so old ledgers load unchanged."""
from fanops.models import Clip, Post, Platform, Fmt


def test_clip_carries_first_frame_kind_and_cut_seconds():
    c = Clip(id="c1", parent_id="m1", path="/x.mp4", first_frame_kind="visual", cut_seconds=18.0)
    assert c.first_frame_kind == "visual"
    assert c.cut_seconds == 18.0

def test_clip_provenance_defaults_none_for_old_ledgers():
    c = Clip(id="c1", parent_id="m1", path="/x.mp4")
    assert c.first_frame_kind is None and c.cut_seconds is None

def test_post_carries_full_attribution_key():
    p = Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
             caption="x", first_frame_kind="visual",
             clip_profile="song", cut_seconds=22.0, aspect=Fmt.r9x16)
    assert p.first_frame_kind == "visual"
    assert p.clip_profile == "song"
    assert p.cut_seconds == 22.0

def test_post_attribution_defaults_none_for_old_ledgers():
    p = Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
             caption="x")
    assert p.first_frame_kind is None
    assert p.clip_profile is None and p.cut_seconds is None
