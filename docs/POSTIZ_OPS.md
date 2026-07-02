# Postiz operations — the `mastra_ai_spans` crash-loop and other honest-external-state notes

Operator runbook for the failure mode where the self-hosted Postiz container reports **healthy** to
Docker while its Node backend is dead, so FanOps publishes stall in `queued` and every `fanops` publish
502s. FanOps cannot fix Postiz from the outside (the failing migration runs *inside* Postiz), but it can
be **honest** about the state and hand you the exact workaround.

**Valid as of: 2026-07-02.** This documents a real incident + the manual fix that worked. Track the
upstream fix below; when Postiz ships it, this whole page becomes obsolete.

Companion surfaces in FanOps:
- The Studio **system strip** shows a **Postiz API unhealthy** banner when the backend probe fails AND a
  channel routes to postiz (the probe goes *past* the nginx-only container health — see §3).
- `fanops doctor` surfaces poster/key readiness.

---

## 1. Symptom

- Every publish via Postiz stalls: posts sit in `queued`, never reach `submitted`, and the operator sees
  502s. The Studio Postiz-down banner appears (`Postiz API unhealthy (status: 502)`).
- `docker ps` still shows the `postiz` container as **healthy** — this is the trap (§3).
- The backend log carries the migration failure:

```bash
docker logs postiz 2>&1 | grep MASTRA_STORAGE_PG_ALTER_TABLE_FAILED
```

If that grep hits, you are in this failure mode. The Mastra AI telemetry table `mastra_ai_spans` keeps
gaining columns until it crosses Postgres's hard **1600-column** per-table limit; the next Postiz
`ALTER TABLE` migration on boot then fails, and the Node backend crash-loops instead of serving the API.

---

## 2. Diagnosis

- **Root cause is inside Postiz, not FanOps.** Postiz embeds Mastra (its AI layer). Mastra writes a
  telemetry span table `mastra_ai_spans` and periodically `ALTER TABLE`s it to add columns. Postgres caps
  a table at **1600 columns**; once `mastra_ai_spans` reaches it, the boot-time migration `ALTER TABLE`
  throws `MASTRA_STORAGE_PG_ALTER_TABLE_FAILED` and the backend never comes up.
- **The container's health status lies** — see §3. Do not trust `docker ps` "healthy" here.
- FanOps' own posting layer is correct: on a 502 it parks the post `needs_reconcile` (never re-POSTs a
  possibly-live body), so nothing double-posts. The stall is entirely Postiz-side.

---

## 3. Why the container's "healthy" status lies (nginx-only)

The Postiz image's Docker `HEALTHCHECK` probes the front **nginx**, not the Node API behind it. nginx
stays up and answers while the Node backend crash-loops, so Docker reports the container **healthy** even
though the API returns 502 to every real request. This is why FanOps does **not** trust the container
status: `postiz_health_probe(cfg)` (in `src/fanops/post/postiz.py`) exercises the real
`GET /public/v1/integrations` backend endpoint. A 502 there means "up at the proxy, dead at the app" — the
honest signal the Studio banner and this runbook are built on.

---

## 4. Workaround

Drop the offending telemetry table and restart Postiz. `mastra_ai_spans` is **AI observability data** —
dropping it loses only Mastra's own span history, not any FanOps content, schedule, or account mapping.

```bash
# 1. Confirm you are in this failure mode
docker logs postiz 2>&1 | grep MASTRA_STORAGE_PG_ALTER_TABLE_FAILED

# 2. Drop the over-wide telemetry table (adjust the psql connection to your Postiz Postgres).
#    Example if Postgres runs in a sibling container named postiz-postgres:
docker exec -i postiz-postgres psql -U postiz -d postiz -c 'DROP TABLE mastra_ai_spans CASCADE;'

# 3. Restart Postiz so the boot migration re-runs cleanly (it recreates the table fresh, empty)
docker restart postiz

# 4. Verify the backend is actually serving (past nginx), not just "healthy"
docker logs --tail 50 postiz 2>&1 | grep -i 'listening\|ready\|started'
```

After the restart, the Studio Postiz-down banner self-clears within ~30s (the probe is cached that long),
FanOps' reconcile picks up the parked posts, and publishing resumes.

> Adjust the Postgres connection (container name, `-U` user, `-d` database) to your deployment. If Postgres
> is not a separate container, run the `DROP TABLE mastra_ai_spans CASCADE;` against your Postiz database
> however you normally reach it (`psql`, a managed console, etc.).

---

## 5. When to use this

Use §4 **only** when **all** of these hold:

- Publishes are stalling / 502-ing AND the Studio shows the Postiz-down banner, and
- `docker logs postiz | grep MASTRA_STORAGE_PG_ALTER_TABLE_FAILED` hits.

Do **not** run `DROP TABLE` speculatively or on a schedule. It is a manual recovery for a confirmed
`mastra_ai_spans` column-limit crash — not a routine maintenance step. (An automated Studio button that
touches Postiz's own database was deliberately **not** built — auto-dropping another product's table on a
health signal is a bad default.)

If the grep does **not** hit but publishes still fail, this is a *different* problem — check
`docker logs postiz` for the real error, POSTIZ_URL/POSTIZ_API_KEY, and network reachability, and consult
[`POSTIZ_SETUP.md`](POSTIZ_SETUP.md).

---

## 6. Permanent fix (upstream)

The real fix is upstream in Postiz/Mastra: cap or rotate the `mastra_ai_spans` table so it never
approaches the 1600-column limit, or disable Mastra AI-span telemetry. Until Postiz ships that, §4 is the
recurring manual recovery. **Track the upstream Mastra issue and upgrade Postiz** when a release addresses
the span-table growth; on that upgrade, retire this runbook.
