# Asynchronous Web Data Collector — Project Brief

**Project ID:** P07  
**Status:** Proposed MVP / Ready for team kickoff  
**Audience:** Engineering, QA, DevOps and project reviewers  
**Suggested delivery:** Weeks 11–12 (50h); confirm capacity with the team  
**Primary language:** Python 3.13+ (or a currently supported Python 3.x version)  
**Document type:** Engineering brief / implementation contract  

## 1. Executive Summary

An asynchronous Python collector that fetches and parses metadata from an explicitly permitted set of web pages. The system demonstrates structured concurrency, backpressure, rate control, deduplication, checkpointing and respectful crawling.

## 2. Goals and Success Metrics

**Goals**

- Collect authorized public metadata efficiently while honoring site policies.
- Bound concurrency, request rates, memory and queue size.
- Support interruption and restart without blindly refetching already completed URLs.

**MVP success**

- Demonstrates measurable performance improvement over sequential collection under comparable policies.
- Respects robots.txt/disallowed routes and never exceeds configured per-host concurrency.
- Resumes from persisted crawl state after cancellation or interruption.

## 3. Users and Use Cases

- Data engineer collecting approved public metadata
- Automation engineer studying asyncio and resilient I/O
- QA team testing concurrency and cancellation invariants

## 4. MVP Scope

### In Scope

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-01 | Seed from allowlisted URLs/domains and configure crawl depth/page limit | Must |
| FR-02 | Check robots.txt and honor crawl restrictions before fetching | Must |
| FR-03 | Fetch concurrently using asyncio/httpx with per-host semaphores | Must |
| FR-04 | Enforce per-host rate limit, timeouts, response-size ceilings | Must |
| FR-05 | Extract title, description, canonical URL and outbound links | Must |
| FR-06 | Deduplicate normalized URLs and track visited/queued/failed states | Must |
| FR-07 | Persist crawl frontier, results and checkpoint for restart | Must |
| FR-08 | Handle cancellation gracefully and release HTTP clients | Must |
| FR-09 | Report throughput, errors, statuses and fetch latency | Should |
| FR-10 | Export normalized results as JSONL/CSV | Must |

### Explicitly Out of Scope

- Authenticated scraping, bypassing paywalls/bot defenses or CAPTCHA.
- Fetching private user data without authorization.
- High-scale multi-region crawler, JavaScript browser rendering and search indexing.

## 5. User Interface / API Contract

```bash
webcollect check-robots https://example.org/
webcollect crawl --seed https://example.org/ --allow-domain example.org --depth 2 --concurrency 4 --rps 1 --max-pages 100
webcollect status --job JOB_ID
webcollect resume --job JOB_ID
webcollect export --job JOB_ID --format jsonl --output pages.jsonl
webcollect benchmark --seed http://localhost:8080/ --mode compare
```

**Contract guidelines:** CLI arguments and output formats are illustrative starting points. Freeze names, exit codes, and schemas during kickoff; document changes and make acceptance tests authoritative.

## 6. Technical Specification

### Proposed Stack

Python, asyncio, httpx, Beautiful Soup, SQLite, pytest-asyncio

### Domain / Data Contract

```text
Normalized result:
{
  "url": "https://example.org/about",
  "canonical_url": "https://example.org/about",
  "title": "About",
  "description": "...",
  "status": 200,
  "fetched_at": "2026-10-15T12:00:00Z",
  "outbound_links": ["https://example.org/contact"]
}

SQLite tables: crawl_jobs, frontier(url_normalized, status, depth, attempt_count, job_id), pages(job_id, normalized_url UNIQUE, payload_json, fetched_at), fetch_failures. Apply (job_id, normalized_url) unique constraints as needed.
```

### System Components

- CLI/config layer: seeds, domain allowlist, policy and output settings.
- Robots/policy layer: per-origin robots rules and permitted fetch decision.
- Frontier repository: URL normalization, deduplication and persistent job state.
- Async fetch coordinator: bounded queue, per-host semaphore, timeout, rate control.
- Parser: bounded HTML parsing and normalized metadata extraction.
- Reporter/exporter: throughput summary, JSONL/CSV outputs and benchmark artifact.

### Suggested Repository Structure

```text
project-root/
├── src/webcollect/cli.py
├── src/webcollect/robots.py
├── src/webcollect/frontier.py
├── src/webcollect/fetcher.py
├── src/webcollect/parser.py
├── src/webcollect/storage.py
├── src/webcollect/export.py
├── tests/test_robots.py
├── tests/test_concurrency.py
├── tests/test_resume.py
├── tests/fixtures/
├── pyproject.toml
├── README.md
```

## 7. Non-Functional Requirements and Safeguards

- Never bypass disallow rules, authentication or anti-bot protections; verify permission and terms before crawling.
- Use a descriptive User-Agent and contact information when appropriate.
- No unbounded asyncio.gather() or uncontrolled task creation; use queues and semaphores.
- Enforce maximum response size and domain allowlists to avoid unintended network destinations.
- Use a local HTTP fixture server in tests rather than depending on third-party uptime.

## 8. Implementation Plan

| Sprint/day | Engineering work |
| --- | --- |
| Week 11 — Days 1–2 | Study asyncio; define allowed sites, normalized URL policy and fixture server. |
| Week 11 — Days 3–5 | Build robots-aware coordinator, async fetch, parser and rate limiter. |
| Week 12 — Days 1–2 | Add persistent frontier, deduplication, checkpoint and resume. |
| Week 12 — Days 3–4 | Add graceful cancellation, export, retry classification and observability. |
| Week 12 — Day 5 | Run sequential/async benchmarks, fault tests, document crawl policies. |

**Recommended cadence:** kickoff/design → first vertical slice → negative-path tests → packaging/CI → demo and review. Keep PRs small and reviewable.

## 9. Acceptance Tests

| Test ID | Scenario | Expected result |
| --- | --- | --- |
| WEB-01 | Robots disallow for path | No request sent to prohibited path. |
| WEB-02 | Four concurrent workers, host limit 2 | Observed active requests to the host never exceed 2. |
| WEB-03 | Repeated same normalized URL | Exactly one persisted result per job and URL. |
| WEB-04 | Cancel during active crawl | Sockets close; frontier remains restartable. |
| WEB-05 | Resume interrupted crawl | Completed URLs are not blindly refetched. |
| WEB-06 | Oversized HTML response | Fetch stops at byte ceiling with recorded error. |
| WEB-07 | Link to disallowed external domain | Domain allowlist prevents request. |
| WEB-08 | Transient 5xx | Retry remains bounded; terminal failures surfaced. |
| WEB-09 | Sequential vs async fixture benchmark | Report measured throughput, conditions and policy-equivalent setup. |

**Testing approach:** pytest unit tests and integration tests with fixture files/services. Include negative paths, deterministic behavior, and a clean-install smoke test. Use isolated temporary directories/databases; never run destructive tests on real user or production data.

## 10. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Website policy violations | Robots enforcement, domain allowlist and conservative rate defaults. |
| Resource exhaustion | Bound queues, response bytes, tasks and per-host concurrency. |
| Lost progress on interruption | Persistent frontier transitions and graceful cancellation. |
| Duplicate pages / URL variants | Documented normalization plus database uniqueness. |

## 11. Team Workstreams and Deliverables

| Workstream | Suggested owner | Evidence / deliverable |
| --- | --- | --- |
| Specification and data contract | Tech lead / developer | Reviewed README, assumptions, diagrams, CLI/API examples |
| Implementation and integration | Python developer(s) | Working packaged application, sample configuration |
| Verification | Developer + QA | Automated unit/integration tests and acceptance report |
| Delivery and reliability | DevOps / maintainer | Reproducible setup, CI, operational notes |

**Ownership note:** Assign actual people and sprint dates at kickoff. These are roles, not staffing assumptions.

## 12. Definition of Done

- [ ] Functional MVP scope is delivered or explicitly descoped with approval.
- [ ] Acceptance tests in §9 pass on a clean environment.
- [ ] README includes install, configuration, usage, limitations and troubleshooting.
- [ ] Automated tests cover success, invalid input, and failure-recovery paths.
- [ ] Ruff checks, packaging/build and GitHub Actions CI pass.
- [ ] No credentials, private datasets or machine-specific absolute paths are committed.
- [ ] Operational metrics/benchmarks are attached where required.
- [ ] Reviewer has run the principal end-to-end demo and signed off.

## 13. References

- [asyncio](https://docs.python.org/3/library/asyncio.html)
- [HTTPX async support](https://www.python-httpx.org/async/)
- [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
- [Robots Exclusion Protocol (RFC 9309)](https://www.rfc-editor.org/rfc/rfc9309.html)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)

## 14. Open Decisions for Kickoff

- Confirm team owner, reviewers and delivery dates.
- Confirm Python runtime and support platforms (Windows/macOS/Linux) as applicable.
- Freeze default configuration, naming, CLI/API behavior and error formats.
- Agree test evidence, operational limits and rollback/recovery expectations.

---
*Prepared for engineering kickoff. This document is a proposed baseline, not evidence of implementation or completed tests.*
