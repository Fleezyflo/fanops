"""Issue 1 — live dependency health + best-effort bring-up, so launching the system launches everything
and a down dependency is VISIBLE immediately (not discovered later via a buried downstream error).
subprocess/HTTP are mocked; these prove the health verdicts + the bring-up DECISIONS (which deps it
would start), never a real Docker/Postiz."""
import types
from pathlib import Path
from fanops.config import Config
from fanops import health


def _cfg(tmp_path, monkeypatch, **env):
    monkeypatch.chdir(tmp_path)
    for k in ("POSTIZ_URL", "ZERNIO_API_URL", "FANOPS_POSTIZ_COMPOSE_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config(root=tmp_path)


class _Run:
    """A fake subprocess.run: records every command, returns a chosen returncode keyed by a substring."""
    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}
    def __call__(self, cmd, *a, **k):
        self.calls.append(cmd)
        code = 0
        for prefix, c in self.codes.items():
            if prefix in " ".join(cmd):
                code = c
        return types.SimpleNamespace(returncode=code, stdout=b"", stderr=b"")

    def joined(self):
        return [" ".join(c) for c in self.calls]


# ---------------------------------------------------------------- per-dependency verdicts ----
def test_docker_health_up(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(health.subprocess, "run", _Run({"docker info": 0}))
    h = health._docker_health()
    assert h.name == "docker" and h.ok is True


def test_docker_health_down(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(health.subprocess, "run", _Run({"docker info": 1}))
    assert health._docker_health().ok is False


def test_docker_health_missing_cli(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(health.shutil, "which", lambda n: None)
    assert health._docker_health().ok is False


# MOL-61 — postiz_health now speaks the deeper API-health probe (postiz_health_probe,
# GET /integrations), so a nginx-only-alive-but-Node-crash-looping 502 is caught as DEGRADED
# (not-ok) instead of the old "any HTTP answer == reachable" blind spot. We mock the shared
# probe by mocking the requests.get inside fanops.post.postiz (its network layer).
def _mock_probe(monkeypatch, *, status=None, refused=False):
    from fanops.post import postiz as pz
    monkeypatch.setenv("POSTIZ_API_KEY", "test-key")     # so the probe reaches the (mocked) network, not a pre-auth raise
    def _get(*a, **k):
        if refused:
            raise pz.requests.exceptions.ConnectionError("refused")
        return types.SimpleNamespace(status_code=status, text="", json=lambda: [])
    monkeypatch.setattr(pz.requests, "get", _get)


def test_postiz_health_ok_when_backend_healthy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    _mock_probe(monkeypatch, status=200)                 # backend answers 200 on /integrations
    h = health.postiz_health(cfg)
    assert h.ok is True and h.detail == "reachable"


def test_postiz_health_degraded_on_502(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    _mock_probe(monkeypatch, status=502)                 # nginx alive, Node backend 502s
    h = health.postiz_health(cfg)
    assert h.ok is False                                 # DEGRADED -> not-ok so dep-alert fires
    assert "502" in h.detail and "stalled" in h.detail.lower()


def test_postiz_health_degraded_on_401(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    _mock_probe(monkeypatch, status=401)                 # backend answers but rejects the key
    h = health.postiz_health(cfg)
    assert h.ok is False and "401" in h.detail           # HTTP answer, API unhealthy -> degraded


def test_postiz_health_unreachable_on_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    _mock_probe(monkeypatch, refused=True)               # connection refused / network down
    h = health.postiz_health(cfg)
    assert h.ok is False and h.detail == "unreachable"   # no HTTP answer -> unreachable (existing path)


def test_postiz_health_not_configured(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)                    # no POSTIZ_URL
    assert health.postiz_health(cfg).ok is False and health.postiz_health(cfg).detail == "not configured"


def test_system_health_lists_docker_postiz_zernio(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(health.subprocess, "run", _Run({"docker info": 0}))
    monkeypatch.setattr(health.requests, "get", lambda *a, **k: types.SimpleNamespace(status_code=200))
    _mock_probe(monkeypatch, status=200)                 # MOL-61: postiz row now rides the deeper probe
    assert [d.name for d in health.system_health(cfg)] == ["docker", "postiz", "zernio"]


# MOL-61 — the degraded postiz row must render the SAME err class + dep-alert as an unreachable one
# (MOL-48's machinery keys off d.ok, so a not-ok degraded row fires it with zero template change).
def test_golive_health_renders_dep_alert_for_degraded_postiz(tmp_path, monkeypatch):
    from jinja2 import Environment, FileSystemLoader
    tpl_dir = Path(__file__).resolve().parents[1] / "src" / "fanops" / "studio" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)))
    rows = [health.DepHealth("docker", True, "daemon up"),
            health.DepHealth("postiz", False, "answers HTTP but API unhealthy (502) — publishes stalled"),
            health.DepHealth("zernio", True, "reachable")]
    out = env.get_template("_golive_health.html").render(health=rows)
    assert "dep-alert" in out and "postiz" in out         # Tier-1 alert fires on the degraded row
    assert 'class="err"' in out                           # the row itself carries the err treatment


# ---------------------------------------------------------------- compose-dir resolution ----
def test_compose_dir_env_override_existing(tmp_path, monkeypatch):
    d = tmp_path / "compose"; d.mkdir()
    _cfg(tmp_path, monkeypatch, FANOPS_POSTIZ_COMPOSE_DIR=str(d))
    assert health._postiz_compose_dir() == d


def test_compose_dir_env_override_missing_returns_none(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, FANOPS_POSTIZ_COMPOSE_DIR=str(tmp_path / "nope"))
    assert health._postiz_compose_dir() is None


# ---------------------------------------------------------------- ensure_up bring-up plan ----
def test_ensure_up_brings_up_postiz_when_down(tmp_path, monkeypatch):
    compose = tmp_path / "compose"; compose.mkdir()
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api",
               FANOPS_POSTIZ_COMPOSE_DIR=str(compose))
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/" + (n or "x"))
    run = _Run({"docker info": 0})                       # docker UP
    monkeypatch.setattr(health.subprocess, "run", run)
    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    monkeypatch.setattr(health, "postiz_health", lambda c: health.DepHealth("postiz", False, "down"))
    health.ensure_up(cfg)
    j = run.joined()
    assert any("compose" in x and "up" in x and str(compose) in x for x in j)   # brought Postiz up
    assert not any("open" in x and "Docker" in x for x in j)                    # docker already up -> no launch


def test_ensure_up_starts_docker_when_daemon_down(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/" + (n or "x"))
    run = _Run({"docker info": 1})                       # docker stays DOWN
    monkeypatch.setattr(health.subprocess, "run", run)
    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    monkeypatch.setattr(health, "postiz_health", lambda c: health.DepHealth("postiz", True, "up"))
    health.ensure_up(cfg)
    assert any("open" in x and "Docker" in x for x in run.joined())             # attempted to launch Docker


def test_ensure_up_noop_when_all_up(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/" + (n or "x"))
    run = _Run({"docker info": 0})                       # docker UP
    monkeypatch.setattr(health.subprocess, "run", run)
    monkeypatch.setattr(health, "postiz_health", lambda c: health.DepHealth("postiz", True, "up"))
    health.ensure_up(cfg)
    assert not any("open" in x for x in run.joined())                          # nothing to start
    assert not any("compose" in x and "up" in x for x in run.joined())
