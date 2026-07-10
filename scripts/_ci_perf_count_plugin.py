"""Pytest plugin: count refresh_env calls (CI perf investigation only)."""
import tests.conftest as cf
from fanops.config import Config

_count = {"refresh_env": 0, "refresh_all": 0}
_orig_re = Config.refresh_env
_orig_all = cf._refresh_all_config_env


def _wrap_refresh(self):
    _count["refresh_env"] += 1
    return _orig_re(self)


def _wrap_all():
    _count["refresh_all"] += 1
    return _orig_all()


Config.refresh_env = _wrap_refresh
cf._refresh_all_config_env = _wrap_all


def pytest_sessionfinish(session, exitstatus):
    print(f"\nREFRESH_ENV_COUNT={_count['refresh_env']}")
    print(f"REFRESH_ALL_COUNT={_count['refresh_all']}")
