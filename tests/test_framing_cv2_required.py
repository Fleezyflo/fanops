# tests/test_framing_cv2_required.py — the LITERAL "tests fail if cv2 is not there" guarantee, placed in
# the ONLY job where cv2 is a real precondition: the e2e job (the sole job that installs the [framing] extra,
# opencv-python-headless, via requirements/ci-e2e.txt). A plain unit test importing cv2 would fail the
# HERMETIC unit job (cv2 absent there BY DESIGN — see tests/CLAUDE.md), re-creating the exact CI outage this
# whole design avoids. So both tests are @pytest.mark.integration; the e2e job runs with FANOPS_REQUIRE_E2E=1,
# which turns a skip into a FAILURE — meaning if cv2 ever regresses out of ci-e2e.txt, `import cv2` raises and
# the e2e job goes RED. That red is the point.
import pytest
from fanops.config import Config
from fanops import framing


@pytest.mark.integration
def test_cv2_is_installed_in_e2e_toolchain():
    # The [framing] extra ships in ci-e2e.txt (opencv-python-headless==5.0.0.93). This fails LOUD if a lock
    # regen ever drops it — ImportError here IS the regression we want to catch, not silently tolerate.
    import cv2                                    # noqa: PLC0415 — the raise is the signal
    assert cv2 is not None


@pytest.mark.integration
def test_require_cv2_passes_with_real_opencv(tmp_path):
    # Real cv2 + the vendored YuNet model (src/fanops/data/yunet_2023mar.onnx, shipped via package-data) must
    # build the detector — this is the production render precondition the smart-framing path depends on.
    # Raises ToolchainMissingError if cv2 is absent OR the model can't build the detector (either == broken toolchain).
    framing.require_cv2(Config(root=tmp_path))
