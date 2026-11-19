# Drill: stop one app instance (Compose stage)

**Date:** 2026-11-01 · **Stage:** 2 · **Script:** `scripts/drill-stop-instance.ps1`

## Setup

- Docker Desktop 29.6.1, WSL 2, 12 CPUs / 3.9 GB available to WSL.
- `app-1`, `app-2`, `app-3` behind `nginx:1.28-alpine` (round-robin, `max_fails=1`,
  `fail_timeout=10s`, `proxy_connect_timeout 1s`, `proxy_next_upstream error timeout http_502 http_503`).
- 30 sequential `GET /api/v1/info` requests per step.

## Expected vs observed

| Step | Expected | Observed |
| --- | --- | --- |
| Baseline | Requests on all three instances, not necessarily equal | 9 / 11 / 10 |
| `docker compose stop app-2` | No failed requests; traffic on app-1 and app-3 | 30 / 30 succeeded: app-1 16, app-3 14 |
| First request to the stopped instance | One slow request while NGINX discovers the failure | One request took 1.005 s: `upstream_status=504, 200` (timeout on app-2, retried on app-3) |
| `docker compose start app-2` | app-2 rejoins without restarting NGINX | 10 / 10 / 10 within 12 s |
| All three instances stopped | Controlled error, not a hang | `503 {"error":"no backend available","request_id":…}` |

## What this shows

- Passive health checks: NGINX only learns an instance is down by failing a real
  request, then skips it for `fail_timeout`. That first request pays the connect timeout.
- `resolve` on the upstream servers lets NGINX follow containers that restart with new IPs.
- Kubernetes will behave differently: readiness probes remove a pod from the
  EndpointSlice *before* traffic hits it (lab 3 and 5).

## Follow-ups

- Repeat with the `/api/v1/slow?ms=200` endpoint and 3 in flight to see how in-flight
  requests on the stopped instance behave.
- Compare with the same drill on kind (delete a pod) in stage 4.
