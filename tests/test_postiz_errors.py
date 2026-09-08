# tests/test_postiz_errors.py
"""postiz_errors — the guarded Postgres-side read + parser behind reconcile's failure detail.

The Postiz public API hides Post.error (an ERROR row of GET /public/v1/posts carries only `state`),
so classification runs on the stored Temporal envelope. These tests pin the two real failure shapes
observed live 2026-08-10 (workflow-gate "Refresh channel needed"; Graph subcode 2207077 media-fetch)
and the fail-open guards: no docker, malformed ids, nonzero exit, timeout, unparseable rows — each
degrades to {} / (unknown, "") rather than raising or inventing detail."""
import base64
import subprocess
from types import SimpleNamespace

from fanops.models import ErrorKind
from fanops.post import postiz_errors as pe

_REFRESH = "Refresh channel needed"
# Trimmed to the shape that matters: the escaped Graph body nested inside the Temporal envelope.
_GRAPH_503 = (
    '{"cause":{"failure":{"applicationFailureInfo":{"type":"bad_body","details":[{"json":'
    '"{\\"error\\":{\\"message\\":\\"Fatal\\",\\"type\\":\\"OAuthException\\",\\"code\\":-1,'
    '\\"error_subcode\\":2207077,\\"is_transient\\":false,'
    '\\"error_user_msg\\":\\"An internal server error has occurred. Video download failed with: '
    'HTTP error code 503. Service Unavailable\\"}}"}]}}}}'
)


def test_refresh_needed_is_auth_and_prescribes_reconnect():
    kind, why = pe.classify_error_text(_REFRESH)
    assert kind is ErrorKind.auth
    assert "reconnect" in why.lower()


def test_video_download_503_is_transient_with_the_http_code():
    kind, why = pe.classify_error_text(_GRAPH_503)
    assert kind is ErrorKind.transient
    assert "503" in why


def test_bare_subcode_2207077_is_transient():
    kind, why = pe.classify_error_text('x \\"error_subcode\\":2207077 y')
    assert kind is ErrorKind.transient
    assert why


def test_garbage_and_empty_yield_no_detail():
    assert pe.classify_error_text("") == (ErrorKind.unknown, "")
    assert pe.classify_error_text(None) == (ErrorKind.unknown, "")
    assert pe.classify_error_text("total nonsense") == (ErrorKind.unknown, "")


def test_envelope_noise_is_not_a_detail():
    # "Activity task failed" / "Fatal" are the Temporal wrapper, not a cause — surfacing them as the
    # reason would be the same invented-detail defect this module exists to end.
    kind, why = pe.classify_error_text('{"failure":{"message":"Activity task failed"}}')
    assert kind is ErrorKind.unknown
    assert why == ""


def test_an_unrecognized_graph_error_surfaces_its_user_message():
    text = '{"json":"{\\"error\\":{\\"error_user_msg\\":\\"Your video is too long for Reels.\\"}}"}'
    kind, why = pe.classify_error_text(text)
    assert kind is ErrorKind.unknown
    assert "too long" in why


def test_fetch_degrades_to_empty_without_docker(monkeypatch):
    monkeypatch.setattr(pe, "_docker_bin", lambda: None)
    assert pe.fetch_error_details(["cmsabcdefgh"]) == {}


def test_fetch_filters_malformed_ids_out_of_the_sql(monkeypatch):
    calls = []
    monkeypatch.setattr(pe, "_docker_bin", lambda: "/usr/bin/docker")

    def fake_run(cmd, **kw):
        calls.append(" ".join(cmd))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert pe.fetch_error_details(["cms_ok_12345", "bad'id; drop--"]) == {}
    assert calls and "drop" not in calls[0]
    assert "cms_ok_12345" in calls[0]


def test_fetch_parses_base64_rows(monkeypatch):
    blob = base64.b64encode(_REFRESH.encode()).decode()
    monkeypatch.setattr(pe, "_docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=f"cmsabcdefgh|{blob}\n"))
    assert pe.fetch_error_details(["cmsabcdefgh"]) == {"cmsabcdefgh": _REFRESH}


def test_fetch_swallows_nonzero_exit_and_timeout(monkeypatch):
    monkeypatch.setattr(pe, "_docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    assert pe.fetch_error_details(["cmsabcdefgh"]) == {}

    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert pe.fetch_error_details(["cmsabcdefgh"]) == {}
