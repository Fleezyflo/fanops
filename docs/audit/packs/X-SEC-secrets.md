# Pack: X-SEC — secrets

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Finding status below is unchanged. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Closed

| ID | Status | Symbols |
|----|--------|---------|
| SEC-IGPW | closed | `secret_provider._SECRET_KEYS` and `is_secret_env_key` include `FANOPS_IG_SCRAPE_PASSWORD` and keys starting `FANOPS_IG_SCRAPE_PASSWORD_`. `FANOPS_IG_SCRAPE_USER` is not a secret. `config_introspect._SECRET_SUFFIX` includes `_PASSWORD`, so `_is_secret` masks the write-only field as `(set)`. `settings._is_resolved_secret_key` and `_BASE_SECRET_KEYS` resolve the base key the same way as the other base secrets, and resolve a present `FANOPS_IG_SCRAPE_PASSWORD_*` key in `_enriched_env`. The Settings field stays non-studio (`EnvVar(dynamic=True)`). `config.py` already calls `resolve_secret` and was not edited. |
| SEC-ENV | closed | `autopilot.set_env_var` and `autopilot.unset_env_var` hold `ledger._file_lock` (lazy import) on `env_path.with_name(env_path.name + ".lock")` around the read-modify-replace. A `LockBusyError` is re-raised as `OSError` so `golive._dual_write` stays on its existing error channel. `ledger.py` was not edited. |
