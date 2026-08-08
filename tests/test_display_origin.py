# tests/test_display_origin.py — permanent MomentOrigin → operator wording (MOL-808).
#
# `display_origin` / `UNLABELLED_DISPLAY` live beside `MomentOrigin` in models.py. These asserts pin
# field display for Review cards (`unknown` reads as `unlabelled`); they are not migration tests.
from fanops.models import MomentOrigin, UNLABELLED_DISPLAY, display_origin


def test_an_unobserved_origin_reads_unlabelled_to_an_operator():
    # `unknown` is the honest at-rest value; "unlabelled" is what it MEANS on a surface. One function
    # decides, so every operator surface that shows origin uses the same wording.
    assert UNLABELLED_DISPLAY == "unlabelled"
    assert display_origin(MomentOrigin.unknown) == "unlabelled"
    assert display_origin(MomentOrigin.machine_inferred) == "machine_inferred"
    assert display_origin(MomentOrigin.operator) == "operator"
