"""Configuration and durable transaction checks independent of HTTP fixtures."""

import pytest

from webcollect.cli import main
from webcollect.config import CrawlConfig
from webcollect.storage import Store


@pytest.mark.parametrize(
    "override",
    [
        {"rps": 0},
        {"rps": float("nan")},
        {"timeout": float("inf")},
        {"depth": -1},
        {"queue_size": 0},
        {"max_pages": 0},
        {"max_attempts": 0},
        {"concurrency": True},
        {"per_host_concurrency": -2},
        {"max_response_bytes": 0},
        {"user_agent": "bad\r\nheader"},
    ],
)
def test_invalid_limits_are_rejected(override):
    with pytest.raises(ValueError):
        CrawlConfig(seeds=["https://example.test/"], allow_domains=["example.test"], **override)


def test_seed_policy_is_validated_before_database_creation(tmp_path, capsys):
    database = tmp_path / "unused.sqlite3"
    result = main(
        [
            "--db",
            str(database),
            "crawl",
            "--seed",
            "https://outside.test/",
            "--allow-domain",
            "example.test",
        ]
    )
    assert result == 2
    assert not database.exists()
    assert "allow" in capsys.readouterr().err.lower()


def test_page_and_frontier_are_committed_atomically_and_scoped_to_job(tmp_path):
    config = CrawlConfig(seeds=["https://example.test/"], allow_domains=["example.test"])
    database = tmp_path / "jobs.sqlite3"
    with Store(database) as store:
        first = store.create_job(config)
        second = store.create_job(config)
        store.begin(first)
        item = store.claim(first)
        page = {"url": item["url_normalized"], "fetched_at": "2026-10-16T00:00:00Z"}
        with pytest.raises(KeyError):
            store.complete(first, item["url_normalized"], {"url": page["url"]}, [], 1, 100)
        assert store.status(first)["pages"] == 0
        assert store.frontier(first)[0]["status"] == "in_progress"
        store.complete(
            first,
            page["url"],
            page,
            ["https://example.test/next", "https://example.test/next"],
            1,
            100,
        )
    with Store(database) as store:
        assert store.status(first)["pages"] == 1
        assert len(store.frontier(first)) == 2
        assert store.status(second)["pages"] == 0
        assert store.frontier(second)[0]["status"] == "pending"


def test_export_cannot_overwrite_database_or_sidecar(tmp_path, capsys):
    database = tmp_path / "jobs.sqlite3"
    with Store(database) as store:
        job = store.create_job(
            CrawlConfig(seeds=["https://example.test/"], allow_domains=["example.test"])
        )
    before = database.read_bytes()
    for output in (database, tmp_path / "jobs.sqlite3-wal", tmp_path / "jobs.sqlite3-shm"):
        assert (
            main(
                [
                    "--db",
                    str(database),
                    "export",
                    "--job",
                    job,
                    "--format",
                    "jsonl",
                    "--output",
                    str(output),
                ]
            )
            == 2
        )
        assert "Output must differ" in capsys.readouterr().err
    assert database.read_bytes() == before
