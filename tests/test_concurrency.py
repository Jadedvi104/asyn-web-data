from __future__ import annotations

import pytest

from webcollect.config import CrawlConfig
from webcollect.crawler import crawl
from webcollect.storage import Store


@pytest.mark.asyncio
async def test_web02_observed_per_host_concurrency_never_exceeds_two(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/fanout")], allow_domains=["127.0.0.1"],
        depth=1, concurrency=4, per_host_concurrency=2, rps=1000, max_pages=9,
        queue_size=1,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
    assert summary["pages"] == 9
    assert local_site.snapshot()["maximum_active"] == 2
    assert all(local_site.count(f"/slow/{n}") == 1 for n in range(8))


@pytest.mark.asyncio
async def test_requests_observe_per_host_rate_limit(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url(f"/page/{n}") for n in range(4)],
        allow_domains=["127.0.0.1"], depth=0, concurrency=4,
        per_host_concurrency=4, rps=10,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        await crawl(store, job_id)
    starts = sorted(
        moment
        for path, moments in local_site.snapshot()["starts"].items()
        if path.startswith("/page/")
        for moment in moments
    )
    assert len(starts) == 4
    # Allow a little scheduler/clock noise while detecting an unthrottled burst.
    assert all(later - earlier >= 0.08 for earlier, later in zip(starts, starts[1:]))
