"""Cached, conservative per-origin robots.txt enforcement."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from webcollect.fetcher import FetchError, FetchResult, fetch_response
from webcollect.urls import normalize_url, origin_for

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PERCENT_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    reason: str
    crawl_delay: float = 0.0


@dataclass(frozen=True, slots=True)
class _Permission:
    allow: bool
    pattern: str
    anchored: bool
    specificity: int

    def matches(self, target: str) -> bool:
        # Match only '*' and a terminal '$', avoiding regex backtracking on site input.
        pieces = self.pattern.split("*")
        if len(pieces) == 1:
            return target == self.pattern if self.anchored else target.startswith(self.pattern)
        if not target.startswith(pieces[0]):
            return False
        position = len(pieces[0])
        for piece in pieces[1:-1]:
            found = target.find(piece, position)
            if found < 0:
                return False
            position = found + len(piece)
        if self.anchored:
            return target.endswith(pieces[-1]) and len(target) - len(pieces[-1]) >= position
        return target.find(pieces[-1], position) >= 0


@dataclass(frozen=True, slots=True)
class _Rules:
    allowed: bool
    reason: str
    permissions: tuple[_Permission, ...] = ()
    crawl_delay: float = 0.0


def _comparison_path(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in _UNRESERVED else match.group(0).upper()

    value = quote(value, safe="/%?:@!$&'()*+,;=-._~")
    return _PERCENT_ESCAPE.sub(replace, value)


def _parse_policy(text: str, user_agent: str) -> tuple[tuple[_Permission, ...], float]:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    directives: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if any(record in {"allow", "disallow"} for record, _ in directives):
                groups.append((agents, directives))
                agents, directives = [], []
            if value:
                agents.append(value.lower())
        elif agents:
            directives.append((key, value))
    if agents:
        groups.append((agents, directives))
    # Specific product tokens take priority over '*'; repeated matching groups merge.
    product_token = user_agent.split(maxsplit=1)[0].split("/", 1)[0].lower()
    selected = [records for names, records in groups if product_token in names]
    if not selected:
        selected = [records for names, records in groups if "*" in names]
    permissions: list[_Permission] = []
    delay = 0.0
    for records in selected:
        extension_parser = RobotFileParser()
        try:
            extension_parser.parse(
                ["User-agent: *", *(f"{key}: {value}" for key, value in records)]
            )
            rate = extension_parser.request_rate("*")
            if rate is not None and rate.requests > 0:
                interval = rate.seconds / rate.requests
                if math.isfinite(interval):
                    delay = max(delay, interval)
        except (ValueError, OverflowError):
            # Malformed or excessive extension numbers must not abort policy checks.
            pass
        for key, value in records:
            if key == "crawl-delay":
                try:
                    candidate = float(value)
                except ValueError:
                    continue
                if math.isfinite(candidate) and candidate >= 0:
                    delay = max(delay, candidate)
            if key not in {"allow", "disallow"} or not value.startswith("/"):
                continue
            anchored = value.endswith("$")
            pattern = _comparison_path(value[:-1] if anchored else value)
            specificity = len(re.findall(r"%[0-9A-F]{2}|.", pattern.replace("*", "")))
            permissions.append(_Permission(key == "allow", pattern, anchored, specificity))
    return tuple(permissions), delay


class RobotsPolicy:
    """Load robots once per origin and deny when its policy is unavailable.

    The optional fetch callback lets the coordinator apply the same per-host
    concurrency and request pacing controls to robots requests as page requests.
    Redirects are denied to keep robots requests on the approved destination.
    Cache lifetime is one policy instance (normally one crawl run).
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        max_bytes: int = 512_000,
        fetch: Callable[[str, int], Awaitable[FetchResult]] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("A descriptive User-Agent is required")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.client = client
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self._fetch = fetch
        self._cache: dict[str, _Rules] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _load(self, origin: str) -> _Rules:
        url = f"{origin}/robots.txt"
        try:
            if self._fetch is None:
                result = await fetch_response(self.client, url, max_bytes=self.max_bytes)
            else:
                result = await self._fetch(url, self.max_bytes)
        except FetchError as exc:
            return _Rules(False, f"robots_unavailable:{exc.code}")
        if result.status in {404, 410}:
            return _Rules(True, "robots_not_found")
        if result.status in {401, 403}:
            return _Rules(False, "robots_access_denied")
        if 300 <= result.status < 400:
            return _Rules(False, "robots_redirect_denied")
        if not 200 <= result.status < 300:
            return _Rules(False, f"robots_unavailable:http_{result.status}")
        if any(
            kind in result.headers.get("content-type", "").lower()
            for kind in ("text/html", "application/xhtml+xml")
        ):
            return _Rules(False, "robots_invalid_content")
        text = result.body.decode("utf-8-sig", errors="replace")
        permissions, delay = _parse_policy(text, self.user_agent)
        return _Rules(True, "robots_allowed", permissions, delay)

    async def check(self, url: str) -> RobotsDecision:
        """Return a longest-match rule decision and the site-supplied interval."""
        url = normalize_url(url)
        origin = origin_for(url)
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                self._cache[origin] = await self._load(origin)
            rules = self._cache[origin]
        if not rules.allowed:
            return RobotsDecision(False, rules.reason)
        parts = urlsplit(url)
        target = _comparison_path(parts.path + (f"?{parts.query}" if parts.query else ""))
        matching = [
            (rule.specificity, rule.allow) for rule in rules.permissions if rule.matches(target)
        ]
        allowed = parts.path == "/robots.txt" or not matching or max(matching)[1]
        return RobotsDecision(
            allowed, rules.reason if allowed else "robots_disallowed", rules.crawl_delay
        )
