"""Go-Live route group for the Studio: Postiz connection, per-account/channel integration mapping, the
dryrun<->live switch, casting/lever toggles, and the A/B learning-loop intent flags. register_golive_routes
(app, cfg) registers them on the shared app under their ORIGINAL endpoint names (function names unchanged, so
url_for is byte-identical); create_app calls it. Helpers/constants come from app (fully loaded before create_app
runs, so importing them here is cycle-free)."""
from __future__ import annotations
from flask import render_template, request
from fanops.studio import actions, golive, views
from fanops.studio.app import _ALL_PLATFORMS, _LEVER_EFFECTS


def register_golive_routes(app, cfg):
    @app.get("/golive")
    def golive_view():
        return render_template("golive.html", status=views.golive_status(cfg), result=None,
                               all_platforms=_ALL_PLATFORMS, effects=_LEVER_EFFECTS, tab="golive")

    def _golive_panel(result):
        # Re-render the panel with FRESH golive_status after an action (htmx swaps #golive-panel), so the
        # mode banner + readiness checks update in place — mirrors _run_panel. S8: `effects` carries the
        # engine-true _LEVER_EFFECTS so the clip-length bands render from the catalog, never a stale literal.
        return render_template("_golive_panel.html", status=views.golive_status(cfg), result=result,
                               all_platforms=_ALL_PLATFORMS, effects=_LEVER_EFFECTS, tab="golive")

    @app.post("/golive/config")
    def do_golive_config():
        return _golive_panel(golive.set_postiz_config(cfg, request.form.get("url", ""), request.form.get("key", "")))

    @app.post("/golive/account/add")
    def do_golive_account_add():
        # Onboard a new account from the UI: handle + platform checkboxes + optional persona -> a new
        # active/postiz account appended to accounts.json (no JSON hand-edit), ready to map below.
        return _golive_panel(golive.add_account(cfg, request.form.get("handle", ""),
                                                request.form.getlist("platform"),
                                                request.form.get("persona", "")))

    @app.post("/golive/hooks")
    def do_golive_hooks():
        # Toggle per-account on-screen hooks (FANOPS_CREATIVE_VARIATION). Explicit "1"==on (NOT bool(str) —
        # bool("0") is True; the off button sends value=""/anything-not-1). Works in dryrun or live (changes
        # per-account render, not whether posts publish).
        return _golive_panel(golive.set_per_account_hooks(cfg, request.form.get("on") == "1"))

    @app.post("/golive/casting")
    def do_golive_casting():
        # Toggle per-account moment casting (FANOPS_ACCOUNT_CASTING). Same shape as do_golive_hooks: explicit
        # "1"==on (NOT bool(str) — bool("0") is True; the off button sends value=""). Works in dryrun or live
        # (changes which posts are BORN, not whether they publish).
        return _golive_panel(golive.set_account_casting(cfg, request.form.get("on") == "1"))

    @app.post("/golive/clip-profile")
    def do_golive_clip_profile():
        # Phase 2: set the clip-length band (FANOPS_CLIP_PROFILE = talk|song); validated in the setter.
        return _golive_panel(golive.set_clip_profile(cfg, request.form.get("profile", "")))

    @app.post("/golive/responder")
    def do_golive_responder():
        # THE explicit AI switch (FANOPS_RESPONDER=llm|manual). Explicit "1"==on (NOT bool(str)). This is the
        # ONLY intended way to turn the LLM responder on/off — claude fires because this is on, never on PATH alone.
        return _golive_panel(golive.set_ai_responder(cfg, request.form.get("on") == "1"))

    @app.post("/golive/daemon-install")
    def do_golive_daemon_install():
        # Install + load the launchd pipeline driver (hands-off processing) — no CLI. Scheduling only; inherits
        # the AI switch above, so this never turns the LLM on by itself.
        return _golive_panel(golive.install_daemon(cfg, request.form.get("interval", "10m")))

    @app.post("/golive/daemon-uninstall")
    def do_golive_daemon_uninstall():
        # Unload + remove the launchd pipeline driver — no CLI.
        return _golive_panel(golive.uninstall_daemon(cfg))

    @app.post("/golive/learning")
    def do_golive_learning():
        # Phase 6: toggle the A/B learning master switch (FANOPS_VARIANT_LEARNING) — explicit "1"==on. Intent
        # only; the apply paths stay learning_validated-frozen (ON does NOT unfreeze learning).
        return _golive_panel(golive.set_variant_learning(cfg, request.form.get("on") == "1"))

    @app.post("/golive/amplify")
    def do_golive_amplify():
        # Phase 6: toggle variant-driven amplify (FANOPS_VARIANT_AMPLIFY) — explicit "1"==on.
        return _golive_panel(golive.set_variant_amplify(cfg, request.form.get("on") == "1"))

    @app.post("/golive/ucb")
    def do_golive_ucb():
        # Phase 6: toggle UCB1 variant ranking (FANOPS_VARIANT_UCB) — explicit "1"==on.
        return _golive_panel(golive.set_variant_ucb(cfg, request.form.get("on") == "1"))

    @app.post("/golive/transfer")
    def do_golive_transfer():
        # Phase 6: toggle cross-account hook transfer (FANOPS_VARIANT_TRANSFER) — explicit "1"==on.
        return _golive_panel(golive.set_variant_transfer(cfg, request.form.get("on") == "1"))

    @app.post("/golive/zernio-config")
    def do_golive_zernio_config():
        # Zernio slice 4: connect Zernio (key only, hosted) — dual-writes ZERNIO_API_KEY + tests it.
        return _golive_panel(golive.set_zernio_config(cfg, request.form.get("key", "")))

    @app.post("/golive/account/backend")
    def do_golive_account_backend():
        # Zernio slice 4: route ONE (handle, platform) channel to a backend. A LIVE backend is gated
        # (creds + confirm) in the setter — the per-account 'go live'. confirm box -> confirmed=True.
        return _golive_panel(golive.set_account_backend(
            cfg, request.form.get("handle", ""), request.form.get("platform", ""),
            request.form.get("backend", ""), confirmed=request.form.get("confirm") == "1"))

    @app.post("/golive/account/persona")
    def do_golive_account_persona():
        # Phase 3: set/clear an existing account's persona (was add-time only -> accounts.json hand-edit).
        return _golive_panel(golive.set_persona(cfg, request.form.get("handle", ""), request.form.get("persona", "")))

    @app.post("/golive/account/promote")
    def do_golive_account_promote():
        # Phase 3: promote a demoted/planned account back to active (inverse of demote — no longer one-way).
        return _golive_panel(golive.promote_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/account/remove")
    def do_golive_account_remove():
        # Remove an account from the UI (no JSON hand-edit) — clears a placeholder like @TBD-1; re-render the panel.
        return _golive_panel(golive.remove_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/account/demote")
    def do_golive_account_demote():
        # Demote an account to `planned` — it leaves the active publishing fan-out but keeps its row/history.
        return _golive_panel(golive.demote_account(cfg, request.form.get("handle", "")))

    @app.post("/golive/refresh")
    def do_golive_refresh():
        return _golive_panel(golive.refresh_integrations(cfg))

    @app.post("/golive/discover")
    def do_golive_discover():
        # M4b: list every channel the connected schedulers (Postiz + Zernio) already hold, each proposed
        # for one-click adoption (handle + provider + id + deterministic match). discover_channels never
        # writes — the operator confirms each row in adopt; re-render the panel with the proposed rows.
        return _golive_panel(golive.discover_channels(cfg))

    @app.post("/golive/adopt")
    def do_golive_adopt():
        # M4b: adopt the ticked discovered channels. Each ticked checkbox submits its row INDEX in `adopt`;
        # the row's hidden provider__i/id__i/platform__i + the editable handle__i/persona__i carry the data
        # (so adopt never re-discovers). confirm routes the adopted channels to their scheduler (creds-gated
        # in adopt_channels — without it a channel is mapped but unrouted, never publishing).
        sels = [{"provider": request.form.get(f"provider__{i}", ""), "id": request.form.get(f"id__{i}", ""),
                 "platform": request.form.get(f"platform__{i}", ""), "handle": request.form.get(f"handle__{i}", ""),
                 "persona": request.form.get(f"persona__{i}", "")} for i in request.form.getlist("adopt")]
        return _golive_panel(golive.adopt_channels(cfg, sels, confirmed=request.form.get("confirm") == "1"))

    @app.get("/golive/health")
    def do_golive_health():
        from fanops.health import system_health
        health = system_health(cfg)
        if request.args.get("compact"):
            return render_template("_health_pills.html", health=health)
        return render_template("_golive_health.html", health=health)

    @app.get("/golive/connect")
    def golive_connect():
        return render_template("golive_page.html", status=views.golive_status(cfg), result=None,
                               step="connect", all_platforms=_ALL_PLATFORMS, effects=_LEVER_EFFECTS, tab="golive")

    @app.get("/golive/accounts")
    def golive_accounts_page():
        return render_template("golive_page.html", status=views.golive_status(cfg), result=None,
                               step="accounts", all_platforms=_ALL_PLATFORMS, effects=_LEVER_EFFECTS, tab="golive")

    @app.get("/golive/live")
    def golive_live_page():
        return render_template("golive_page.html", status=views.golive_status(cfg), result=None,
                               step="live", all_platforms=_ALL_PLATFORMS, effects=_LEVER_EFFECTS, tab="golive")

    @app.post("/golive/map")
    def do_golive_map():
        # Batch per-CHANNEL map: one <select name="map__<handle>__<platform>"> per channel, submitted
        # together. Split on the LAST "__" so a handle keeps its own characters; map only the channels the
        # operator actually picked (non-blank), via the per-platform unit action golive.map_account.
        picks = []
        for k in request.form:
            if not k.startswith("map__"):
                continue
            v = (request.form.get(k) or "").strip()
            rest = k[len("map__"):]
            if not v or "__" not in rest:
                continue
            handle, platform = rest.rsplit("__", 1)
            picks.append((handle, platform, v))
        if not picks:
            return _golive_panel(actions.ActionResult(ok=False, error="pick a Postiz integration for at least one channel"))
        errors = [r.error for r in (golive.map_account(cfg, h, p, v) for h, p, v in picks) if not r.ok]
        if errors:
            return _golive_panel(actions.ActionResult(ok=False, error="; ".join(errors)))
        return _golive_panel(actions.ActionResult(ok=True, detail={"mapped": len(picks)}))

    @app.post("/golive/live")
    def do_golive_live():
        # The ONLY route that can set FANOPS_LIVE=1 (the global live switch — provider is per-channel);
        # confirm derived from the checkbox, and go_live itself re-gates on readiness (≥1 channel with a
        # provider+creds) — a stray POST can't flip the system live.
        return _golive_panel(golive.go_live(cfg, confirmed=request.form.get("confirm") == "1"))

    @app.post("/golive/dryrun")
    def do_golive_dryrun():
        return _golive_panel(golive.go_dryrun(cfg))

    @app.post("/golive/validate")
    def do_golive_validate():
        # M3: run the Postiz cutover from the browser to unfreeze the learning loop — posts ONE real
        # throwaway probe to the operator-SELECTED integration behind a confirm. validate_learning
        # re-gates (live-postiz + known integration + confirm); a stray POST can't fire it.
        return _golive_panel(golive.validate_learning(cfg, integration_id=request.form.get("integration_id"),
                                                       confirmed=request.form.get("confirm") == "1"))
