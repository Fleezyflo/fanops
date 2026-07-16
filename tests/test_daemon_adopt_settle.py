# tests/test_daemon_adopt_settle.py
# The code-drift storm guard must wait on what it is actually waiting for: the pump's NEXT HEARTBEAT.
#
# It waited on KEEPER_POLL_INTERVAL_S (120s) instead — but the keeper FIRES every 120s, so a pump's age is
# always >= 120s at the next fire and a kickstart went through every single cycle. The pump only stamps its
# running SHA into the heartbeat when a PASS completes (default 600s), so it was SIGTERM'd long before it
# could clear the mismatch. Result: a permanent 120s restart loop in which no pass ever finished.
#
# This never fired historically only because `_pump_pid_age_s` always returned None (BSD ps has no `etimes`),
# so the guard skipped for the wrong reason and masked the off-by-one. Fixing the ps keyword exposed it —
# observed live 2026-07-16: pids 49425 -> 51695 -> 52493 -> 52886 -> 53266 in ~8 minutes, last_exit -15,
# heartbeat SHA frozen the whole time.
from fanops.config import Config
from fanops.daemon import KEEPER_POLL_INTERVAL_S, _adopt_settle_s


def test_settle_exceeds_the_keeper_poll_interval(tmp_path):
    # THE regression: if settle <= the keeper's own cadence, every fire re-kickstarts. Storm by construction.
    cfg = Config(root=tmp_path)
    assert _adopt_settle_s(cfg) > KEEPER_POLL_INTERVAL_S


def test_settle_covers_a_full_pass_plus_a_keeper_tick(tmp_path):
    # The pump cannot report a new SHA until a pass completes, so the guard must outlast one.
    cfg = Config(root=tmp_path)
    assert _adopt_settle_s(cfg) == 600 + KEEPER_POLL_INTERVAL_S      # no plist installed -> default interval


def test_settle_tracks_the_installed_interval(tmp_path, monkeypatch):
    from fanops import daemon
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon, "installed_interval", lambda c: 1800)
    assert daemon._adopt_settle_s(cfg) == 1800 + KEEPER_POLL_INTERVAL_S


def test_settle_survives_an_unreadable_interval(tmp_path, monkeypatch):
    from fanops import daemon
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon, "installed_interval", lambda c: None)
    assert daemon._adopt_settle_s(cfg) == 600 + KEEPER_POLL_INTERVAL_S   # falls back, never 0 (0 = storm)
