"""URL and exact-host allowlist invariants."""

import pytest

from webcollect.urls import host_for, is_allowed, normalize_domain, normalize_url, origin_for


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.COM:443#fragment", "https://example.com/"),
        ("http://example.com:80/a/../b/./?z=2&a=1", "http://example.com/b/?z=2&a=1"),
        ("http://example.com:8080/a//b", "http://example.com:8080/a//b"),
        ("https://BÜCHER.example/ä", "https://xn--bcher-kva.example/%C3%A4"),
        ("http://[2001:0db8::1]:80/", "http://[2001:db8::1]/"),
        ("https://example.com/a/..", "https://example.com/"),
        ("https://example.com/../../a", "https://example.com/a"),
        ("https://example.com./", "https://example.com/"),
    ],
)
def test_normalize_url(value, expected):
    assert normalize_url(value) == expected
    assert normalize_url(expected) == expected


def test_relative_links_resolve_against_normalized_base():
    assert normalize_url("../help#section", "https://EXAMPLE.com/docs/start") == (
        "https://example.com/help"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ftp://example.com/",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https:///missing",
        "https://example.com:99999/",
        "https://example.com:0/",
        "https://example.com:/",
        "https://exa mple.com/",
        "https://example.com/\nnext",
        "https://example.com\\@evil.com/",
        "https://example.com/%xy",
        "https://-bad.example/",
        "http://[fe80::1%25eth0]/",
        "https://example.com../",
        "//example.com/",
    ],
)
def test_invalid_urls_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_url(value)


def test_exact_hostname_allowlist():
    assert is_allowed("https://EXAMPLE.com:8080/path", ["example.com"])
    assert is_allowed("http://xn--bcher-kva.example/", ["bücher.example"])
    assert is_allowed("http://[::1]:8000/", ["::1"])
    assert not is_allowed("https://sub.example.com/", ["example.com"])
    assert not is_allowed("https://example.com.evil.test/", ["example.com"])
    assert not is_allowed("https://user@example.com/", ["example.com"])
    assert not is_allowed("file:///example.com", ["example.com"])
    assert not is_allowed("https://example.com/", ["*.example.com", "example.com:80"])


def test_host_and_origin_helpers():
    assert host_for("http://EXAMPLE.com:8000/path") == "example.com"
    assert origin_for("HTTPS://Example.com:443/a?b=c") == "https://example.com"
    assert origin_for("http://[::1]:8000/a") == "http://[::1]:8000"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "example.com/path",
        "*.example.com",
        "example.com:80",
        "user@example.com",
    ],
)
def test_allowlist_domain_validation(value):
    with pytest.raises(ValueError):
        normalize_domain(value)


def test_allowlist_domain_normalization():
    assert normalize_domain("BÜCHER.example.") == "xn--bcher-kva.example"
    assert normalize_domain("2001:0db8::1") == "2001:db8::1"


def test_normalization_matches_httpx_idna_and_decodes_unreserved_escapes():
    assert normalize_url("https://faß.de/%7euser/%2E%2E/about?a=%61&b=%2f") == (
        "https://xn--fa-hia.de/about?a=a&b=%2F"
    )
    assert normalize_domain("faß.de") == "xn--fa-hia.de"


def test_ipv6_leading_zero_port_normalizes():
    assert normalize_url("http://[::1]:0080/path") == "http://[::1]/path"
    with pytest.raises(ValueError):
        normalize_url("http://[::1]junk/")
