"""Load generator: sends RATE requests per second to TARGET_URL.

Runs as the incidentlab-loadgen Deployment; the dashboard sets its replica count
to choose a load level, so total load = replicas x RATE.

Open model: requests start on a fixed schedule whether or not earlier ones have
finished, like real users. (Keeping N requests in flight instead would make even
one pod saturate every replica, and the HPA could never scale down.)

    python -m app.loadgen
"""

import os
import signal
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TARGET = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local/api/v1/cpu?ms=50"
REPORT_EVERY_S = 10

lock = threading.Lock()
counts = {"ok": 0, "failed": 0, "skipped": 0}
in_flight = 0
stopping = threading.Event()


def read_config() -> tuple[str, float, int]:
    target = os.getenv("TARGET_URL", DEFAULT_TARGET)
    rate = float(os.getenv("RATE", "2"))  # requests per second from this pod
    max_in_flight = int(os.getenv("MAX_IN_FLIGHT", "20"))
    if rate <= 0:
        raise SystemExit(f"loadgen: RATE must be > 0, got {rate}")
    if max_in_flight < 1:
        raise SystemExit(f"loadgen: MAX_IN_FLIGHT must be >= 1, got {max_in_flight}")
    return target, rate, max_in_flight


def one_request(url: str) -> None:
    global in_flight
    key = "failed"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            r.read()
        key = "ok"
    except Exception:
        # Any failure (refused, timeout, HTTP error, truncated body) counts as
        # failed. Catching everything matters: an escaped exception would skip
        # the release below and the in-flight slot would leak for good.
        pass
    finally:
        with lock:
            counts[key] += 1
            in_flight -= 1


def try_acquire(max_in_flight: int) -> bool:
    """Reserve an in-flight slot; when the target can't keep up, skip instead."""
    global in_flight
    with lock:
        if in_flight >= max_in_flight:
            counts["skipped"] += 1
            return False
        in_flight += 1
        return True


def main() -> None:
    target, rate, max_in_flight = read_config()
    # This process is PID 1 in its container, and PID 1 ignores SIGTERM unless
    # it installs a handler: without this, "Off" kept load running until the
    # pod was killed.
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    print(f"loadgen: {rate} req/s -> {target}", flush=True)
    pool = ThreadPoolExecutor(max_workers=max_in_flight)
    interval = 1 / rate
    next_at = time.monotonic()
    last_report = next_at
    while not stopping.is_set():
        if try_acquire(max_in_flight):
            pool.submit(one_request, target)
        next_at += interval
        stopping.wait(max(0.0, next_at - time.monotonic()))

        now = time.monotonic()
        if now - last_report >= REPORT_EVERY_S:
            with lock:
                ok, failed, skipped = counts["ok"], counts["failed"], counts["skipped"]
                counts.update(ok=0, failed=0, skipped=0)
            print(
                f"loadgen: last {REPORT_EVERY_S}s ok={ok} failed={failed} skipped={skipped}",
                flush=True,
            )
            last_report = now

    print("loadgen: stopping", flush=True)
    pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
