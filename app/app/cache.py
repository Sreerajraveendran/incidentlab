"""Redis cache for single items (cache-aside).

Short timeouts on purpose: when Redis is down the app skips the cache and
serves from the database instead of hanging.
"""

import os

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL")

_client: redis.Redis | None = None


class CacheUnavailable(Exception):
    pass


def init() -> None:
    global _client
    if REDIS_URL:
        _client = redis.from_url(
            REDIS_URL,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
            decode_responses=True,
        )


async def close() -> None:
    if _client is not None:
        await _client.aclose()


def _require() -> redis.Redis:
    if _client is None:
        raise CacheUnavailable("REDIS_URL is not set")
    return _client


async def get(key: str) -> str | None:
    try:
        return await _require().get(key)
    except CacheUnavailable:
        raise
    except Exception as exc:
        raise CacheUnavailable(str(exc)) from exc


async def set(key: str, value: str, ttl_s: int) -> None:
    try:
        await _require().set(key, value, ex=ttl_s)
    except CacheUnavailable:
        raise
    except Exception as exc:
        raise CacheUnavailable(str(exc)) from exc


async def delete(key: str) -> int:
    try:
        return await _require().delete(key)
    except CacheUnavailable:
        raise
    except Exception as exc:
        raise CacheUnavailable(str(exc)) from exc
