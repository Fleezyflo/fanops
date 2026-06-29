"""R2 RED — accounts.json routing co-constraint.

PRD Evidence (lifecycle-deep-audit-2026-06 + this session's cisumwolfhom incident):
`cisumwolfhom` had `integrations.instagram=cmqno5ops0...` set (operator-connected via Postiz UI
in a prior session) but `backends.instagram` was unset. Result: the routing resolver fell back
to the global `FANOPS_POSTER=dryrun` bridge and the channel went through DryRunPoster instead
of PostizPoster. The operator never saw an error — the system silently posted to dryrun on a
"live" config.

Root: `accounts.json` per-platform routing has TWO independent writers (`set_backend`,
`write_integration`); nothing constrains them to be set together. `Accounts.validate` doesn't
catch the `(integrations[X] set AND backends[X] unset)` combo, and the legacy
`FANOPS_POSTER` bridge silently routes incomplete channels.

R2 fix: (1) `Accounts.validate` REJECTS the bad combo (the structural gate at go_live time);
(2) a new atomic `set_channel_routing(cfg, handle, platform, backend, integration_id)` API
writes BOTH in one transaction — the operator cannot construct the drift state by calling the
canonical setter. The legacy setters stay for narrow ops but the validator catches their drift.

These tests pin: D4 (atomic writes), D5 (validator + go_live refusal), D15 (validator gap)."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts, set_backend, write_integration


# ---------- D5/D15: validator must catch (integration set, backend unset) ----------

def _seed_accounts(cfg: Config, *, handle: str = "@a", integrations: dict | None = None,
                   backends: dict | None = None, platforms: list | None = None) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [{
        "handle": handle, "account_id": "legacy_id", "platforms": platforms or ["instagram"],
        "status": "active", "integrations": integrations or {}, "backends": backends or {},
    }]}))


def test_accounts_validate_rejects_integration_without_backend(tmp_path):
    """R2/D5/D15: an account with integrations.instagram set but backends.instagram absent
    is the cisumwolfhom drift state. validate() MUST surface it as a problem, not pass clean."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={"instagram": "cmqno5ops0xyz"}, backends={})

    accts = Accounts.load(cfg)
    problems = accts.validate()

    assert any("integration" in p.lower() and "backend" in p.lower() and "@a" in p
               for p in problems), (
        f"validate() did not catch the integration-without-backend drift (problems={problems}). "
        f"R2/D5/D15: this combo silently fell back to FANOPS_POSTER=dryrun on a 'live' config.")


def test_accounts_validate_passes_when_both_set(tmp_path):
    """R2 firewall: when integrations AND backends both set, validate stays clean — the fix
    must not over-trigger on the canonical happy path."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={"instagram": "cmqno5ops0xyz"},
                   backends={"instagram": "postiz"})

    accts = Accounts.load(cfg)
    problems = accts.validate()

    assert not any("integration" in p.lower() and "backend" in p.lower() for p in problems), (
        f"validate() flagged a CORRECTLY-routed channel as a problem (problems={problems}). "
        f"R2 over-trigger: the rule must fire on drift, not on the happy path.")


def test_accounts_validate_passes_when_neither_set(tmp_path):
    """R2 firewall: neither integration nor backend set is the LEGACY shared-account_id case
    — validate() must keep allowing it (account_id is the fallback). Only the drift state
    (one set, the other unset) is the bug."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={}, backends={})

    accts = Accounts.load(cfg)
    problems = accts.validate()

    assert not any("integration" in p.lower() and "backend" in p.lower() for p in problems), (
        f"validate() flagged a legacy account_id-fallback channel (problems={problems}). "
        f"R2 over-trigger: the rule must fire on drift, NOT on the unmapped-yet legacy case.")


def test_accounts_validate_catches_drift_per_platform(tmp_path):
    """R2 boundary: a multi-platform handle with IG drift but TikTok clean must surface the
    IG problem ALONE — the rule scopes per (handle, platform), not per handle."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, platforms=["instagram", "tiktok"],
                   integrations={"instagram": "ig_id", "tiktok": "tk_id"},
                   backends={"tiktok": "postiz"})    # ig drift, tk clean

    accts = Accounts.load(cfg)
    problems = accts.validate()

    drift = [p for p in problems if "integration" in p.lower() and "backend" in p.lower()]
    assert len(drift) == 1, f"expected exactly 1 drift problem, got {len(drift)}: {drift}"
    assert "instagram" in drift[0].lower(), (
        f"drift problem must name 'instagram' (got: {drift[0]!r})")
    assert "tiktok" not in drift[0].lower(), (
        f"drift problem leaked into the clean TikTok channel: {drift[0]!r}")


# ---------- D5: go_live refuses an accounts.json with drift ----------

def test_go_live_refuses_with_incomplete_routing(tmp_path, monkeypatch):
    """R2/D5: the new validator rule flows through golive.py:497 — go_live(confirmed=True)
    on an accounts.json with drift returns ok=False and surfaces the drift problem in the
    error string. NEVER flip FANOPS_LIVE on a drift config."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={"instagram": "cmqno5ops0xyz"}, backends={})

    from fanops.studio.golive import go_live
    res = go_live(cfg, confirmed=True)

    assert res.ok is False, (
        f"go_live PASSED with a drift accounts.json: res={res} — the live flip must refuse "
        f"a config that silently routes to dryrun.")
    assert "integration" in (res.error or "").lower() and "backend" in (res.error or "").lower(), (
        f"go_live error must name the drift problem; got: {res.error!r}")


# ---------- D4: set_channel_routing atomic ----------

def test_set_channel_routing_writes_both_atomically(tmp_path):
    """R2/D4: the new `set_channel_routing` atomic API writes integrations[platform] AND
    backends[platform] in ONE transaction — never one without the other. After a single
    call, BOTH sides are present and the load reflects them."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={}, backends={})

    set_channel_routing(cfg, "@a", "instagram", backend="postiz", integration_id="ig_id_1")

    accts = Accounts.load(cfg)
    a = next(x for x in accts.accounts if x.handle == "@a")
    assert a.integrations.get("instagram") == "ig_id_1", (
        f"integrations.instagram not written: {a.integrations!r}")
    assert a.backends.get("instagram") == "postiz", (
        f"backends.instagram not written: {a.backends!r}")
    # And validate is clean — the canonical write must produce a clean accounts.json.
    drift = [p for p in accts.validate()
             if "integration" in p.lower() and "backend" in p.lower()]
    assert not drift, f"set_channel_routing produced a drift state: {drift}"


def test_set_channel_routing_rejects_empty_integration_id(tmp_path):
    """R2/D4: refusing empty input at the write boundary closes the structural door. An
    empty integration_id is never a valid routing — the API must raise, not silently write
    an empty string."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)

    with pytest.raises(ValueError, match="integration_id"):
        set_channel_routing(cfg, "@a", "instagram", backend="postiz", integration_id="")


def test_set_channel_routing_rejects_empty_backend(tmp_path):
    """R2/D4: same closure on the backend side — an empty backend creates the very drift
    state we are fixing. The API refuses it at the boundary."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)

    with pytest.raises(ValueError, match="backend"):
        set_channel_routing(cfg, "@a", "instagram", backend="", integration_id="ig_id_1")


def test_set_channel_routing_rejects_unknown_backend(tmp_path):
    """R2/D4: a backend value not in _VALID_BACKENDS fails the write — the platform-routing
    enum truth lives in Config, the writer just enforces it."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)

    with pytest.raises(ValueError, match="backend"):
        set_channel_routing(cfg, "@a", "instagram", backend="notarealbackend",
                            integration_id="ig_id_1")


def test_set_channel_routing_rejects_unknown_handle(tmp_path):
    """R2/D4: an unknown handle raises KeyError so the caller surfaces a clean error,
    same shape as set_backend/write_integration today."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, handle="@a")

    with pytest.raises(KeyError):
        set_channel_routing(cfg, "@nope", "instagram", backend="postiz",
                            integration_id="ig_id_1")


def test_set_channel_routing_overwrites_existing(tmp_path):
    """R2/D4: re-routing an already-mapped channel REPLACES both sides — the API is
    idempotent in the success direction (operator re-maps after disconnect/reconnect)."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={"instagram": "old_id"},
                   backends={"instagram": "zernio"})

    set_channel_routing(cfg, "@a", "instagram", backend="postiz", integration_id="new_id")

    accts = Accounts.load(cfg)
    a = next(x for x in accts.accounts if x.handle == "@a")
    assert a.integrations["instagram"] == "new_id"
    assert a.backends["instagram"] == "postiz"


def test_set_channel_routing_preserves_other_platforms(tmp_path):
    """R2/D4: writing IG routing must leave a sibling TikTok routing untouched — the write
    is per (handle, platform), not per handle. Mirrors set_backend's sibling-preservation."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, platforms=["instagram", "tiktok"],
                   integrations={"tiktok": "tk_id_keep"},
                   backends={"tiktok": "zernio"})

    set_channel_routing(cfg, "@a", "instagram", backend="postiz", integration_id="ig_new")

    accts = Accounts.load(cfg)
    a = next(x for x in accts.accounts if x.handle == "@a")
    assert a.integrations["tiktok"] == "tk_id_keep", (
        f"sibling TikTok integration clobbered: {a.integrations!r}")
    assert a.backends["tiktok"] == "zernio", (
        f"sibling TikTok backend clobbered: {a.backends!r}")
    assert a.integrations["instagram"] == "ig_new"
    assert a.backends["instagram"] == "postiz"


def test_set_channel_routing_refuses_platform_not_on_account(tmp_path):
    """R2/D4: routing a platform the account doesn't carry is a config error — never
    silently written. Mirrors set_backend's same guard."""
    from fanops.accounts import set_channel_routing
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, platforms=["instagram"])

    with pytest.raises(ValueError, match="tiktok|carry"):
        set_channel_routing(cfg, "@a", "tiktok", backend="postiz", integration_id="tk_id")


# ---------- D4: legacy setters still work (back-compat) but the validator catches their drift ----------

def test_legacy_set_backend_alone_leaves_drift_caught_by_validator(tmp_path):
    """R2 back-compat: calling set_backend WITHOUT a paired write_integration still works
    (don't break a narrow caller) — but the next validate() catches the drift. The fix is
    the structural rule, not a runtime refusal of the legacy seam."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={}, backends={})

    set_backend(cfg, "@a", "instagram", "postiz")    # backend set, integration NOT

    accts = Accounts.load(cfg)
    drift = [p for p in accts.validate()
             if "backend" in p.lower() and "integration" in p.lower()]
    # The mirror case of the cisumwolfhom incident — equally bad, same rule must catch it.
    assert drift, (
        f"validate() did not catch the inverse drift (backend set, integration unset). "
        f"R2: the structural rule must cover both sides of the asymmetric pair.")


# ---------- doctor --fix-routing scaffold ----------

def test_doctor_fix_routing_lists_drift_in_dryrun_mode(tmp_path, monkeypatch, capsys):
    """R2/D4 follow-up: `fanops doctor --fix-routing` is a READ-ONLY surveyor by default —
    it LISTS every (handle, platform) drift state with a proposed fix. NEVER auto-writes.
    The operator runs it, reads the proposal, then runs `set_channel_routing` themselves."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, integrations={"instagram": "cmqno5ops0xyz"}, backends={})

    from fanops.cli import main
    rc = main(["doctor", "--fix-routing"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "@a" in out and "instagram" in out, (
        f"doctor --fix-routing did not name the drifted channel; output={out!r}")
    assert "postiz" in out.lower() or "propose" in out.lower() or "set_channel_routing" in out, (
        f"doctor --fix-routing did not propose a fix; output={out!r}")
    # And the file is UNCHANGED — read-only.
    after = Accounts.load(cfg)
    a = next(x for x in after.accounts if x.handle == "@a")
    assert a.backends == {}, (
        f"doctor --fix-routing wrote to accounts.json in dry-run/read-only mode: {a.backends!r}")
