"""Robots restrictions are checked once per origin and fail conservatively."""

import asyncio

import httpx
import pytest

from webcollect.fetcher import FetchResult
from webcollect.robots import RobotsPolicy


@pytest.mark.asyncio
async def test_disallow_allow_and_crawl_delay():
    async def handler(request):
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\nCrawl-delay: 2\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        policy = RobotsPolicy(client, user_agent="webcollect/0.1")
        denied = await policy.check("https://example.com/private/page")
        allowed = await policy.check("https://example.com/public")
    assert not denied.allowed
    assert denied.reason == "robots_disallowed"
    assert denied.crawl_delay == 2
    assert allowed.allowed
    assert allowed.crawl_delay == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,allowed",
    [
        (404, True),
        (410, True),
        (401, False),
        (403, False),
        (429, False),
        (500, False),
        (503, False),
        (302, False),
    ],
)
async def test_status_policy_and_no_redirects(status, allowed):
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(status, headers={"location": "https://outside.example/robots.txt"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        decision = await RobotsPolicy(client, user_agent="webcollect/0.1").check(
            "https://example.com/"
        )
    assert decision.allowed is allowed
    assert requests == ["https://example.com/robots.txt"]


@pytest.mark.asyncio
async def test_network_failure_denies_access():
    async def handler(request):
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await RobotsPolicy(client, user_agent="webcollect/0.1").check(
            "https://example.com/"
        )
    assert not decision.allowed
    assert decision.reason == "robots_unavailable:network_error"


@pytest.mark.asyncio
async def test_concurrent_checks_cache_once_per_origin():
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        await asyncio.sleep(0)
        return httpx.Response(200, text="User-agent: *\nDisallow: /blocked\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        policy = RobotsPolicy(client, user_agent="webcollect/0.1")
        decisions = await asyncio.gather(
            *(policy.check(f"https://example.com/{i}") for i in range(8))
        )
        await policy.check("http://example.com/")
        await policy.check("https://example.com:8443/")
    assert all(decision.allowed for decision in decisions)
    assert requests == [
        "https://example.com/robots.txt",
        "http://example.com/robots.txt",
        "https://example.com:8443/robots.txt",
    ]


@pytest.mark.asyncio
async def test_robots_fetch_callback_can_apply_shared_host_controls():
    calls = []

    async def fetch(url, max_bytes):
        calls.append((url, max_bytes))
        return FetchResult(url, 200, b"User-agent: *\nRequest-rate: 1/4\n", {}, 0)

    async with httpx.AsyncClient() as client:
        policy = RobotsPolicy(client, user_agent="webcollect/0.1", fetch=fetch, max_bytes=120)
        decision = await policy.check("https://example.com/page")
    assert calls == [("https://example.com/robots.txt", 120)]
    assert decision.allowed
    assert decision.crawl_delay == 4


@pytest.mark.asyncio
async def test_oversized_or_html_policy_is_denied():
    async def handler(request):
        return httpx.Response(
            200, text="<html>interstitial</html>", headers={"content-type": "text/html"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await RobotsPolicy(client, user_agent="webcollect/0.1").check(
            "https://example.com/"
        )
        oversized = await RobotsPolicy(client, user_agent="webcollect/0.1", max_bytes=2).check(
            "https://example.com/"
        )
    assert not decision.allowed
    assert decision.reason == "robots_invalid_content"
    assert not oversized.allowed
    assert oversized.reason == "robots_unavailable:response_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/private/data", False),
        ("/private/public/page", True),
        ("/private/public/secret", False),
        ("/download/report.pdf", False),
        ("/download/report.pdf?preview=1", True),
        ("/public/file?secret=1", False),
        ("/equal", True),
        ("/robots.txt", True),
        ("/safe/%2e%2e/private/data", False),
        ("/caf%C3%A9", False),
        ("/%70rivate/data", False),
    ],
)
async def test_wildcards_end_anchor_longest_rule_and_allow_ties(path, allowed):
    body = """User-agent: *
Disallow: /private/
Allow: /private/public/
Disallow: /private/public/secret$
Disallow: /*.pdf$
Disallow: /*?secret=
Disallow: /equal
Allow: /equal
Disallow: /robots.txt
Disallow: /café
"""

    async def handler(request):
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await RobotsPolicy(client, user_agent="webcollect/0.1").check(
            f"https://example.com{path}"
        )
    assert decision.allowed is allowed


@pytest.mark.asyncio
async def test_matching_user_agent_groups_merge_and_override_wildcard():
    body = """User-agent: *
Disallow: /

User-agent: webcollect
Disallow: /first
Crawl-delay: 0.5

User-agent: otherbot
Disallow: /unrelated

User-agent: WebCollect
Disallow: /second
Request-rate: 1/3
"""

    async def handler(request):
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        policy = RobotsPolicy(client, user_agent="webcollect/0.1 (+contact)")
        assert not (await policy.check("https://example.com/first")).allowed
        assert not (await policy.check("https://example.com/second")).allowed
        assert (await policy.check("https://example.com/unrelated")).allowed
        assert (await policy.check("https://example.com/")).crawl_delay == 3


@pytest.mark.asyncio
async def test_product_token_does_not_accidentally_match_a_substring():
    async def handler(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /\nUser-agent: web\nAllow: /\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert not (
            await RobotsPolicy(client, user_agent="webcollect/0.1").check("https://example.com/")
        ).allowed


@pytest.mark.asyncio
async def test_invalid_numeric_extensions_do_not_abort_policy_checks():
    async def handler(request):
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /private\nRequest-rate: 1/" + "9" * 5000
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert not (
            await RobotsPolicy(client, user_agent="webcollect/0.1").check(
                "https://example.com/private"
            )
        ).allowed


@pytest.mark.asyncio
async def test_extension_records_do_not_terminate_robots_groups():
    async def handler(request):
        return httpx.Response(
            200,
            text=(
                "User-agent: webcollect\nSitemap: https://example.com/map.xml\n"
                "User-agent: otherbot\nDisallow: /private\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await RobotsPolicy(client, user_agent="webcollect/0.1").check(
            "https://example.com/private"
        )
    assert not decision.allowed
