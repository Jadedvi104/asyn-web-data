# Crawl policy and operational contract

Use this collector for approved public metadata. The brief excludes authentication,
private-data collection without permission, paywall bypass, anti-bot evasion, and
browser rendering.

## Destination rules

Crawls require explicit `--allow-domain` values: bare hostnames or IP addresses without
schemes, paths, ports, or wildcards. Matching is exact after lowercasing, IDNA conversion,
and removing a trailing hostname dot. `example.org` does not authorize `www.example.org`.

Only HTTP/HTTPS URLs are accepted. Credentials, invalid ports, backslashes, controls,
whitespace, malformed percent escapes, and unsupported schemes are rejected.
Seeds, admitted links, and every redirect destination must be allowlisted. An allowed
hostname may use either scheme and any supported port. These rules are not a firewall
or protection against DNS changes/private-address resolution.

Page redirects are followed manually for at most five transitions. Each destination
must pass the hostname allowlist and that destination's robots policy before its request.
Loops, missing/invalid Location values, and excess hops are terminal failures.
Canonical metadata does not trigger a request.

## Robots handling

Rules are cached by origin (`scheme://host:port`) for a run and reloaded on resume.
Per-origin locks avoid duplicate concurrent loads. The same User-Agent identifies
requests and selects the applicable policy.

| robots.txt response | Decision |
| --- | --- |
| Successful non-HTML response | Parse matching groups and apply longest matching Allow/Disallow rule. |
| HTTP 404 or 410 | No published policy; allow. |
| HTTP 401 or 403 | Deny. |
| Redirect | Deny without following. |
| Other error, timeout, network failure, oversized response, HTML interstitial | Deny. |

Robots bodies have a 512,000-byte ceiling. A valid empty policy permits collection.
Product-specific groups take priority over wildcard groups; repeated matching groups merge.
Rules support `*`, terminal `$`, normalized percent escapes, longest-rule priority, and
allow-on-tie. Fractional Crawl-delay and Request-rate directives can increase the start interval.
The Request-rate extension is parsed with `urllib.robotparser`.
An unavailable policy is cached as a denial for the run and creates a terminal blocked
entry. Retrying a page does not bypass robots. Complete RFC 9309 conformance is not claimed.

## Resource bounds

A fixed worker count and bounded queue limit scheduled work; SQLite holds the backlog.
Page, retry, redirect, and robots requests share worker connection limits, host semaphores,
and request-start pacing. Host gates span ports and HTTP/HTTPS origins. Starts are at
least `1 / rps` seconds apart, or any larger supported robots delay. A site's own delay
cannot be known until the first robots response arrives.

Responses request identity encoding and are streamed under a byte ceiling. Gzip and
zlib-wrapped deflate use bounded decoding; unsupported/stacked encodings, malformed
streams and concatenated gzip are terminal errors. Oversized declared Content-Length values
and decoded streamed bodies exceeding the limit stop collection. Requests have a total
deadline and HTTP client timeouts. Only successful 2xx HTML/XHTML responses are parsed.
Embedded stylesheets, scripts, images, and other resources are not fetched.

The page budget counts unique admitted frontier URLs: seeds, failed/blocked URLs, and
separately admitted redirect aliases included. Retries do not consume another slot.
Robots/redirect requests can make request totals exceed the page budget.
Depth counts links from seeds at depth zero. Scheduling waits for the current depth to
finish before fetching the next depth, so a slower short route cannot lose discovery
coverage to a faster long route.

## URL identity and metadata

Normalization lowercases scheme/host, converts hostnames to IDNA, removes default ports
and fragments, decodes unreserved percent escapes, removes dot segments, adds an empty
path's slash, and percent-encodes non-ASCII path/query characters. Other percent escapes
use uppercase hex. Query ordering/values and repeated slashes remain distinct.
Job/normalized-URL keys enforce deduplication.

The parser extracts title, meta description, canonical URL, and unique normalized
HTTP(S) anchor/area links. A valid HTML base changes relative-link resolution.
Results retain the admitted URL; relative metadata resolves against the final response.
Off-allowlist canonical/outbound metadata may appear in exports, while frontier admission
independently enforces scope.

## Failures and restart

Attempt budgets persist across interruption and resume. An interrupted request that exhausted
its ceiling becomes terminal. An attempt starts when the first page request begins,
after policy and host-gate waiting. Entries still waiting for those checks keep their budget.
Network failures/timeouts and HTTP 408, 425, 429, 500, 502, 503, and 504 are eligible for
bounded retries. The attempt ceiling includes the initial attempt. A short exponential
delay and host pacing apply before further requests.
Other HTTP failures, unsupported content, oversized bodies, invalid redirects, and
policy denials are terminal. Each failed attempt is retained in `fetch_failures`.

Result persistence, discovered links, and the completed frontier transition commit
together. Cancellation closes resources, records interruption, and returns unfinished
entries to pending. Resume also recovers stale in-progress work after a hard exit,
using saved configuration and preserving completed/failed/blocked entries.
A response received before an uncommitted result may be fetched again; restart is not
an exactly-once HTTP guarantee. Start a fresh job to revisit terminal failures.

One process per database is supported. Multiple collectors sharing a database are
outside the coordination model.

## Reporting and retention

Status includes frontier counts, completed pages, failed-attempt count, requests, bytes,
accumulated fetch latency, elapsed active run time, and throughput. Robots and redirects
count as requests. HTTP status histograms include fully received responses (including retries
and robots). Recent failures are bounded to the latest ten records. Byte and latency sums
exclude failed/oversized partial streams. Summed response latency can exceed wall time
under concurrency.

State is local and persistent. JSONL/CSV exports contain completed pages, use UTF-8,
and overwrite output files. CSV stores link arrays as JSON text. Retain metadata and
backups according to the site owner's permission and your retention needs.
