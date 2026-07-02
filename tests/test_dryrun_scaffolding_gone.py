# tests/test_dryrun_scaffolding_gone.py
# dryrun-boundary M3 (PRD Finding #1): M2 removed every WRITER of the phantom-publish artifacts, so the
# readers/healers that existed only to detect+undo them are now inert dead code. This file pins their
# DELETION — the by-construction proof that the phantom-published class is gone. RED against main (the
# surfaces still exist); GREEN once M3 deletes them.
import pytest


def test_doctor_fix_ghosts_cli_is_removed():
    # The `doctor-fix-ghosts` maintenance verb existed ONLY to heal ghost rows the pipeline can no longer
    # produce. argparse (subparsers required=True) must reject it as an unknown command -> SystemExit(2).
    from fanops.cli import main
    with pytest.raises(SystemExit):
        main(["doctor-fix-ghosts"])


def test_no_doctor_fix_ghosts_symbol():
    # The command handler function itself is deleted.
    import fanops.cli as cli
    assert not hasattr(cli, "cmd_doctor_fix_ghosts"), \
        "cmd_doctor_fix_ghosts must be deleted (M3) — the ghost-row healer is dead code post-boundary"


def test_is_phantom_published_is_removed():
    # The phantom-row DETECTOR is deleted — nothing produces a phantom `published` row any more, so there
    # is nothing to detect. The symbol must be gone from views_results.
    import fanops.studio.views_results as vr
    assert not hasattr(vr, "is_phantom_published"), \
        "is_phantom_published must be deleted (M3) — the phantom-detector is dead code post-boundary"


def test_classify_channel_still_labels_unknown_url_dryrun():
    # FIREWALL: deleting the dedicated `dryrun://` branch must NOT change the label. An empty/unknown-scheme
    # url still classifies as 'dryrun' via the fall-through — the Posted chip is unaffected, only the dead
    # branch goes.
    from fanops.studio.views_results import _classify_channel
    assert _classify_channel(None) == "dryrun"
    assert _classify_channel("") == "dryrun"
    assert _classify_channel("dryrun://p1") == "dryrun"          # legacy value still reads as dryrun
    assert _classify_channel("https://www.instagram.com/reel/AAA/") == "live"
