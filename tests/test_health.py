"""Issue 1 — live dependency health, so a down dependency is VISIBLE immediately
(not discovered later via a buried downstream error).
subprocess/HTTP are mocked; these prove the health verdicts, never a real Docker/Postiz."""
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
    cfg = _cfg(tmp_path, monkeypatch)                    # no POSTIZ_URL / key
    h = health.postiz_health(cfg)
    assert h.ok is True and h.detail == "skipped (not configured)"


def test_system_health_lists_docker_postiz_zernio(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:4007/api")
    monkeypatch.setattr(health.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(health.subprocess, "run", _Run({"docker info": 0}))
    _mock_probe(monkeypatch, status=200)                 # MOL-61: postiz row now rides the deeper probe
    assert [d.name for d in health.system_health(cfg)] == ["docker", "postiz", "zernio"]


# MOL-61 — the degraded postiz row must render the SAME err class + dep-alert as an unreachable one
# (MOL-48's machinery keys off d.ok, so a not-ok degraded row fires it with zero template change).
def test_golive_health_renders_dep_alert_for_degraded_postiz(tmp_path, monkeypatch):
    from jinja2 import Environment, FileSystemLoader
    tpl_dir = Path(__file__).resolve().parents[1] / "src" / "fanops" / "studio" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)))
    env.globals["url_for"] = lambda *a, **k: "/golive/health?refresh=1"  # template-only render; no Flask app
    rows = [health.DepHealth("docker", True, "daemon up"),
            health.DepHealth("postiz", False, "answers HTTP but API unhealthy (502) — publishes stalled"),
            health.DepHealth("zernio", True, "reachable")]
    postiz_hint = {"parked": False, "hint": ""}          # S10 route contract: parked postiz suppresses dep-alert
    blocking_deps = [d for d in rows if not d.ok and not (d.name == "postiz" and postiz_hint.get("parked"))]
    out = env.get_template("_golive_health.html").render(health=rows, postiz_hint=postiz_hint,
                                                         blocking_deps=blocking_deps)
    assert "dep-alert" in out and "postiz" in out         # Tier-1 alert fires on the degraded row
    assert 'class="err"' in out                           # the row itself carries the err treatment


# ---------------------------------------------------------------- compose-dir resolution ----
def test_compose_dir_env_override_existing(tmp_path, monkeypatch):
    d = tmp_path / "compose"; d.mkdir()
    cfg = _cfg(tmp_path, monkeypatch, FANOPS_POSTIZ_COMPOSE_DIR=str(d))
    assert health._postiz_compose_dir(cfg) == d


def test_compose_dir_env_override_missing_returns_none(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, FANOPS_POSTIZ_COMPOSE_DIR=str(tmp_path / "nope"))
    assert health._postiz_compose_dir(cfg) is None


def test_read_snapshots_missing_are_missing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for sr in (health.read_dep_snapshot(cfg), health.read_daemon_strip_snapshot(cfg),
               health.read_strip_metrics(cfg)):
        assert sr.freshness is health.SnapshotFreshness.MISSING
        assert sr.data is None


def test_refresh_runtime_snapshots_writes_three_json_files(tmp_path, monkeypatch):
    from fanops.post.postiz import PostizHealth
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr("fanops.post.postiz.postiz_health_probe",
                        lambda c: PostizHealth(True, 200, ""))
    monkeypatch.setattr("fanops.daemon.installed_interval", lambda c: 600)
    monkeypatch.setattr("fanops.daemon.status", lambda c, interval=600: {
        "installed": False, "loaded": False, "pid": None, "last_exit": None,
        "heartbeat_age_s": None, "verdict": "not installed"})
    monkeypatch.setattr("fanops.daemon.sibling_agents_status", lambda: [])
    health.refresh_runtime_snapshots(cfg)
    assert cfg.deps_health_path.exists()
    assert cfg.daemon_strip_path.exists()
    assert cfg.strip_metrics_path.exists()
    for sr in (health.read_dep_snapshot(cfg), health.read_daemon_strip_snapshot(cfg),
               health.read_strip_metrics(cfg)):
        assert sr.freshness is health.SnapshotFreshness.FRESH
        assert isinstance(sr.data, dict)



def test_ancient_checked_at_is_stale_not_fresh(tmp_path, monkeypatch):
    """Anti-regression B: ancient checked_at → STALE; consumers must not treat as calm truth."""
    import json
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.strip_metrics_path.write_text(json.dumps({
        "checked_at": "2020-01-01T00:00:00Z",
        "blocked_gates": 0,
        "recoverable_sources": 0,
        "errored_first_id": None,
    }))
    sr = health.read_strip_metrics(cfg)
    assert sr.freshness is health.SnapshotFreshness.STALE
    assert isinstance(sr.data, dict)


def test_refresh_runtime_snapshots_is_named_strip_writer():
    """CPDP-WP4: strip writer role is health.refresh_runtime_snapshots (FunctionDef exists).
    Sole Call site: cli --loop (observe/GET paths must not Call it)."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / "fanops" / "health.py").read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "refresh_runtime_snapshots" in names


def test_only_cli_calls_refresh_runtime_snapshots():
    """CPDP-02: the only src/fanops Call of refresh_runtime_snapshots is cli.py (pump)."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "fanops"
    hits = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            cname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if cname == "refresh_runtime_snapshots":
                hits.append(str(path.relative_to(root)))
    assert set(hits) == {"cli.py"}, hits


def test_snapshot_readers_never_call_refresh_write_or_probe():
    """CPDP-WP4: named snapshot readers must not Call refresh_*/write_json_atomic/postiz_health_probe."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "fanops"
    targets = (
        ("health.py", "_read_snapshot"),
        ("health.py", "read_dep_snapshot"),
        ("health.py", "read_daemon_strip_snapshot"),
        ("health.py", "read_strip_metrics"),
        ("health_model.py", "snapshot_postiz_probe"),
        ("health_model.py", "snapshot_daemon_status"),
        ("health_model.py", "deps_from_snapshot"),
        ("health_model.py", "strip_metrics_freshness_check"),
        ("studio/views_common.py", "postiz_health_for_banner"),
        ("studio/app_routes_golive.py", "do_golive_health"),
    )
    banned = {
        "refresh_runtime_snapshots", "refresh_dep_snapshot", "refresh_daemon_strip_snapshot",
        "refresh_strip_metrics", "write_json_atomic", "postiz_health_probe",
    }
    hits = []
    for rel, fname in targets:
        tree = ast.parse((root / rel).read_text())
        fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fname), None)
        assert fn is not None, f"missing FunctionDef {rel}:{fname}"
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            cname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if cname in banned:
                hits.append(f"{rel}:{fname}->{cname}")
    assert hits == [], f"snapshot readers must not call writers/probes: {hits}"
