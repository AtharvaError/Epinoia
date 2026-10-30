"""Simulates a busy shift against a running API and reports response times.

    python -m scripts.load_test [base_url] [concurrent_users] [rounds]
"""
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import settings

PAGES = ["dashboard", "participants", "participants?q=mehta", "events", "events/conflicts", "accommodation/summary", "accommodation/rooms", "volunteers/understaffed",
         "transport/routes", "finance/summary", "incidents", "analytics", "simulations/options", "audit"]


def session(base: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30)
    client.post("/api/auth/login", json={"email": "admin@festos.test", "password": settings.demo_password}).raise_for_status()
    return client


def worker(base: str, rounds: int) -> tuple[list[tuple[str, float, int]], list[float]]:
    client = session(base)
    fid = client.get("/api/festivals").json()[0]["id"]
    timings, simulations = [], []
    for _ in range(rounds):
        for page in PAGES:
            started = time.perf_counter()
            status = client.get(f"/api/festivals/{fid}/{page}").status_code
            timings.append((page.split("?")[0], (time.perf_counter() - started) * 1000, status))
        started = time.perf_counter()
        client.post(f"/api/festivals/{fid}/simulations", json={"scenario": "bus_delay", "change": {"route_code": "TRIP02", "minutes": 15}}).raise_for_status()
        simulations.append((time.perf_counter() - started) * 1000)
    return timings, simulations


def percentile(values: list[float], share: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * share))]


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    users = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    started = time.perf_counter()
    with ThreadPoolExecutor(users) as pool:
        results = list(pool.map(lambda _: worker(base, rounds), range(users)))
    elapsed = time.perf_counter() - started
    by_page: dict[str, list[float]] = defaultdict(list)
    errors = 0
    for timings, _ in results:
        for page, ms, status in timings:
            by_page[page].append(ms)
            errors += status >= 400
    simulations = [ms for _, runs in results for ms in runs]
    total = sum(len(v) for v in by_page.values()) + len(simulations)
    print(f"{users} users, {total} requests in {elapsed:.1f}s, {total / elapsed:.0f} requests/s, {errors} errors")
    print(f"{'page':28}{'median':>9}{'p95':>9}{'max':>9}")
    for page, values in sorted(by_page.items(), key=lambda item: -statistics.median(item[1])):
        print(f"{page:28}{statistics.median(values):8.0f}ms{percentile(values, .95):7.0f}ms{max(values):7.0f}ms")
    print(f"{'run a simulation':28}{statistics.median(simulations):8.0f}ms{percentile(simulations, .95):7.0f}ms{max(simulations):7.0f}ms")


if __name__ == "__main__":
    main()
