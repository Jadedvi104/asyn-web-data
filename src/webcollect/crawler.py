"""Bounded structured concurrency, host pacing, and durable checkpoints."""

import asyncio
import time
from collections import defaultdict

import httpx

from webcollect.config import CrawlConfig
from webcollect.fetcher import FetchError, FetchResult, fetch_response
from webcollect.parser import parse_html
from webcollect.robots import RobotsPolicy
from webcollect.storage import Store, utcnow
from webcollect.urls import host_for, is_allowed, normalize_url


class HostGate:
    def __init__(self, concurrency: int, rps: float):
        self.concurrency = concurrency
        self.interval = 1.0 / rps
        self.semaphores: dict[str, asyncio.Semaphore] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_start: dict[str, float] = {}
        self.delays: dict[str, float] = defaultdict(float)

    def set_delay(self, url: str, delay: float) -> None:
        host = host_for(url)
        self.delays[host] = max(self.delays[host], delay)

    async def run(self, url: str, operation):
        host = host_for(url)
        semaphore = self.semaphores.setdefault(host, asyncio.Semaphore(self.concurrency))
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with semaphore:
            async with lock:
                interval = max(self.interval, self.delays[host])
                wait = self.last_start.get(host, -float("inf")) + interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.last_start[host] = time.monotonic()
            return await operation()


async def crawl(store: Store, job_id: str, *, transport=None) -> dict:
    """Run or recover a job; caller owns the store, this scope owns HTTP resources.

    A single process must own a database during a crawl. SQLite is the durable backlog;
    only queue_size entries and concurrency workers are resident in the asyncio queue.
    """
    config = CrawlConfig(**store.get_job(job_id)["config"])
    store.begin(job_id)
    started = time.monotonic()
    gate = HostGate(config.per_host_concurrency, config.rps)
    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=config.queue_size)
    changed = asyncio.Event()
    active = 0

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": config.user_agent},
            timeout=httpx.Timeout(config.timeout),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=config.concurrency),
            transport=transport,
        ) as client:

            async def request(url: str, max_bytes: int, *, on_start=None) -> FetchResult:
                if not is_allowed(url, config.allow_domains):
                    raise FetchError("domain_blocked", "URL is outside the allowlist", False)

                async def perform():
                    if on_start is not None:
                        on_start()
                    store.request_started(job_id)
                    async with asyncio.timeout(config.timeout):
                        return await fetch_response(client, url, max_bytes=max_bytes)

                try:
                    result = await gate.run(url, perform)
                except TimeoutError as error:
                    raise FetchError("timeout", "Total request deadline exceeded", True) from error
                store.response_received(
                    job_id, len(result.body), result.latency_seconds, result.status
                )
                return result

            robots = RobotsPolicy(client, user_agent=config.user_agent, fetch=request)

            async def fetch_page(url: str, on_start) -> FetchResult:
                current = url
                seen = set()
                for _ in range(6):
                    if not is_allowed(current, config.allow_domains):
                        raise FetchError("domain_blocked", "Redirect leaves the allowlist", False)
                    decision = await robots.check(current)
                    gate.set_delay(current, decision.crawl_delay)
                    if not decision.allowed:
                        raise FetchError("robots_blocked", decision.reason, False)
                    if current in seen:
                        raise FetchError("redirect_loop", "Redirect loop detected", False)
                    seen.add(current)
                    result = await request(current, config.max_response_bytes, on_start=on_start)
                    if result.status in {301, 302, 303, 307, 308}:
                        location = result.headers.get("location")
                        if not location:
                            raise FetchError("redirect_invalid", "Missing redirect Location", False)
                        try:
                            current = normalize_url(location, current)
                        except ValueError as error:
                            raise FetchError("redirect_invalid", str(error), False) from error
                        continue
                    if result.status >= 400:
                        raise FetchError(
                            "http_status",
                            f"HTTP {result.status}",
                            result.status in {408, 425, 429, 500, 502, 503, 504},
                            result.status,
                        )
                    if result.status < 200 or result.status >= 300:
                        raise FetchError(
                            "http_status", f"Unexpected HTTP {result.status}", False, result.status
                        )
                    return result
                raise FetchError("redirect_limit", "Too many redirects (maximum 5)", False)

            async def process(item: dict) -> None:
                url = item["url_normalized"]
                if item["attempt_count"] >= config.max_attempts:
                    store.failure(
                        job_id,
                        url,
                        code="attempts_exhausted",
                        message="Saved attempt budget is exhausted",
                    )
                    return
                attempt = item["attempt_count"]
                attempt_started = False

                def start_attempt() -> None:
                    nonlocal attempt, attempt_started
                    if not attempt_started:
                        attempt = store.start_attempt(job_id, url)
                        attempt_started = True

                try:
                    response = await fetch_page(url, start_attempt)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise FetchError(
                            "unsupported_content", "Response is not HTML", False, response.status
                        )
                    page = parse_html(response.body, response.url)
                    page["url"] = url
                    page["status"] = response.status
                    page["fetched_at"] = utcnow()
                    links = []
                    if item["depth"] < config.depth:
                        links = [
                            link
                            for link in page["outbound_links"]
                            if is_allowed(link, config.allow_domains)
                        ]
                    store.complete(job_id, url, page, links, item["depth"] + 1, config.max_pages)
                except FetchError as error:
                    retry = error.retryable and attempt < config.max_attempts
                    # Keep ownership during backoff; other workers cannot claim this retry.
                    terminal = (
                        "in_progress"
                        if retry
                        else (
                            "blocked"
                            if error.code in {"domain_blocked", "robots_blocked"}
                            else "failed"
                        )
                    )
                    store.failure(
                        job_id,
                        url,
                        code=error.code,
                        message=str(error),
                        status=error.status,
                        retryable=error.retryable,
                        terminal=terminal,
                    )
                    if retry:
                        await asyncio.sleep(min(0.1 * 2 ** min(attempt - 1, 5), 2.0))
                        store.requeue(job_id, url)

            async def worker() -> None:
                nonlocal active
                while True:
                    item = await queue.get()
                    changed.set()  # Refill freed queue slots while this worker performs I/O.
                    try:
                        if item is None:
                            return
                        await process(item)
                    finally:
                        if item is not None:
                            active -= 1
                        queue.task_done()
                        changed.set()

            async with asyncio.TaskGroup() as tasks:
                for _ in range(config.concurrency):
                    tasks.create_task(worker())
                while True:
                    changed.clear()
                    while not queue.full():
                        item = store.claim(job_id)
                        if item is None:
                            break
                        active += 1
                        queue.put_nowait(item)
                    if active == 0:
                        break
                    await changed.wait()
                for _ in range(config.concurrency):
                    await queue.put(None)
                await queue.join()
    except BaseException:
        store.finish(job_id, "interrupted", time.monotonic() - started)
        raise
    else:
        counts = store.status(job_id)["counts"]
        status = "completed_with_errors" if counts["failed"] or counts["blocked"] else "completed"
        store.finish(job_id, status, time.monotonic() - started)
    return store.status(job_id)
