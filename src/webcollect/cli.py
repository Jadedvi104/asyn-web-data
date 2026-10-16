"""Public command-line contract for local crawl job management."""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import httpx

from webcollect import __version__
from webcollect.config import CrawlConfig
from webcollect.crawler import HostGate, crawl
from webcollect.export import export_pages
from webcollect.fetcher import fetch_response
from webcollect.robots import RobotsPolicy
from webcollect.storage import Store
from webcollect.urls import host_for, is_allowed, normalize_domain, normalize_url


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="webcollect", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", type=Path, default=Path(".webcollect/crawls.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-robots", help="Inspect the current robots decision")
    check.add_argument("url")
    check.add_argument("--allow-domain", action="append")
    check.add_argument("--user-agent", default="webcollect/0.1")
    collect = commands.add_parser("crawl", help="Create and run a durable crawl")
    collect.add_argument("--seed", action="append", required=True)
    collect.add_argument("--allow-domain", action="append", required=True)
    collect.add_argument("--depth", type=int, default=2)
    collect.add_argument("--concurrency", type=int, default=4)
    collect.add_argument("--per-host-concurrency", type=int, default=2)
    collect.add_argument("--rps", type=float, default=1)
    collect.add_argument("--max-pages", type=int, default=100)
    collect.add_argument("--queue-size", type=int, default=100)
    collect.add_argument("--timeout", type=float, default=10)
    collect.add_argument("--max-response-bytes", type=int, default=2_097_152)
    collect.add_argument("--max-attempts", type=int, default=3)
    collect.add_argument("--user-agent", default="webcollect/0.1")
    for command in ("status", "resume"):
        child = commands.add_parser(command)
        child.add_argument("--job", required=True)
    export = commands.add_parser("export")
    export.add_argument("--job", required=True)
    export.add_argument("--format", choices=("jsonl", "csv"), required=True)
    export.add_argument("--output", type=Path, required=True)
    benchmark = commands.add_parser("benchmark", help="Compare equivalent sequential/async jobs")
    benchmark.add_argument("--seed", required=True)
    benchmark.add_argument("--allow-domain", action="append")
    benchmark.add_argument("--mode", choices=("compare",), default="compare")
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--concurrency", type=int, default=4)
    benchmark.add_argument("--per-host-concurrency", type=int, default=2)
    benchmark.add_argument("--rps", type=float, default=20)
    benchmark.add_argument("--max-pages", type=int, default=20)
    return parser


async def check_robots(args) -> dict:
    url = normalize_url(args.url)
    domains = [normalize_domain(d) for d in (args.allow_domain or [host_for(url)])]
    if not is_allowed(url, domains):
        raise ValueError("URL must belong to the explicit allowlist")
    gate = HostGate(1, 1)
    async with httpx.AsyncClient(
        headers={"User-Agent": args.user_agent},
        timeout=10,
        trust_env=False,
        follow_redirects=False,
    ) as client:

        async def request(target: str, max_bytes: int):
            async def perform():
                async with asyncio.timeout(10):
                    return await fetch_response(client, target, max_bytes=max_bytes)

            return await gate.run(target, perform)

        decision = await RobotsPolicy(client, user_agent=args.user_agent, fetch=request).check(url)
    return {
        "url": url,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "crawl_delay": decision.crawl_delay,
    }


async def benchmark_jobs(store: Store, args) -> dict:
    seed = normalize_url(args.seed)
    domains = args.allow_domain or [host_for(seed)]
    common = dict(
        seeds=[seed],
        allow_domains=domains,
        rps=args.rps,
        max_pages=args.max_pages,
        per_host_concurrency=args.per_host_concurrency,
    )
    sequential_config = CrawlConfig(**common, concurrency=1)
    concurrent_config = CrawlConfig(**common, concurrency=args.concurrency)
    sequential = await crawl(store, store.create_job(sequential_config))
    concurrent = await crawl(store, store.create_job(concurrent_config))
    duration = concurrent["elapsed_seconds"]
    return {
        "sequential": sequential,
        "async": concurrent,
        "speedup": sequential["elapsed_seconds"] / duration if duration else 0.0,
        "conditions": {
            **concurrent_config.to_dict(),
            "sequential_concurrency": 1,
            "policy": "Identical scope, limits and cold robots caches; sequential first",
            "note": "Speedup depends on latency and policy; rps may dominate",
        },
    }


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command == "check-robots":
            result = asyncio.run(check_robots(args))
            emit(result)
            return 0 if result["allowed"] else 1
        # Validate before creating files or a job for malformed crawl arguments.
        config = None
        if args.command == "crawl":
            config = CrawlConfig(
                seeds=args.seed,
                allow_domains=args.allow_domain,
                depth=args.depth,
                concurrency=args.concurrency,
                per_host_concurrency=args.per_host_concurrency,
                rps=args.rps,
                max_pages=args.max_pages,
                queue_size=args.queue_size,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                max_attempts=args.max_attempts,
                user_agent=args.user_agent,
            )
        if args.command in {"export", "benchmark"} and args.output:
            protected = {
                args.db.resolve(),
                Path(str(args.db) + "-wal").resolve(),
                Path(str(args.db) + "-shm").resolve(),
            }
            if args.output.resolve() in protected:
                raise ValueError("Output must differ from the crawl database files")
        with Store(args.db) as store:
            if args.command in {"crawl", "resume"}:
                job_id = store.create_job(config) if config is not None else args.job
                # Print ID early on stderr, so Ctrl+C users can resume even without a summary.
                print(f"Job: {job_id}", file=sys.stderr)
                result = asyncio.run(crawl(store, job_id))
                emit(result)
                return 0 if result["status"] == "completed" else 1
            if args.command == "status":
                emit(store.status(args.job))
            elif args.command == "export":
                count = export_pages(store, args.job, args.output, args.format)
                emit(
                    {
                        "job_id": args.job,
                        "format": args.format,
                        "output": str(args.output),
                        "pages": count,
                    }
                )
            elif args.command == "benchmark":
                result = asyncio.run(benchmark_jobs(store, args))
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                emit(result)
                return (
                    0
                    if all(result[key]["status"] == "completed" for key in ("sequential", "async"))
                    else 1
                )
    except KeyboardInterrupt:
        print("Interrupted; the saved job can be resumed.", file=sys.stderr)
        return 130
    except (ValueError, TypeError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError, ExceptionGroup) as error:
        print(f"Operation failed: {error}", file=sys.stderr)
        return 1
    return 0
