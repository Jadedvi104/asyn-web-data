from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from webcollect.config import CrawlConfig
from webcollect.crawler import crawl
from webcollect.storage import Store


class ObservedTransport(httpx.AsyncHTTPTransport):
    closed = False

    async def __aexit__(self, *args):
        await super().__aexit__(*args)
        self.closed = True

    async def aclose(self):
        await super().aclose()
        self.closed = True


async def wait_until(predicate, *, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Local crawl did not reach the expected checkpoint")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_web04_web05_cancellation_closes_sockets_and_resume_keeps_completed_results(
    local_site, tmp_path,
):
    database = tmp_path / "checkpoint.db"
    config = CrawlConfig(
        seeds=[local_site.url("/fast"), local_site.url("/stream/0"),
               local_site.url("/slow/1"), local_site.url("/slow/2")],
        allow_domains=["127.0.0.1"], depth=0, concurrency=2,
        per_host_concurrency=1, rps=1000,
    )
    transport = ObservedTransport(retries=0)
    with Store(database) as store:
        job_id = store.create_job(config)
        task = asyncio.create_task(crawl(store, job_id, transport=transport))
        try:
            await wait_until(lambda: (
                any(page["url"] == local_site.url("/fast") for page in store.pages(job_id))
                and local_site.count("/stream/0") >= 1
            ))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert transport.closed, "WEB-04: the crawl must release its HTTP client"
        assert store.get_job(job_id)["status"] == "interrupted"
        assert all(row["status"] != "in_progress" for row in store.frontier(job_id))
        assert any(row["status"] == "pending" for row in store.frontier(job_id))

    await wait_until(lambda: local_site.snapshot()["disconnected_streams"] >= 1, timeout=2)
    assert local_site.count("/fast") == 1

    # Reopen SQLite as a fresh process would: resumption depends on durable state.
    with Store(database) as reopened:
        summary = await crawl(reopened, job_id)
        urls = [page["url"] for page in reopened.pages(job_id)]
        assert len(urls) == len(set(urls)) == 4
        assert summary["status"] == "completed"
        assert summary["counts"]["pending"] == 0
        assert summary["counts"]["in_progress"] == 0
        assert summary["counts"]["completed"] == 4
    assert local_site.count("/fast") == 1, "WEB-05: a completed URL was fetched again"
    assert local_site.count("/stream/0") == 2



@pytest.mark.asyncio
async def test_resume_does_not_reset_a_consumed_attempt_budget(local_site, tmp_path):
    database = tmp_path / "attempt-budget.db"
    config = CrawlConfig(
        seeds=[local_site.url("/stream/0")], allow_domains=["127.0.0.1"],
        depth=0, concurrency=1, max_attempts=1, rps=1000,
    )
    with Store(database) as store:
        job_id = store.create_job(config)
        task = asyncio.create_task(crawl(store, job_id))
        try:
            await wait_until(lambda: local_site.count("/stream/0") == 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert store.frontier(job_id)[0]["attempt_count"] == 1

    with Store(database) as reopened:
        summary = await crawl(reopened, job_id)
        assert summary["counts"]["failed"] == 1
        assert summary["pages"] == 0
        assert reopened.frontier(job_id)[0]["attempt_count"] == 1
    assert local_site.count("/stream/0") == 1


@pytest.mark.asyncio
async def test_cancelled_host_waiters_keep_their_unspent_attempt_budget(local_site, tmp_path):
    database = tmp_path / "waiting-budget.db"
    config = CrawlConfig(
        seeds=[local_site.url("/stream/0"), local_site.url("/page/1"),
               local_site.url("/page/2")],
        allow_domains=["127.0.0.1"], depth=0, concurrency=3,
        per_host_concurrency=1, max_attempts=1, rps=1000,
    )
    with Store(database) as store:
        job_id = store.create_job(config)
        task = asyncio.create_task(crawl(store, job_id))
        try:
            await wait_until(lambda: local_site.count("/stream/0") == 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        attempts = {
            row["url_normalized"]: row["attempt_count"] for row in store.frontier(job_id)
        }
        assert attempts[local_site.url("/stream/0")] == 1
        for path in ("/page/1", "/page/2"):
            assert local_site.count(path) == 0
            assert attempts[local_site.url(path)] == 0

    with Store(database) as reopened:
        summary = await crawl(reopened, job_id)
        assert summary["counts"]["failed"] == 1
        assert summary["counts"]["completed"] == 2
        assert summary["pages"] == 2
    assert local_site.count("/stream/0") == 1
    assert local_site.count("/page/1") == 1
    assert local_site.count("/page/2") == 1
