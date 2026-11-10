# Pack: X-SEC — secrets

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `secret_provider._SECRET_KEYS` includes `FANOPS_IG_SCRAPE_PASSWORD`; `autopilot.set_env_var` holds `ledger._file_lock`. `config.py` was not edited. The 69 cap does not bind. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| SEC-IGPW | medium | leak | On subject SHA, every Settings credential-like field (`POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `ANTHROPIC_API_KEY`, `FANOPS_IG_SCRAPE_PASSWORD`) is masked by `config_introspect._is_secret` / `_display_value` as `(set)` when non-empty; `fanops config` omits the plaintext. `FANOPS_IG_SCRAPE_USER` stays visible. Studio static/templates carry no scrape-password or API-key values (write-only password inputs and bool read-models only). | Write-only vars in `docs/CONFIG.md` must not appear in config introspection or Studio render paths. | `secret_provider._SECRET_KEYS` and `is_secret_env_key` include `FANOPS_IG_SCRAPE_PASSWORD` and `FANOPS_IG_SCRAPE_PASSWORD_*`; `config_introspect._SECRET_SUFFIX` includes `_PASSWORD`; `settings._BASE_SECRET_KEYS` / `_is_resolved_secret_key` resolve base and dynamic scrape keys; field is `EnvVar(dynamic=True)` non-studio. `tests/test_config_verb.py` (`test_config_row_masks_ig_scrape_password`, `test_format_config_report_masks_ig_scrape_password`). | closed |
| SEC-ENV | medium | race | On subject SHA, `autopilot.set_env_var` and `unset_env_var` wrap read-modify-write in `_env_rewrite_lock`, which holds `ledger._file_lock` on `<env>.lock`; `LockBusyError` re-raises as `OSError` for `golive._dual_write`. | Concurrent `.env` rewrites must not clobber each other. | `autopilot._env_rewrite_lock`, `tests/test_autopilot.py` (`test_set_unset_env_var_lock_busy_raises_oserror`, lock contention tests). `ledger.py` not edited. | closed |

## Audit slice 2026-10-18

Suffix list vs Settings credential fields: `_SECRET_SUFFIX` covers all seven credential-like Settings names (including `R2_ACCESS_KEY_ID` via `_ACCESS_KEY` substring match). `is_secret_env_key` aligns with keyring-routed secrets (`POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN`, `FANOPS_IG_SCRAPE_PASSWORD` and dynamic families); R2 keys are `.env`-only but still masked in introspection.

`docs/CONFIG.md` write-only rows for API keys, tokens, R2 keys, and `FANOPS_IG_SCRAPE_PASSWORD` match `_is_secret` masking on the registered field names.

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/secret_provider.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/config_introspect.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/autopilot.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:scripts/scan-secrets.sh
rg -n 'FANOPS_IG_SCRAPE|POSTIZ_API_KEY|ZERNIO_API_KEY|write-only' docs/CONFIG.md
rg -l 'type="password"' src/fanops/studio/templates
rg 'FANOPS_IG_SCRAPE|scrape.password' src/fanops/studio/static src/fanops/studio/templates -i
PYTHONPATH=src python3 -c "from fanops.config_introspect import _is_secret; from fanops.settings import Settings; creds=[n for n in Settings.model_fields if any(x in n for x in ('KEY','SECRET','TOKEN','PASSWORD'))]; print([n for n in creds if not _is_secret(n)])"
rg -n 'SEC-IGPW|SEC-ENV|masks_ig_scrape|lock_busy' tests/test_config_verb.py tests/test_autopilot.py tests/test_secret_provider.py
git diff origin/main -- src
```
