"""Load generator: sends RATE requests per second to TARGET_URL.

Runs as the minicloud-loadgen Deployment; the dashboard sets its replica count
to choose a load level, so total load = replicas x RATE.

Open model: requests start on a fixed schedule whether or not earlier ones have
finished, like real users. (Keeping N requests in flight instead would make even
one pod saturate every replica, and the HPA could never scale down.)

    python -m app.loadgen
"""

import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TARGET_URL = os.getenv("TARGET_URL", "http://minicloud-gateway/api/v1/cpu?ms=50")
RATE = float(os.getenv("RATE", "2"))  # requests per second from this pod
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "20"))
REPORT_EVERY_S = 10

lock = threading.Lock()
counts = {"ok": 0, "failed": 0, "skipped": 0}
in_flight = 0


def one_request() -> None:
    global in_flight
    try:
        with urllib.request.urlopen(TARGET_URL, timeout=10) as r:
            r.read()
        key = "ok"
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        key = "failed"
    with lock:
        counts[key] += 1
        in_flight -= 1


def main() -> None:
    global in_flight
    print(f"loadgen: {RATE} req/s -> {TARGET_URL}", flush=True)
    pool = ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT)
    interval = 1 / RATE
    next_at = time.monotonic()
    last_report = next_at
    while True:
        with lock:
            # When the target is too slow to keep up, skip rather than pile up threads.
            if in_flight >= MAX_IN_FLIGHT:
                counts["skipped"] += 1
                start = False
            else:
                in_flight += 1
                start = True
        if start:
            pool.submit(one_request)
        next_at += interval
        time.sleep(max(0.0, next_at - time.monotonic()))

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


if __name__ == "__main__":
    main()
