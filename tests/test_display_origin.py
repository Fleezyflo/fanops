# tests/test_display_origin.py — permanent MomentOrigin → operator wording (MOL-808 prep).
#
# `display_origin` / `UNLABELLED_DISPLAY` live beside `MomentOrigin` in models.py so the one-shot
# origin_backfill module can be deleted later without breaking Review cards. These three asserts
# moved out of test_origin_backfill.py; they pin field display, not migration.
from fanops.models import MomentOrigin, UNLABELLED_DISPLAY, display_origin


def test_an_unobserved_origin_reads_unlabelled_to_an_operator():
    # `unknown` is the honest at-rest value; "unlabelled" is what it MEANS on a surface. One function
    # decides, so the Review card and the Home panel can never disagree.
    assert UNLABELLED_DISPLAY == "unlabelled"
    assert display_origin(MomentOrigin.unknown) == "unlabelled"
    assert display_origin(MomentOrigin.machine_inferred) == "machine_inferred"
    assert display_origin(MomentOrigin.operator) == "operator"
