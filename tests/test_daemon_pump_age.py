# tests/test_daemon_pump_age.py
# The keeper's code-drift self-heal is the ONLY thing that moves the resident pump onto new code (the
# pump's in-process execv adopter was deleted). Its storm guard skips when the pump's age is unreadable —
# correct in itself, but `_pump_pid_age_s` asked BSD ps for `etimes`, a GNU/procps keyword that does not
# exist on macOS. ps printed "etimes: keyword not found" to stderr, exited 0, left stdout empty, so age was
# ALWAYS None and the guard ALWAYS skipped: auto-adopt was permanently inert, not merely delayed.
#
# Observed live 2026-07-16: the pump sat on a day-old SHA through 18 merges while the keeper logged
# "skipping to avoid a restart storm" every 120s. These pin the parse and the real-platform behaviour.
import subprocess
import pytest
from fanops.daemon import _parse_etime, _pump_pid_age_s


@pytest.mark.parametrize("s,want", [
    ("      05:32", 332),            # MM:SS, ps right-pads
    ("01:02:03", 3723),              # HH:MM:SS
    ("2-03:04:05", 183845),          # DD-HH:MM:SS
    ("00:00", 0),
    ("10-00:00:00", 864000),
])
def test_parse_etime_bsd_formats(s, want):
    assert _parse_etime(s) == want


@pytest.mark.parametrize("s", ["", "   ", "not-a-time", "1:2:3:4", "abc:def", "-", "5"])
def test_parse_etime_rejects_junk(s):
    assert _parse_etime(s) is None


def test_this_platform_ps_supports_the_keyword_we_ask_for():
    # THE regression. `ps -o etimes=` is silently empty on macOS; whatever we ask for must actually work
    # here, or the storm guard skips forever and the keeper can never adopt new code.
    ps = subprocess.run(["ps", "-o", "etime=", "-p", str(subprocess.os.getpid())],
                        capture_output=True, text=True, timeout=10)
    assert ps.returncode == 0 and ps.stdout.strip(), "ps -o etime= produced nothing on this platform"
    assert _parse_etime(ps.stdout) is not None, "our own process's age must be readable"


def test_pump_age_is_readable_when_a_pid_exists(monkeypatch):
    # With a real PID, age must be a real number — never None. None means the keeper skips forever.
    from fanops import daemon
    me = subprocess.os.getpid()
    monkeypatch.setattr(daemon, "_launchctl",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, f'\t"PID" = {me};\n', ""))
    pid, age = _pump_pid_age_s()
    assert pid == me
    assert isinstance(age, int) and age >= 0, f"pump age unreadable ({age}) — the keeper would never adopt"


def test_no_pid_is_still_none_none(monkeypatch):
    from fanops import daemon
    monkeypatch.setattr(daemon, "_launchctl", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    assert _pump_pid_age_s() == (None, None)
