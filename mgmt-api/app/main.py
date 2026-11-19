"""MiniCloud management API.

The only component with Kubernetes permissions, and only these: read pods,
events, the HPA and pod metrics in one namespace, read nodes, and scale the
load-generator Deployment. The browser never talks to Kubernetes directly.
"""

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import cluster

API_TOKEN = os.getenv("API_TOKEN")
MAX_LOAD_LEVEL = len(cluster.LEVEL_PODS) - 1

log = logging.getLogger("mgmt-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_config() -> None:
    try:
        from kubernetes import config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()  # running outside the cluster, e.g. in development
    except Exception as exc:  # the API still starts; cluster calls return 503
        log.warning("no Kubernetes config: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_config()
    yield


app = FastAPI(title="MiniCloud management API", lifespan=lifespan)


@app.get("/api/mgmt/health")
async def health():
    return {"status": "ok"}


@app.get("/api/mgmt/cluster")
async def snapshot():
    try:
        return await asyncio.to_thread(cluster.fetch_snapshot)
    except Exception as exc:
        log.warning("snapshot failed: %s", exc)
        return JSONResponse({"error": "cluster unavailable", "detail": str(exc)[:300]}, 503)


class LoadRequest(BaseModel):
    level: int = Field(ge=0, le=MAX_LOAD_LEVEL)


@app.post("/api/mgmt/load")
async def set_load(request: Request, body: LoadRequest):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    if not API_TOKEN or not token or not hmac.compare_digest(token, API_TOKEN):
        return JSONResponse({"error": "unauthorized"}, 401)
    try:
        await asyncio.to_thread(cluster.set_load, body.level)
    except Exception as exc:
        log.warning("set_load failed: %s", exc)
        return JSONResponse({"error": "could not change load", "detail": str(exc)[:300]}, 503)
    # Audit line: who/what/result, never the token.
    log.info("audit action=set_load level=%s result=ok client=%s", body.level, request.client)
    return {"load_level": body.level}
