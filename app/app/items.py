"""Item endpoints, instrumented step by step.

Every request records a trace of what it did (auth, validation, cache, database)
with real timings. A client that sends `X-Trace: 1` gets the trace back next to
the result; the dashboard replays it as an animation.

Scenario headers (honoured only when SIMULATION_ENABLED=true, never in production):
  X-Sim-Cache: auto | force-hit | force-miss
  X-Sim-DB-Error: 1          the database step fails as if the connection was refused
  X-Sim-Network-Ms: N        adds N ms of slow-network delay, half in, half out
"""

import asyncio
import hmac
import json
import os
import socket
from dataclasses import asdict, dataclass

from fastapi import APIRouter, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app import cache, db
from app.trace import Trace

APP_VERSION = os.getenv("APP_VERSION", "dev")
INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()
API_TOKEN = os.getenv("API_TOKEN")
SIMULATION_ENABLED = os.getenv("SIMULATION_ENABLED", "false").lower() == "true"
CACHE_TTL_S = 60
MAX_SIM_NETWORK_MS = 5000

router = APIRouter(prefix="/api/v1/items")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@dataclass
class Scenario:
    cache: str = "auto"
    db_error: bool = False
    network_ms: int = 0


def _scenario(request: Request) -> Scenario:
    if not SIMULATION_ENABLED:
        return Scenario()
    h = request.headers
    mode = h.get("x-sim-cache", "auto")
    try:
        network_ms = int(h.get("x-sim-network-ms", "0"))
    except ValueError:
        network_ms = 0
    return Scenario(
        cache=mode if mode in ("auto", "force-hit", "force-miss") else "auto",
        db_error=h.get("x-sim-db-error") == "1",
        network_ms=max(0, min(network_ms, MAX_SIM_NETWORK_MS)),
    )


def _start(request: Request) -> tuple[Trace, Scenario]:
    trace = Trace(request.method, request.url.path, INSTANCE_ID, APP_VERSION)
    sc = _scenario(request)
    trace.scenario = asdict(sc) | {"enabled": SIMULATION_ENABLED}
    step = trace.begin(
        "app",
        "Request received",
        instance=INSTANCE_ID,
        request_id=request.headers.get("x-request-id"),
        forwarded_for=request.headers.get("x-forwarded-for"),
        content_type=request.headers.get("content-type"),
    )
    trace.end(step)
    return trace, sc


async def _slow_network(trace: Trace, sc: Scenario, direction: str) -> None:
    if not sc.network_ms:
        return
    delay = sc.network_ms / 2
    step = trace.begin("network", f"Slow network ({direction})", delay_ms=delay, simulated=True)
    await asyncio.sleep(delay / 1000)
    trace.end(step, "simulated")


def _reply(request: Request, trace: Trace, status: int, body) -> JSONResponse:
    request.state.db_ms = trace.db_ms()
    body = jsonable_encoder(body)
    if request.headers.get("x-trace") == "1":
        return JSONResponse({"result": body, "trace": trace.to_dict(status)}, status)
    return JSONResponse(body, status)


async def _fail(request: Request, trace: Trace, sc: Scenario, status: int, error: str):
    await _slow_network(trace, sc, "outbound")
    return _reply(request, trace, status, {"error": error})


def _authorize(request: Request, trace: Trace, write: bool) -> str | None:
    """Return an error message when the request is not allowed, else None."""
    step = trace.begin("app", "Auth check")
    if not write:
        trace.end(step, "skipped", reason="reads are public; writes need a Bearer token")
        return None
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    if not API_TOKEN:
        trace.end(step, "error", reason="server has no API_TOKEN configured")
        return "auth not configured"
    # Never echo the token itself: only whether one was sent and whether it matched.
    valid = bool(token) and hmac.compare_digest(token, API_TOKEN)
    trace.end(
        step,
        "ok" if valid else "error",
        scheme="Bearer",
        token_present=bool(token),
        result="token valid" if valid else ("token invalid" if token else "no token sent"),
    )
    return None if valid else "unauthorized"


async def _validate(request: Request, trace: Trace) -> tuple[ItemIn | None, dict | None]:
    step = trace.begin("app", "Validate payload")
    try:
        raw = await request.json()
        item = ItemIn.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else [{"msg": "invalid JSON"}]
        problems = [e.get("msg") for e in errors]
        trace.end(step, "error", problems=problems)
        return None, {"error": "invalid payload", "problems": problems}
    trace.end(step, payload=item.model_dump())
    return item, None


async def _db(trace: Trace, sc: Scenario, name: str, sql: str, params: tuple, call):
    """Run one database call as a traced step. Returns (result, error message)."""
    step = trace.begin("db", name, sql=sql, params=list(params))
    if sc.db_error:
        trace.end(step, "error", error="connection refused (simulated)", simulated=True)
        return None, "database unavailable"
    try:
        result = await call()
    except db.DatabaseUnavailable as exc:
        trace.end(step, "error", error=str(exc)[:200])
        return None, "database unavailable"
    rows = len(result) if isinstance(result, list) else (0 if result in (None, False) else 1)
    trace.end(step, rows=rows)
    return result, None


async def _cache_set(trace: Trace, key: str, item: dict, name: str) -> None:
    step = trace.begin("cache", name, key=key, ttl_s=CACHE_TTL_S)
    try:
        await cache.set(key, json.dumps(jsonable_encoder(item)), CACHE_TTL_S)
        trace.end(step)
    except cache.CacheUnavailable as exc:
        trace.end(step, "error", error=str(exc)[:200], effect="not cached; request continues")


async def _cache_delete(trace: Trace, key: str, name: str) -> None:
    step = trace.begin("cache", name, key=key)
    try:
        removed = await cache.delete(key)
        trace.end(step, keys_removed=removed)
    except cache.CacheUnavailable as exc:
        trace.end(step, "error", error=str(exc)[:200], effect="stale entry may remain until TTL")


@router.get("")
async def list_items(request: Request, limit: int = Query(50, ge=1, le=200)):
    trace, _ = _start(request)
    result, error = await _db(
        trace,
        Scenario(),
        "SELECT recent items",
        "SELECT … ORDER BY id DESC LIMIT %s",
        (limit,),
        lambda: db.list_items(limit),
    )
    if error:
        return _reply(request, trace, 503, {"error": error})
    return _reply(request, trace, 200, {"items": result, "instance": INSTANCE_ID})


@router.post("", status_code=201)
async def create_item(request: Request):
    trace, sc = _start(request)
    await _slow_network(trace, sc, "inbound")
    if error := _authorize(request, trace, write=True):
        return await _fail(request, trace, sc, 401 if error == "unauthorized" else 503, error)
    item, problem = await _validate(request, trace)
    if problem:
        await _slow_network(trace, sc, "outbound")
        return _reply(request, trace, 422, problem)
    created, error = await _db(
        trace,
        sc,
        "INSERT item",
        db.SQL_INSERT,
        (item.name, INSTANCE_ID),
        lambda: db.create_item(item.name, INSTANCE_ID),
    )
    if error:
        return await _fail(request, trace, sc, 503, error)
    await _cache_set(trace, f"item:{created['id']}", created, "Cache write (write-through)")
    await _slow_network(trace, sc, "outbound")
    return _reply(request, trace, 201, created)


@router.get("/{item_id}")
async def get_item(request: Request, item_id: int = Path(ge=1)):
    trace, sc = _start(request)
    await _slow_network(trace, sc, "inbound")
    _authorize(request, trace, write=False)
    key = f"item:{item_id}"

    if sc.cache == "force-miss":
        await _cache_delete(trace, key, "Evict key (scenario: force miss)")
    elif sc.cache == "force-hit":
        await _warm(trace, key, item_id)

    step = trace.begin("cache", "Cache lookup", key=key)
    try:
        cached = await cache.get(key)
        trace.end(step, "hit" if cached else "miss")
    except cache.CacheUnavailable as exc:
        cached = None
        trace.end(step, "error", error=str(exc)[:200], effect="falling back to the database")
    if cached:
        await _slow_network(trace, sc, "outbound")
        return _reply(request, trace, 200, {"item": json.loads(cached), "source": "cache"})

    found, error = await _db(
        trace, sc, "SELECT item by id", db.SQL_SELECT, (item_id,), lambda: db.get_item(item_id)
    )
    if error:
        return await _fail(request, trace, sc, 503, error)
    if found is None:
        return await _fail(request, trace, sc, 404, "item not found")
    await _cache_set(trace, key, found, "Cache fill (after miss)")
    await _slow_network(trace, sc, "outbound")
    return _reply(request, trace, 200, {"item": found, "source": "database"})


async def _warm(trace: Trace, key: str, item_id: int) -> None:
    """Scenario setup for force-hit: make sure the key is cached before the lookup."""
    step = trace.begin("cache", "Warm cache (scenario: force hit)", key=key, setup=True)
    try:
        if await cache.get(key):
            trace.end(step, already_cached=True)
            return
        found = await db.get_item(item_id)
        if found is not None:
            await cache.set(key, json.dumps(jsonable_encoder(found)), CACHE_TTL_S)
        trace.end(step, already_cached=False, loaded_from_db=found is not None)
    except (cache.CacheUnavailable, db.DatabaseUnavailable) as exc:
        trace.end(step, "error", error=str(exc)[:200])


@router.put("/{item_id}")
async def update_item(request: Request, item_id: int = Path(ge=1)):
    trace, sc = _start(request)
    await _slow_network(trace, sc, "inbound")
    if error := _authorize(request, trace, write=True):
        return await _fail(request, trace, sc, 401 if error == "unauthorized" else 503, error)
    item, problem = await _validate(request, trace)
    if problem:
        await _slow_network(trace, sc, "outbound")
        return _reply(request, trace, 422, problem)
    updated, error = await _db(
        trace,
        sc,
        "UPDATE item",
        db.SQL_UPDATE,
        (item.name, item_id),
        lambda: db.update_item(item_id, item.name),
    )
    if error:
        return await _fail(request, trace, sc, 503, error)
    if updated is None:
        return await _fail(request, trace, sc, 404, "item not found")
    await _cache_delete(trace, f"item:{item_id}", "Invalidate cache")
    await _slow_network(trace, sc, "outbound")
    return _reply(request, trace, 200, updated)


@router.delete("/{item_id}")
async def delete_item(request: Request, item_id: int = Path(ge=1)):
    trace, sc = _start(request)
    await _slow_network(trace, sc, "inbound")
    if error := _authorize(request, trace, write=True):
        return await _fail(request, trace, sc, 401 if error == "unauthorized" else 503, error)
    deleted, error = await _db(
        trace, sc, "DELETE item", db.SQL_DELETE, (item_id,), lambda: db.delete_item(item_id)
    )
    if error:
        return await _fail(request, trace, sc, 503, error)
    if not deleted:
        return await _fail(request, trace, sc, 404, "item not found")
    await _cache_delete(trace, f"item:{item_id}", "Invalidate cache")
    await _slow_network(trace, sc, "outbound")
    return _reply(request, trace, 200, {"deleted": item_id})
