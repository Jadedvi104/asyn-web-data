from __future__ import annotations

import pytest

from webcollect.config import CrawlConfig
from webcollect.crawler import crawl
from webcollect.storage import Store


@pytest.mark.asyncio
async def test_web01_web03_web07_robots_deduplication_and_allowlist(local_site, tmp_path):
    """Disallowed pages and redirect destinations are never requested."""
    config = CrawlConfig(
        seeds=[local_site.url("/linked"), local_site.url("/linked#duplicate")],
        allow_domains=["127.0.0.1"], depth=1, rps=1000,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        pages = list(store.pages(job_id))
        frontier = store.frontier(job_id)

    assert local_site.count("/blocked") == 0, "WEB-01: robots-disallowed path was fetched"
    assert local_site.count("/outside") == 0, "WEB-07: external host was fetched"
    assert local_site.count("/linked") == 1
    assert local_site.count("/page/1") == 1
    urls = [page["url"] for page in pages]
    assert len(urls) == len(set(urls)) == 3, "WEB-03: normalized results must be unique"
    page = next(page for page in pages if page["url"] == local_site.url("/page/1"))
    assert page["title"] == "Fixture page"
    assert page["description"] == "A local acceptance fixture."
    assert page["canonical_url"] == local_site.url("/canonical")
    assert page["status"] == 200
    assert page["fetched_at"]
    assert summary["pages"] == 3
    assert any(row["status"] == "blocked" for row in frontier)


@pytest.mark.asyncio
async def test_web06_oversized_response_is_recorded_as_failure(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/oversized")], allow_domains=["127.0.0.1"],
        depth=0, max_response_bytes=1024, max_attempts=2, rps=1000,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        assert list(store.pages(job_id)) == []
        assert summary["counts"]["failed"] == 1
        assert summary["errors"] >= 1
        assert store.frontier(job_id)[0]["attempt_count"] <= 2
    assert local_site.count("/oversized") <= 2


@pytest.mark.asyncio
async def test_web08_transient_failures_retry_and_terminal_failures_are_bounded(
    local_site, tmp_path,
):
    config = CrawlConfig(
        seeds=[local_site.url("/retry"), local_site.url("/always-fails")],
        allow_domains=["127.0.0.1"], depth=0, max_attempts=3, rps=1000,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        pages = list(store.pages(job_id))
        assert [page["title"] for page in pages] == ["Recovered"]
        assert summary["counts"]["completed"] == 1
        assert summary["counts"]["failed"] == 1
        assert summary["errors"] >= 1
        assert all(row["attempt_count"] <= 3 for row in store.frontier(job_id))
    assert local_site.count("/retry") == 3
    assert local_site.count("/always-fails") == 3


@pytest.mark.asyncio
async def test_timeouts_are_bounded_and_persisted(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/timeout")], allow_domains=["127.0.0.1"],
        depth=0, timeout=0.2, max_attempts=2, rps=1000,
    )
    with Store(tmp_path / "collector.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        assert summary["counts"]["failed"] == 1
        assert summary["errors"] >= 1
        assert list(store.pages(job_id)) == []
    assert local_site.count("/timeout") == 2


@pytest.mark.asyncio
async def test_depth_and_page_limits_bound_collection(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/chain/0")], allow_domains=["127.0.0.1"],
        depth=1, max_pages=2, rps=1000,
    )
    with Store(tmp_path / "depth.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        assert summary["pages"] == 2
    assert local_site.count("/chain/2") == 0

    config = CrawlConfig(
        seeds=[local_site.url("/fanout")], allow_domains=["127.0.0.1"],
        depth=2, max_pages=3, concurrency=4, per_host_concurrency=2, rps=1000,
    )
    with Store(tmp_path / "limit.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        assert summary["pages"] == 3
        assert len(list(store.pages(job_id))) == 3




@pytest.mark.asyncio
async def test_redirect_destination_is_checked_against_robots(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/redirect-blocked")], allow_domains=["127.0.0.1"],
        depth=0, rps=1000,
    )
    with Store(tmp_path / "redirect.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        assert summary["counts"]["blocked"] == 1
        assert summary["pages"] == 0
    assert local_site.count("/redirect-blocked") == 1
    assert local_site.count("/blocked") == 0


@pytest.mark.asyncio
async def test_retry_backoff_does_not_duplicate_work_or_exceed_attempt_budget(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/always-fails")]
              + [local_site.url(f"/page/{number}") for number in range(8)],
        allow_domains=["127.0.0.1"], depth=0, concurrency=8,
        per_host_concurrency=8, queue_size=1, rps=1000, max_attempts=3,
    )
    with Store(tmp_path / "retry-budget.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        frontier = store.frontier(job_id)
        failed = next(row for row in frontier if row["url_normalized"].endswith("/always-fails"))
        assert failed["status"] == "failed"
        assert failed["attempt_count"] == 3
        assert summary["pages"] == 8
    assert local_site.count("/always-fails") == 3


@pytest.mark.asyncio
async def test_fast_deep_path_cannot_hide_valid_shallower_link_discovery(local_site, tmp_path):
    config = CrawlConfig(
        seeds=[local_site.url("/depth")], allow_domains=["127.0.0.1"],
        depth=3, rps=1000, concurrency=4, per_host_concurrency=4,
    )
    with Store(tmp_path / "shortest-depth.db") as store:
        job_id = store.create_job(config)
        summary = await crawl(store, job_id)
        depths = {row["url_normalized"]: row["depth"] for row in store.frontier(job_id)}
        assert depths[local_site.url("/depth/x")] == 2
        assert depths[local_site.url("/depth/y")] == 3
        assert summary["pages"] == 6
    assert local_site.count("/depth/x") == 1
    assert local_site.count("/depth/y") == 1
