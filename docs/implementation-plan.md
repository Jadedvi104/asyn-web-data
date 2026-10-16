# Implementation plan and acceptance tracking

The [project brief](../asynchronous_web_data_collector_project_brief.md) is the scope baseline.
Unchecked items below require recorded evidence or signoff, rather than necessarily
indicating missing implementation.

## Delivery sequence

| Stage | Deliverable | Verification |
| --- | --- | --- |
| 1. Policy/foundation | Src package, CLI validation, URL identity, exact-host rules, local fixture | Configuration, normalization, policy tests |
| 2. Async slice | Robots-aware streaming, bounded queue/workers, host concurrency/pacing, parser | Local integration and negative-path tests |
| 3. Durability | SQLite jobs/frontier/pages/failures, atomic checkpoints, resume | Cancellation and completed-page preservation tests |
| 4. Operations | Bounded retry, JSON status/export, comparable benchmark | Failure classification, export, fixture benchmark |
| 5. Delivery | Setup, sample config, wheel/sdist, CI | Ruff, pytest, build, clean wheel smoke |

## Frozen choices

- Targets: Python 3.13/3.14; Windows, macOS, and Linux.
- Distribution: `asynchronous-web-data-collector`; package/command: `webcollect`.
- Default database: `.webcollect/crawls.sqlite3`; global `--db` precedes the subcommand.
- Crawl requires repeatable `--seed` and explicit repeatable `--allow-domain`.
- Check-robots and benchmark may infer the input/seed hostname when no allowlist is given.
- Exit codes: 0 success; 1 terminal failure/denial; 2 invalid input/unknown job; 130 Ctrl+C.
- Fields: url, canonical_url, title, description, status, fetched_at, outbound_links.
- Frontier states: pending, in_progress, completed, failed, blocked.
- Resume preserves terminal states and recovers unfinished work with saved configuration.
- Maximum pages counts unique admitted URLs rather than successful results.
- Automated tests and the local demo need no third-party websites.

See [README](../README.md) for defaults and [crawl policy](crawl-policy.md) for operational rules.

## Acceptance evidence

Local verification completed on 2026-10-16. See [validation evidence](validation.md)
and the [measured comparison](benchmarks/local-comparison.json). The remaining unchecked
items require a remote repository run or human review.

- [x] WEB-01: A robots-disallowed route receives no page request.
- [x] WEB-02: Four workers with host limit two never exceed two active requests per host.
- [x] WEB-03: Repeated normalized URLs create one persisted result per job/URL.
- [x] WEB-04: Cancellation closes resources and leaves a restartable frontier.
- [x] WEB-05: Resume preserves completed URLs without blindly fetching them again.
- [x] WEB-06: Oversized HTML stops at the byte ceiling with a recorded error.
- [x] WEB-07: Off-allowlist links/redirect destinations receive no request.
- [x] WEB-08: Transient 5xx retries are bounded and terminal errors are surfaced.
- [x] WEB-09: A local sequential/async benchmark records timing, throughput, common policy.
- [x] Invalid arguments/configuration and unknown jobs use the documented exit code.
- [x] JSONL/CSV contain the documented normalized result schema.
- [x] Ruff and automated tests pass in the delivery environment.
- [x] Wheel and source distributions build.
- [x] A clean environment installs the wheel and runs the CLI.
- [ ] The cross-platform Python 3.13/3.14 CI matrix passes (workflow configured; remote run pending).
- [ ] An actual owner/reviewer signs off the principal demo (human review pending).

## Review notes

The CI workflow needs an actual repository run for remote CI evidence. Team names,
review dates, and benchmark results must come from observed evidence. The brief's
proposed week/day schedule is planning context rather than a delivery commitment.
