"""Validated configuration saved with every crawl job."""

import math
from dataclasses import asdict, dataclass, field

from webcollect.urls import is_allowed, normalize_domain, normalize_url


@dataclass
class CrawlConfig:
    seeds: list[str]
    allow_domains: list[str]
    depth: int = 2
    concurrency: int = 4
    per_host_concurrency: int = 2
    rps: float = 1.0
    max_pages: int = 100
    queue_size: int = 100
    timeout: float = 10.0
    max_response_bytes: int = 2_097_152
    max_attempts: int = 3
    user_agent: str = "webcollect/0.1"
    _validated: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if not self.seeds or not self.allow_domains:
            raise ValueError("At least one seed and explicit allow-domain are required")
        for name in (
            "concurrency",
            "per_host_concurrency",
            "max_pages",
            "queue_size",
            "max_response_bytes",
            "max_attempts",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.depth, int) or isinstance(self.depth, bool) or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        for name in ("rps", "timeout"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if not self.user_agent.strip() or any(c in self.user_agent for c in "\r\n"):
            raise ValueError("user_agent must be a nonempty single line")
        self.allow_domains = list(dict.fromkeys(normalize_domain(d) for d in self.allow_domains))
        self.seeds = list(dict.fromkeys(normalize_url(s) for s in self.seeds))
        if len(self.seeds) > self.max_pages:
            raise ValueError("max_pages must be at least the number of unique seeds")
        if any(not is_allowed(seed, self.allow_domains) for seed in self.seeds):
            raise ValueError("Every seed must belong to an explicitly allowed domain")
        self._validated = True

    def to_dict(self) -> dict:
        values = asdict(self)
        values.pop("_validated")
        return values
