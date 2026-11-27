"""IncidentLab Lab API.

The only component with Kubernetes permissions (see deploy/k8s/base/lab-api.yaml):
read the target namespace, scale the load generator, and, for incident
scenarios, change exactly the objects the scenarios break plus pause/resume
Argo CD auto-sync. The browser never talks to Kubernetes directly.
"""

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import cluster, scenarios
from app.engine import TICK_SECONDS, Engine, EngineError

API_TOKEN = os.getenv("API_TOKEN")
MAX_LOAD_LEVEL = len(cluster.LEVEL_PODS) - 1

log = logging.getLogger("lab-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Loaded at import: an invalid scenario file stops the API from starting.
SCENARIOS = scenarios.load_all()
engine: Engine | None = None


def load_config() -> bool:
    try:
        from kubernetes import config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()  # running outside the cluster, e.g. in development
        return True
    except Exception as exc:  # the API still starts; cluster calls return 503
        log.warning("no Kubernetes config: %s", exc)
        return False


def start_engine() -> Engine | None:
    from app.actions import KubePort
    from app.store import ConfigMapStore

    try:
        return Engine(SCENARIOS, ConfigMapStore(), KubePort())
    except Exception as exc:
        log.warning("scenario engine unavailable: %s", exc)
        return None


async def tick_forever() -> None:
    while True:
        if engine is not None:
            try:
                await asyncio.to_thread(engine.tick)
            except Exception as exc:  # keep ticking; the next tick retries
                log.warning("engine tick failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    cluster_ok = load_config()
    # SCENARIO_ENGINE=off: tests set it, so they never act on a real cluster.
    if cluster_ok and engine is None and os.getenv("SCENARIO_ENGINE", "on") == "on":
        engine = await asyncio.to_thread(start_engine)
    task = asyncio.create_task(tick_forever())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="IncidentLab Lab API", lifespan=lifespan)


def authorized(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(API_TOKEN and token and hmac.compare_digest(token, API_TOKEN))


UNAUTHORIZED = JSONResponse({"error": "unauthorized"}, 401)


def audit(request: Request, action: str, **fields) -> None:
    """Who/what/result, never the token."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    log.info("audit action=%s %s result=ok client=%s", action, extra, request.client)


@app.get("/api/lab/health")
async def health():
    return {"status": "ok"}


@app.get("/api/lab/cluster")
async def snapshot():
    try:
        return await asyncio.to_thread(cluster.fetch_snapshot)
    except Exception as exc:
        log.warning("snapshot failed: %s", exc)
        return JSONResponse({"error": "cluster unavailable", "detail": str(exc)[:300]}, 503)


class LoadRequest(BaseModel):
    level: int = Field(ge=0, le=MAX_LOAD_LEVEL)


@app.post("/api/lab/load")
async def set_load(request: Request, body: LoadRequest):
    if not authorized(request):
        return UNAUTHORIZED
    try:
        await asyncio.to_thread(cluster.set_load, body.level)
    except Exception as exc:
        log.warning("set_load failed: %s", exc)
        return JSONResponse({"error": "could not change load", "detail": str(exc)[:300]}, 503)
    audit(request, "set_load", level=body.level)
    return {"load_level": body.level}


# ---- incident scenarios -------------------------------------------------------
@app.get("/api/lab/scenarios")
async def list_scenarios():
    return {"scenarios": [s.public() for s in SCENARIOS.values()]}


async def run_engine(fn, *args):
    """Call an engine method in a thread and map its errors to HTTP responses."""
    if engine is None:
        return JSONResponse({"error": "scenario engine unavailable"}, 503)
    try:
        return await asyncio.to_thread(fn, *args)
    except EngineError as exc:
        return JSONResponse({"error": str(exc)}, exc.status)
    except Exception as exc:  # Kubernetes API errors while saving or injecting
        log.warning("engine call failed: %s", exc)
        return JSONResponse({"error": "engine call failed", "detail": str(exc)[:300]}, 503)


@app.get("/api/lab/runs")
async def list_runs():
    return await run_engine(lambda: engine.list_runs())


@app.get("/api/lab/runs/{run_id}")
async def get_run(run_id: str):
    return await run_engine(lambda: engine.get_run(run_id))


class StartRequest(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=64)


@app.post("/api/lab/runs")
async def start_run(request: Request, body: StartRequest):
    if not authorized(request):
        return UNAUTHORIZED
    result = await run_engine(lambda: engine.start(body.scenario_id))
    if isinstance(result, dict):
        audit(request, "start_run", scenario=body.scenario_id, run=result["id"])
        return JSONResponse(result, 201)
    return result


@app.post("/api/lab/runs/{run_id}/hint")
async def use_hint(request: Request, run_id: str):
    if not authorized(request):
        return UNAUTHORIZED
    return await run_engine(lambda: engine.hint(run_id))


@app.post("/api/lab/runs/{run_id}/abort")
async def abort_run(request: Request, run_id: str):
    if not authorized(request):
        return UNAUTHORIZED
    result = await run_engine(lambda: engine.abort(run_id))
    if isinstance(result, dict):
        audit(request, "abort_run", run=run_id)
    return result


class AnswerRequest(BaseModel):
    choice: int = Field(ge=0, le=20)


@app.post("/api/lab/runs/{run_id}/root-cause")
async def answer_root_cause(request: Request, run_id: str, body: AnswerRequest):
    if not authorized(request):
        return UNAUTHORIZED
    return await run_engine(lambda: engine.answer(run_id, body.choice))
