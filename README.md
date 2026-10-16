# Asynchronous Web Data Collector

A Python CLI for collecting public HTML metadata from explicitly permitted domains.
Bounded asynchronous workers, per-host request pacing, robots checks, and a SQLite
frontier keep collection controlled and interrupted jobs restartable.

The distribution is `asynchronous-web-data-collector`; the command and Python package
are `webcollect`. The scope is defined in
[the project brief](asynchronous_web_data_collector_project_brief.md).

## Install

Use Python 3.13 or 3.14. The CI configuration targets Windows, macOS, and Linux.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
webcollect --help
```

On macOS/Linux, activate with `source .venv/bin/activate` instead.
For installation without development tools, run `python -m pip install .`.
Every example also works with `python -m webcollect` in place of `webcollect`.

## Local demo

Start the deterministic fixture in one terminal:

```console
python examples/local_site.py
```

In another terminal:

```console
webcollect check-robots http://127.0.0.1:8080/
webcollect --db .webcollect/demo.sqlite3 crawl --seed http://127.0.0.1:8080/ --allow-domain 127.0.0.1 --depth 2 --concurrency 4 --per-host-concurrency 2 --rps 5 --max-pages 30
```

The crawl prints its job ID immediately to stderr and then its final JSON summary to
stdout. Substitute the printed ID for `JOB_ID`:

```console
webcollect --db .webcollect/demo.sqlite3 status --job JOB_ID
webcollect --db .webcollect/demo.sqlite3 export --job JOB_ID --format jsonl --output pages.jsonl
webcollect --db .webcollect/demo.sqlite3 export --job JOB_ID --format csv --output pages.csv
```

The server provides 12 linked pages with a 150 ms delay, a redirect, duplicate fragment
references, and an unlinked robots-disallowed route. Inspect the denial with
`webcollect check-robots http://127.0.0.1:8080/private/secret`; this exits with code 1.

[examples/crawl-config.json](examples/crawl-config.json) contains sample settings.
Run `python examples/run_config.py` to translate them into CLI flags.
Its optional `--config PATH` chooses another JSON file. Paths in that file are relative
to the current working directory. The core CLI accepts flags rather than JSON configuration.

## Commands and defaults

`--db PATH` is a **global option before the subcommand**. Its default is
`.webcollect/crawls.sqlite3`.

| Command | Purpose |
| --- | --- |
| `check-robots URL [--allow-domain DOMAIN] [--user-agent TEXT]` | Print the policy decision; infer the input hostname if the allowlist is omitted. |
| `crawl --seed URL --allow-domain DOMAIN [OPTIONS]` | Start a new job; repeat either input flag for multiple values. |
| `status --job JOB_ID` | Print persisted states, counts, requests, errors, throughput, and accumulated fetch latency. |
| `resume --job JOB_ID` | Continue unfinished work with its saved configuration. |
| `export --job JOB_ID --format jsonl\|csv --output PATH` | Export completed HTML results. |
| `benchmark --seed URL --mode compare [OPTIONS]` | Compare sequential and concurrent jobs against the same target. |

Crawl options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--seed URL` | Required | Seed at depth 0; repeatable. |
| `--allow-domain DOMAIN` | Required | Exact hostname or IP address; repeatable. |
| `--depth` | `2` | Maximum link distance from a seed. |
| `--concurrency` | `4` | Total asynchronous workers. |
| `--per-host-concurrency` | `2` | Maximum active requests to one hostname. |
| `--rps` | `1` | Maximum request starts per second for each hostname. |
| `--max-pages` | `100` | Unique admitted URL budget, including seeds and blocked/failed URLs. |
| `--queue-size` | `100` | Maximum entries in the in-memory work queue. |
| `--timeout` | `10` | Request deadline in seconds. |
| `--max-response-bytes` | `2097152` | Body ceiling, including decoded content. |
| `--max-attempts` | `3` | Attempt ceiling for retryable failures, including the initial request. |
| `--user-agent` | `webcollect/0.1` | Descriptive identity; supply a real contact when appropriate. |

Retries and robots requests do not consume additional page-budget slots. They do count
as HTTP requests and share host pacing and concurrency limits. SQLite stores backlog
beyond the queue capacity. A breadth-level barrier preserves shortest-depth discovery
when different branches respond at different speeds. Reported outbound links may include URLs never admitted
because of the allowlist, depth, or page budget.

Before using an external site, confirm permission and terms, check robots, and use an
appropriate User-Agent. See [crawl policy](docs/crawl-policy.md).

## Interruption and recovery

Press Ctrl+C to stop. HTTP resources close, interruption is recorded, and unfinished
entries become pending. Resume with the same database:

```console
webcollect --db .webcollect/demo.sqlite3 resume --job JOB_ID
```

The attempt budget survives interruption: an interrupted request that already exhausted its
ceiling becomes terminal on resume; queued work that never started retains its budget.
Completed, failed, and blocked entries are preserved. Resume does not automatically
retry terminal failures; start a new job after repairing a terminal error or changing
configuration. Stale in-progress rows after a hard process exit are recovered too.
Only one collector process should use a database at a time.

Frontier states: `pending`, `in_progress`, `completed`, `failed`, `blocked`.
Job states: `pending`, `running`, `interrupted`, `completed`, `completed_with_errors`.
Jobs with terminal failed/blocked entries return 1 even when other pages succeeded.

| Exit code | Meaning |
| --- | --- |
| `0` | Operation succeeded. |
| `1` | Policy denial or terminal operation/crawl failure. |
| `2` | Invalid arguments/configuration or unknown job. |
| `130` | Interrupted with Ctrl+C. |

## Result format

Each JSONL line contains one completed page:

```json
{
  "url": "https://example.org/about",
  "canonical_url": "https://example.org/about",
  "title": "About",
  "description": "A short description.",
  "status": 200,
  "fetched_at": "2026-10-15T12:00:00Z",
  "outbound_links": ["https://example.org/contact"]
}
```

`url` identifies the admitted normalized URL, including when it redirected. Relative
metadata resolves against the final response URL and any valid HTML base element.
Missing titles/descriptions are empty strings. Missing/invalid canonical links fall
back to the response URL. Metadata does not authorize fetching another domain.
CSV uses the same columns, with `outbound_links` encoded as JSON inside a cell.
Exports use UTF-8 and overwrite the chosen output file.

## Benchmark

With the fixture running:

```console
webcollect --db .webcollect/benchmark.sqlite3 benchmark --seed http://127.0.0.1:8080/ --allow-domain 127.0.0.1 --mode compare --concurrency 4 --per-host-concurrency 2 --rps 20 --max-pages 20 --output benchmark.json
```

Defaults are 4 workers, host limit 2, 20 request starts per second, and 20 admitted URLs.
The allowlist defaults to the seed hostname. Sequential collection uses one worker;
the concurrent run uses the requested worker count. Both use the same scope, host
ceiling, pacing, and page budget, with cold robots caches; the sequential run goes first.

The JSON report has `sequential` and `async` job summaries, observed `speedup`, and
`conditions` containing configuration and comparison notes. Keep this artifact as
measurement evidence. Latency, rate limits, and server caching affect the result;
a benchmark does not guarantee faster collection for every target.

The [recorded local comparison](docs/benchmarks/local-comparison.json) collected 14 results
per run on Windows/Python 3.13: 2.78 seconds sequentially and 1.69 seconds concurrently
(1.64x observed speedup). The fixture used 12 linked pages, 150 ms response delay,
20 starts/second and a host limit of two. These are local measurements, not an external-site promise.

## Development and verification

```console
python -m ruff check .
python -m pytest
python -m build
```

Tests use local fixtures and isolated databases. GitHub Actions is configured for lint,
tests, package builds, and a clean wheel-install CLI smoke test on Python 3.13/3.14 across
Windows/macOS/Linux. A workflow file is not evidence of a completed remote CI run.
Local verification: 112 tests passed on Windows with Python 3.13 and Python 3.14,
including a fresh wheel installation. See [validation evidence](docs/validation.md)
and [implementation and acceptance tracking](docs/implementation-plan.md).

## Limitations and troubleshooting

- Only HTTP(S) HTML/XHTML metadata is collected. There is no JavaScript rendering,
  authentication workflow, CAPTCHA handling, or anti-bot bypass.
- Exact-host allowlisting is not network isolation. Allowed names may resolve to private
  addresses and use non-default ports; local fixture addresses are intentionally supported.
- Robots matching supports product-specific groups, merged groups, wildcard/end-anchor
  rules, longest-rule priority, and allow-on-tie. Complete RFC 9309 conformance is not
  claimed. Policy load failures block the origin for that run.
- Requests prefer uncompressed bodies. Bounded gzip and zlib-wrapped deflate decoding
  is supported; unsupported/stacked encodings and malformed streams are recorded as failures.
- If `webcollect` is unavailable, activate the environment or use `python -m webcollect`.
- For an unknown job, select its original database and use the complete printed ID.
- If port 8080 is occupied, run `python examples/local_site.py --port 8081` and update the seed.
- Results persist locally until removed. Back up after stopping the collector, or preserve
  the database and its WAL files together when copying active state.
