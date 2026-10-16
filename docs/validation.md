# Initialization validation

Verified locally on 2026-10-16. No external website was crawled during validation.

| Check | Environment | Result |
| --- | --- | --- |
| `python -m ruff check .` | Windows, Python 3.13.6 | Passed |
| `python -m pytest -q` | Windows, Python 3.13.6, editable development install | 112 passed |
| `python -m build` | Isolated setuptools build | Wheel and source distribution built |
| Wheel install, `webcollect --version`, `python -m webcollect --help` | Fresh Windows/Python 3.14.2 environment, no editable install | Passed; version 0.1.0 |
| `python -m pytest -q` against installed wheel | Fresh Windows/Python 3.14.2 environment | 112 passed |
| Source archive inspection | Built source distribution | Tests, conftest, fixture HTML, examples, guides, brief and CI workflow included |
| Local fixture benchmark | Windows/Python 3.13.6 | 14 results per mode; both jobs completed |

The tests cover WEB-01 through WEB-09, bounded queues and host activity, request pacing,
response size and decompression limits, robots rules and redirects, URL deduplication,
shortest-depth discovery, atomic checkpoints, cancellation/socket cleanup, fresh-store
resume, persistent retry budgets, invalid configuration, status diagnostics and exports.

## Measured comparison

The [saved benchmark](benchmarks/local-comparison.json) records conditions and full summaries:
12 linked fixture pages with 150 ms response delay, one redirect alias, 20 starts/second,
host concurrency two, sequential worker count one, concurrent worker count four.
Both runs use the same policy and cold robots caches.

- Sequential: 14 results in 2.78 seconds.
- Concurrent: 14 results in 1.69 seconds.
- Observed speedup: 1.64x. Timing varies by machine, latency and rate policy.

## Handover

The project repository is [Jadedvi104/asyn-web-data](https://github.com/Jadedvi104/asyn-web-data).
The virtual environment and generated distributions are ignored by Git. Setup, demo
and operational instructions are in the README.

GitHub Actions is configured for Python 3.13/3.14 on Windows, macOS and Linux. Remote
results are available in the repository's Actions tab; this document records local checks. Human ownership and reviewer signoff remain open as
recorded in the implementation checklist. These are separate from the completed local
project initialization and validation.
