"""Durable SQLite frontier and atomic page/checkpoint transactions."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from webcollect.config import CrawlConfig


def utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS crawl_jobs (
                id TEXT PRIMARY KEY, config_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                total_requests INTEGER NOT NULL DEFAULT 0,
                bytes_received INTEGER NOT NULL DEFAULT 0,
                latency_seconds REAL NOT NULL DEFAULT 0,
                elapsed_seconds REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS frontier (
                job_id TEXT NOT NULL REFERENCES crawl_jobs(id),
                url_normalized TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                depth INTEGER NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, PRIMARY KEY(job_id, url_normalized)
            );
            CREATE INDEX IF NOT EXISTS frontier_pending ON frontier(job_id, status, depth);
            CREATE TABLE IF NOT EXISTS pages (
                job_id TEXT NOT NULL REFERENCES crawl_jobs(id), normalized_url TEXT NOT NULL,
                payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
                PRIMARY KEY(job_id, normalized_url)
            );
            CREATE TABLE IF NOT EXISTS request_statuses (
                job_id TEXT NOT NULL REFERENCES crawl_jobs(id), status INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id, status)
            );
            CREATE TABLE IF NOT EXISTS fetch_failures (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES crawl_jobs(id),
                url_normalized TEXT NOT NULL, attempt_count INTEGER NOT NULL,
                code TEXT NOT NULL, message TEXT NOT NULL, status INTEGER,
                retryable INTEGER NOT NULL, created_at TEXT NOT NULL
            );
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_job(self, config: CrawlConfig) -> str:
        job_id = uuid.uuid4().hex
        now = utcnow()
        with self.connection:
            self.connection.execute(
                "INSERT INTO crawl_jobs(id, config_json, status, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (job_id, json.dumps(config.to_dict()), now, now),
            )
            self.connection.executemany(
                "INSERT INTO frontier(job_id, url_normalized, depth) VALUES (?, ?, 0)",
                [(job_id, url) for url in config.seeds],
            )
        return job_id

    def get_job(self, job_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown job: {job_id}")
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        return result

    def frontier(self, job_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM frontier WHERE job_id=? ORDER BY depth, rowid", (job_id,)
            )
        ]

    def pages(self, job_id: str):
        self.get_job(job_id)
        for row in self.connection.execute(
            "SELECT payload_json FROM pages WHERE job_id=? ORDER BY normalized_url", (job_id,)
        ):
            yield json.loads(row[0])

    def status(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        counts = dict.fromkeys(("pending", "in_progress", "completed", "failed", "blocked"), 0)
        counts.update(
            dict(
                self.connection.execute(
                    "SELECT status, COUNT(*) FROM frontier WHERE job_id=? GROUP BY status",
                    (job_id,),
                )
            )
        )
        page_count = self.connection.execute(
            "SELECT COUNT(*) FROM pages WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        failures = self.connection.execute(
            "SELECT COUNT(*) FROM fetch_failures WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        elapsed = job["elapsed_seconds"]
        return {
            "job_id": job_id,
            "status": job["status"],
            "counts": counts,
            "pages": page_count,
            "total_requests": job["total_requests"],
            "bytes_received": job["bytes_received"],
            "errors": failures,
            "http_statuses": dict(
                self.connection.execute(
                    "SELECT status, count FROM request_statuses WHERE job_id=? ORDER BY status",
                    (job_id,),
                )
            ),
            "recent_failures": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT url_normalized, code, message, status, attempt_count, created_at "
                    "FROM fetch_failures WHERE job_id=? ORDER BY id DESC LIMIT 10",
                    (job_id,),
                )
            ],
            "latency_seconds": job["latency_seconds"],
            "elapsed_seconds": elapsed,
            "throughput_pages_per_second": page_count / elapsed if elapsed else 0.0,
        }

    def begin(self, job_id: str) -> None:
        self.get_job(job_id)
        with self.connection:
            self.connection.execute(
                "UPDATE frontier SET status='pending' WHERE job_id=? AND status='in_progress'",
                (job_id,),
            )
            self.connection.execute(
                "UPDATE crawl_jobs SET status='running', updated_at=? WHERE id=?",
                (utcnow(), job_id),
            )

    def finish(self, job_id: str, status: str, elapsed: float) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE frontier SET status='pending' WHERE job_id=? AND status='in_progress'",
                (job_id,),
            )
            self.connection.execute(
                "UPDATE crawl_jobs SET status=?, updated_at=?, "
                "elapsed_seconds=elapsed_seconds+? WHERE id=?",
                (status, utcnow(), elapsed, job_id),
            )

    def claim(self, job_id: str) -> dict | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM frontier WHERE job_id=? AND status='pending' "
                "AND depth <= COALESCE((SELECT MIN(depth) FROM frontier "
                "WHERE job_id=? AND status='in_progress'), depth) "
                "ORDER BY depth, rowid LIMIT 1",
                (job_id, job_id),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE frontier SET status='in_progress' WHERE job_id=? AND url_normalized=?",
                (job_id, row["url_normalized"]),
            )
        return dict(row)

    def start_attempt(self, job_id: str, url: str) -> int:
        with self.connection:
            self.connection.execute(
                "UPDATE frontier SET attempt_count=attempt_count+1 "
                "WHERE job_id=? AND url_normalized=?",
                (job_id, url),
            )
            return self.connection.execute(
                "SELECT attempt_count FROM frontier WHERE job_id=? AND url_normalized=?",
                (job_id, url),
            ).fetchone()[0]

    def requeue(self, job_id: str, url: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE frontier SET status='pending' WHERE job_id=? AND url_normalized=?",
                (job_id, url),
            )

    def request_started(self, job_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE crawl_jobs SET total_requests=total_requests+1 WHERE id=?", (job_id,)
            )

    def response_received(
        self, job_id: str, size: int, latency: float, status: int | None = None
    ) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE crawl_jobs SET bytes_received=bytes_received+?, "
                "latency_seconds=latency_seconds+? WHERE id=?",
                (size, latency, job_id),
            )
            if status is not None:
                self.connection.execute(
                    "INSERT INTO request_statuses(job_id, status, count) VALUES (?, ?, 1) "
                    "ON CONFLICT(job_id, status) DO UPDATE SET count=count+1",
                    (job_id, status),
                )

    def failure(
        self,
        job_id: str,
        url: str,
        *,
        code: str,
        message: str,
        status: int | None = None,
        retryable: bool = False,
        terminal: str = "failed",
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO fetch_failures(job_id,url_normalized,attempt_count,code,message,"
                "status,retryable,created_at) SELECT job_id,url_normalized,attempt_count,"
                "?,?,?,?,? FROM frontier WHERE job_id=? AND url_normalized=?",
                (code, message, status, int(retryable), utcnow(), job_id, url),
            )
            self.connection.execute(
                "UPDATE frontier SET status=?, last_error=? WHERE job_id=? AND url_normalized=?",
                (terminal, message, job_id, url),
            )

    def complete(
        self, job_id: str, url: str, page: dict, links: list[str], next_depth: int, max_pages: int
    ) -> None:
        # Commit results and discovered URLs together so restart cannot lose links.
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO pages(job_id,normalized_url,payload_json,fetched_at) "
                "VALUES (?,?,?,?)",
                (job_id, url, json.dumps(page), page["fetched_at"]),
            )
            self.connection.execute(
                "UPDATE frontier SET status='completed', last_error=NULL "
                "WHERE job_id=? AND url_normalized=?",
                (job_id, url),
            )
            remaining = (
                max_pages
                - self.connection.execute(
                    "SELECT COUNT(*) FROM frontier WHERE job_id=?", (job_id,)
                ).fetchone()[0]
            )
            for link in links:
                if remaining <= 0:
                    break
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO frontier(job_id,url_normalized,depth) VALUES (?,?,?)",
                    (job_id, link, next_depth),
                )
                remaining -= cursor.rowcount
