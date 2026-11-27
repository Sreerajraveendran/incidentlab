"""IncidentLab sample application.

The service the platform hosts and load-balances. Every response carries the
instance that served it, so traffic distribution can be observed instead of
guessed.
"""

import asyncio
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app import cache, db, items

APP_VERSION = os.getenv("APP_VERSION", "dev")
GIT_SHA = os.getenv("GIT_SHA", "unknown")
# In Kubernetes the pod name arrives through the downward API; in Compose the
# container hostname is set per service.
INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()
# Off by default: a readiness probe that depends on the database makes every
# replica unready at once when the database blips. Turn on only for that drill.
READINESS_CHECKS_DB = os.getenv("READINESS_CHECKS_DB", "false").lower() == "true"

MAX_CPU_MS = 2000
MAX_SLOW_MS = 10000

REQUESTS = Counter("http_requests_total", "HTTP requests handled", ["method", "path", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

state = {"ready": False}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.open_pool()
    cache.init()
    state["ready"] = True
    yield
    # Report unready first so the load balancer stops routing here, then close.
    state["ready"] = False
    await cache.close()
    await db.close_pool()


app = FastAPI(title="IncidentLab sample app", version=APP_VERSION, lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start

    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    if path != "/metrics":
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        LATENCY.labels(request.method, path).observe(elapsed)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Pod-Name"] = INSTANCE_ID
    response.headers["X-App-Version"] = APP_VERSION
    db_ms = getattr(request.state, "db_ms", None)
    timing = f"app;dur={elapsed * 1000:.1f}"
    if db_ms is not None:
        timing += f", db;dur={db_ms:.1f}"
    response.headers["Server-Timing"] = timing
    return response


@app.get("/health/live")
async def live():
    return {"status": "alive", "instance": INSTANCE_ID}


@app.get("/health/ready")
async def ready():
    if not state["ready"]:
        return JSONResponse({"status": "not ready", "instance": INSTANCE_ID}, 503)
    if READINESS_CHECKS_DB and not await db.ping():
        return JSONResponse({"status": "database unreachable", "instance": INSTANCE_ID}, 503)
    return {"status": "ready", "instance": INSTANCE_ID}


@app.get("/api/v1/info")
async def info():
    return {
        "instance": INSTANCE_ID,
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "time": time.time(),
    }


app.include_router(items.router)


@app.get("/api/v1/cpu")
def burn_cpu(ms: int = Query(100, ge=1, le=MAX_CPU_MS)):
    """Busy-loop for `ms` milliseconds so the HPA has real CPU to react to."""
    deadline = time.perf_counter() + ms / 1000
    iterations = 0
    while time.perf_counter() < deadline:
        iterations += 1
    return {"instance": INSTANCE_ID, "burned_ms": ms, "iterations": iterations}


@app.get("/api/v1/slow")
async def slow(ms: int = Query(500, ge=1, le=MAX_SLOW_MS)):
    """Wait without using CPU: an I/O-bound endpoint that scales with replicas."""
    await asyncio.sleep(ms / 1000)
    return {"instance": INSTANCE_ID, "waited_ms": ms}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
