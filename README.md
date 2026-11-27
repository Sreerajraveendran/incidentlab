# IncidentLab

**A flight simulator for on-call engineers.** IncidentLab runs a small but real
web system on a local Kubernetes cluster, breaks it on purpose (a bad deploy, a
database outage, a traffic spike), and lets you find and fix the problem with
the same tools you'd use at work: `kubectl`, logs, metrics, Argo CD. The lab
then checks that the system really recovered, scores the run and keeps a
postmortem.

Everything runs on one Windows laptop (WSL 2 + Docker Desktop, 8 GB RAM);
nothing here costs money. Formerly **MiniCloud**: the MiniCloud app is now the
*target app*, the system that gets broken.

How it is built: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Design and roadmap: [IncidentLab case study](https://claude.ai/artifact/Xzz79Pa1g7zyx7RnqY16wS).

## Status

What exists today (the foundation):

| Area | Scope | State |
| --- | --- | --- |
| Docker | multi-stage images, non-root, healthchecks, signals | Done |
| Compose | NGINX load balancing, Redis cache, Postgres, request-flow visualizer, traffic-split view, failure drill | Done |
| CI | lint, tests, Compose smoke test + drill, kubeconform, image build, Trivy scan, GHCR push | Done |
| Kubernetes | kind cluster, probes, resources, HPA + metrics-server, ingress-nginx, Argo CD (GitOps), Lab API with RBAC, load generator, autoscaling visualizer | Done |

Roadmap:

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Stability fixes, rename MiniCloud → IncidentLab | Done |
| M1 | Scenario engine in the Lab API: inject → detect → fix → auto-verify; first 3 scenarios (bad deploy, DB down, traffic spike); timeline UI; root-cause declaration | Done |
| M2 | Prometheus + Grafana, alerts as the way incidents are detected; more scenarios | Planned |
| Later | Async orders on Kafka (Redpanda) with KEDA scaling on consumer lag; more microservices (orders, inventory, payment, notification); tracing | Planned |

## Layout

```
app/              target app: FastAPI service (load-balanced; traces every request step)
web/              React dashboard (request flow, autoscaling, traffic split)
lab-api/          Lab API: the only component with Kubernetes access (grows into the scenario engine)
deploy/compose/   Stage A: compose.yaml, NGINX config, Postgres init, Redis
deploy/k8s/       Stage B: kind config, Kustomize manifests, vendored metrics-server,
                  ingress-nginx and Argo CD
scripts/          cluster setup, traffic and drill helpers (PowerShell)
docs/             architecture, drill records and exercise log
.github/          CI workflow and Dependabot
```

## Run it

Two stages, never at the same time on an 8 GB machine:

| Stage | Start | Dashboard |
| --- | --- | --- |
| A · Docker Compose | `docker compose up -d --build --wait` in `deploy\compose` | http://127.0.0.1:8088 |
| B · Kubernetes (kind) | `.\scripts\k8s-up.ps1` (stops Compose first) | http://127.0.0.1:8090 |

Prerequisites: Docker Desktop (WSL 2 backend) and kubectl. kind is downloaded
to `.tools\kind.exe` (see below). Python 3.13 and Node 22 only for running
tests outside containers.

### Stage B: autoscaling on Kubernetes

```powershell
# once: kind v0.29.0, checksum-verified, kept out of Git
curl.exe -Lo .tools\kind.exe https://kind.sigs.k8s.io/dl/v0.29.0/kind-windows-amd64
copy deploy\k8s\base\secrets.env.example deploy\k8s\base\secrets.env   # change the values
.\scripts\k8s-up.ps1            # create cluster, build + load images, deploy, wait
.\scripts\k8s-down.ps1          # delete the cluster (asks first; deletes the Postgres volume)
```

`k8s-up.ps1` installs metrics-server, ingress-nginx and Argo CD, then hands the
app to Argo CD, which deploys `deploy/k8s/base` from the `main` branch on
GitHub. So a manifest change reaches the cluster when it is merged, not when it
is saved locally. It is safe to re-run, also after Docker Desktop restarts: it
starts stopped nodes and repairs the node network if the restart broke it.
Argo CD's UI is at http://argocd.localhost:8090 (user `admin`; the script prints
how to get the password).

### Run an incident

Open the **Incidents** tab, paste the API token (`API_TOKEN` in
`deploy\k8s\base\secrets.env`) once, and start a scenario. Then work in a
terminal like on call:

```powershell
kubectl -n incidentlab get pods          # what is not Running?
kubectl -n incidentlab describe pod <name>
kubectl -n incidentlab get events --sort-by=.lastTimestamp
kubectl -n incidentlab logs deploy/incidentlab-app --tail=30
```

While the incident runs, Argo CD auto-sync is paused (otherwise it would undo
the fault, and your fix). The tab shows the recovery checks, which the lab
evaluates every 5 s against the real cluster; when all pass for 15 s the
incident is resolved. Then name the root cause for 20 extra points. Hints cost
10 points each. Giving up, or the 15-minute limit, resumes Argo CD, which
restores everything from Git.

| Scenario | What you see |
| --- | --- |
| The release that never finished | A deploy that never completes; the site still works |
| Items won't load | The item API answers 503, the pods look healthy |
| Slow under load | CPU high, but the pod count doesn't grow |

### Watch autoscaling

Open the **Autoscaling** tab and pick a load level. What you watch:

1. Load-generator pods send CPU-heavy requests through ingress-nginx at a fixed
   rate: Light 4, Medium 6, Heavy 12 requests/s (about 3, 4 and 6 app pods).
   Switching from a higher level to a lower one scales back down.
2. metrics-server reports pod CPU; the HPA (target 50% of the 100m request,
   2–6 pods) computes `desired = ceil(current × CPU ÷ target)`; the panel shows
   the formula with live numbers and which step of the loop is running.
3. New pods appear on the worker node: Pending → ContainerCreating → Running →
   Ready, and only Ready pods get traffic (ingress-nginx follows the Service's
   ready endpoints).
4. Turn load off: CPU drops, a 60 s stabilization window counts down (a demo
   setting; the default is 300 s), then pods turn Terminating and fade out.

The chart keeps the last 5 minutes of CPU and pod count; the event list shows
`SuccessfulRescale`, `Scheduled`, `Pulled`, `Started`, `Killing` as they happen.

### Stage A: Docker Compose

```powershell
cd deploy\compose
copy .env.example .env      # then change the passwords in .env
docker compose up -d --build --wait
```

Open http://127.0.0.1:8088. The port is set by `INCIDENTLAB_PORT` in `.env`
(8080 is often taken on Windows).

### Request flow view (the main screen)

Build a GET / POST / PUT / DELETE request, send it, and watch it travel
Browser → API Gateway (NGINX) → app instance → Cache (Redis) / Database (Postgres) → back.

- **Real, then replayed.** The request really goes through the stack. The app
  records every step (auth check, validation, cache lookup, SQL query, cache
  write) with its measured duration and returns that trace when asked with
  `X-Trace: 1`. The animation replays it; durations in the inspector are measured.
- **Controls:** play / pause, step forward / back, restart, speed 0.25×–4×.
- **Inspector:** each step's details (SQL, cache key, hit/miss, auth result),
  full request and response headers and body, a processing log, and a history of runs.
- **Scenarios** (sent as `X-Sim-*` headers, honoured only while `SIMULATION_ENABLED=true`):
  valid / wrong / no token, force cache hit, force cache miss, database error, slow network.
- Writes need the `API_TOKEN` from `.env`; paste it into the token field once (kept in the browser).

The **Traffic split** tab sends many requests and counts which instance answered each.

```powershell
.\scripts\traffic.ps1 -Count 30                 # requests per instance, from the terminal
.\scripts\drill-stop-instance.ps1 -Instance app-2   # failure drill (asks for confirmation)
docker compose -f deploy\compose\compose.yaml logs nginx   # upstream= shows who served each request
```

### Sample app endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live` | Process is alive (liveness) |
| `GET /health/ready` | Ready for traffic (readiness); does not check the DB unless `READINESS_CHECKS_DB=true` |
| `GET /api/v1/info` | Instance name, version, git SHA |
| `GET /api/v1/items` | Recent rows from Postgres (`app_db`) |
| `POST /api/v1/items` | Create; needs `Authorization: Bearer <API_TOKEN>`; written through to the cache |
| `GET /api/v1/items/{id}` | Cache-aside read: Redis first, Postgres on a miss, then cached for 60 s |
| `PUT` / `DELETE /api/v1/items/{id}` | Update / delete (token needed); invalidates the cache key |
| `GET /api/v1/cpu?ms=N` | Burns CPU for N ms (max 2000), for autoscaling tests |
| `GET /api/v1/slow?ms=N` | Waits N ms without CPU (max 10000) |
| `GET /metrics` | Prometheus metrics |

Every response carries `X-Pod-Name`, `X-App-Version`, `X-Request-ID` and
`Server-Timing`; NGINX adds `X-Upstream`.

## Test

```powershell
cd app;  python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\ruff check .; .\.venv\Scripts\pytest -q
cd ..\lab-api; python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\ruff check .; .\.venv\Scripts\pytest -q
cd ..\web; npm ci; npm run typecheck; npm test; npm run build
```

## Clean up

```powershell
cd deploy\compose
docker compose down        # stop; data in the pgdata volume is kept
docker compose down -v     # stop and delete the database volume
```

## Known limitations

- One machine: three containers are not three servers. A laptop failure takes everything down.
- Open-source NGINX has passive health checks only. The first request to a stopped
  instance waits for the 1 s connect timeout, then is retried on another instance.
- NGINX retries a request on another instance only when an instance can't be
  reached. An app's own 503 (for example, its database is down) is passed
  through: retrying it everywhere would multiply load on what is already failing.
- When Redis is down, reads still work from Postgres but each costs about 1 s
  of cache timeouts (2 × 0.5 s). The request-flow view shows this.
- Postgres is a single instance with no replication. Backups and restore drills come in stage 7.
- The schema is created by the app on first use. Migrations (Alembic, run as a Job) come with Kubernetes.
- Local load tests measure this laptop, not cloud capacity.
- The CI workflow references actions by version tag; pin to commit SHAs once stable.
