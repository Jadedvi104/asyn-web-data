"""Normalize HTTP URLs and enforce an exact-host domain allowlist."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_INVALID_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_PERCENT_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _normalize_host(host: str) -> str:
    host = host.removesuffix(".").lower()
    if not host:
        raise ValueError("URL must include a hostname")
    if ":" in host:
        # Zone identifiers can change the destination interface and are unsupported.
        if "%" in host:
            raise ValueError("IPv6 zone identifiers are unsupported")
        try:
            return ipaddress.IPv6Address(host).compressed
        except ValueError as exc:
            raise ValueError("Invalid IPv6 hostname") from exc
    try:
        host = httpx.URL(f"http://{host}").raw_host.decode("ascii")
    except (UnicodeError, httpx.InvalidURL) as exc:
        raise ValueError("Invalid hostname") from exc
    if len(host) > 253 or any(not _LABEL.fullmatch(label) for label in host.split(".")):
        raise ValueError("Invalid hostname")
    return host


def _normalize_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in _UNRESERVED else match.group(0).upper()

    return _PERCENT_ESCAPE.sub(replace, value)


def _remove_dot_segments(path: str) -> str:
    segments: list[str] = []
    source = path.split("/")
    for index, segment in enumerate(source):
        if segment == ".":
            if index == len(source) - 1:
                segments.append("")
        elif segment == "..":
            if len(segments) > 1:
                segments.pop()
            if index == len(source) - 1:
                segments.append("")
        else:
            segments.append(segment)
    return "/".join(segments) or "/"


def normalize_url(url: str, base: str | None = None) -> str:
    """Return a stable, fragment-free HTTP(S) URL, or raise ``ValueError``.

    Hosts use lowercase IDNA and explicit default ports disappear. Query order and
    repeated path slashes are preserved. Relative references require a valid base.
    Credentials, backslashes, malformed escapes, controls, and whitespace are refused.
    """
    if not isinstance(url, str):
        raise ValueError("URL must be a string")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("URL cannot contain whitespace, controls, or backslashes")
    if _INVALID_PERCENT.search(url):
        raise ValueError("URL contains an invalid percent escape")
    if base is not None:
        url = urljoin(normalize_url(base), url)
    if not url:
        raise ValueError("URL cannot be empty")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            raise ValueError("Only HTTP and HTTPS URLs are supported")
        if parts.username is not None or parts.password is not None:
            raise ValueError("URL credentials are unsupported")
        host = _normalize_host(parts.hostname or "")
        port = parts.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if parts.netloc.startswith("[") and not re.fullmatch(
        r"(?::[0-9]+)?", parts.netloc.partition("]")[2]
    ):
        raise ValueError("Invalid IPv6 authority")
    if parts.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ValueError("URL contains an invalid port")
    scheme = parts.scheme.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        netloc = f"{netloc}:{port}"
    path = quote(
        _remove_dot_segments(_normalize_escapes(parts.path or "/")), safe="/%:@!$&'()*+,;=-._~"
    )
    query = quote(_normalize_escapes(parts.query), safe="/%?:@!$&'()*+,;=-._~")
    return urlunsplit((scheme, netloc, path, query, ""))


def host_for(url: str) -> str:
    """Return the normalized hostname, without a port or IPv6 brackets."""
    return urlsplit(normalize_url(url)).hostname or ""


def origin_for(url: str) -> str:
    """Return the normalized scheme and authority for a URL."""
    parts = urlsplit(normalize_url(url))
    return f"{parts.scheme}://{parts.netloc}"


def normalize_domain(domain: str) -> str:
    """Normalize a bare hostname or IP address; ports and wildcards are refused."""
    if not isinstance(domain, str) or not domain or "/" in domain or "?" in domain:
        raise ValueError("Allowlist entries must be bare hostnames")
    if "#" in domain or "@" in domain:
        raise ValueError("Allowlist entries must be bare hostnames")
    if domain.startswith("[") and domain.endswith("]"):
        domain = domain[1:-1]
    return _normalize_host(domain)


def is_allowed(url: str, domains: Iterable[str]) -> bool:
    """Allow only exact normalized hostname matches; subdomains are explicit."""
    try:
        hostname = host_for(url)
    except ValueError:
        return False
    for domain in domains:
        try:
            if hostname == normalize_domain(domain):
                return True
        except ValueError:
            continue
    return False
