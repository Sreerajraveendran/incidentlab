# Docker exercise log

Proof for each Docker topic in the case study (section 3). Fill in the open rows as you do them.

| Topic | What was done | Evidence |
| --- | --- | --- |
| Multi-stage images | `app/Dockerfile`: build venv in one stage, copy only the venv + code into the runtime stage. `web/Dockerfile`: Node build stage, static files served by unprivileged NGINX | `minicloud-app:dev` 268 MB, `minicloud-web:dev` 81.7 MB (`docker image ls`, Docker Desktop disk size) |
| Layer caching | Requirements / `package-lock.json` copied before source, so code edits reuse the dependency layer | Rebuild after a code-only change: dependency step shows `CACHED` |
| Non-root | App runs as uid 10001; web uses `nginx-unprivileged` on port 8080 | `docker run --rm --entrypoint id minicloud-app:dev` → `uid=10001(app)` |
| Signals / PID 1 | Exec-form `CMD`, uvicorn is PID 1, `--timeout-graceful-shutdown 10` | To do: time `docker stop` (graceful) vs a shell-form CMD (killed after 10 s) |
| Healthchecks | Dockerfile `HEALTHCHECK` on `/health/live`; Compose `depends_on: condition: service_healthy` for Postgres and web | `docker compose ps` shows `(healthy)`; apps wait for Postgres |
| Networks | `edge` (nginx, web, apps) and `internal` (apps, postgres; `internal: true`) | `docker compose exec nginx nc -z postgres 5432` → `bad address 'postgres'`: NGINX cannot even resolve the database (2026-11-01) |
| Ports | Only NGINX is published, on `127.0.0.1` | `docker compose ps`: `127.0.0.1:8088->80/tcp`; Postgres shows no host port |
| Volumes | Named volume `pgdata` | Item created, `docker compose down` + `up`, item still there (2026-11-01) |
| Resource limits | `deploy.resources.limits` per service | Whole stack ≈ 176 MB RAM idle (`docker stats`, 2026-11-01) |
| Registry, tags vs digests | CI pushes `ghcr.io/<owner>/minicloud/app:sha-<short>` | To do: deploy by digest in stage 7 |
