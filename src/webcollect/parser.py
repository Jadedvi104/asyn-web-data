"""Extract public HTML metadata without fetching linked resources."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from webcollect.urls import normalize_url


def _link(reference: Any, base: str) -> str | None:
    if not isinstance(reference, str):
        return None
    try:
        return normalize_url(reference.strip(), base)
    except ValueError:
        return None


def parse_html(html: bytes, url: str) -> dict[str, Any]:
    """Extract normalized links, title, description, and a canonical URL."""
    url = normalize_url(url)
    soup = BeautifulSoup(html, "html.parser")
    base = url
    base_element = soup.find("base", href=True)
    if base_element is not None:
        base = _link(base_element.get("href"), url) or url
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    description = ""
    for meta in soup.find_all("meta", attrs={"name": True}):
        if str(meta.get("name", "")).lower() == "description":
            description = " ".join(str(meta.get("content", "")).split())
            break
    canonical = url
    for element in soup.find_all("link", href=True):
        relation = element.get("rel", [])
        if isinstance(relation, str):
            relation = relation.split()
        if "canonical" in [str(value).lower() for value in relation]:
            canonical = _link(element.get("href"), base) or url
            break
    outbound: list[str] = []
    seen: set[str] = set()
    for element in soup.find_all(["a", "area"], href=True):
        link = _link(element.get("href"), base)
        if link is not None and link not in seen:
            outbound.append(link)
            seen.add(link)
    return {
        "url": url,
        "canonical_url": canonical,
        "title": title,
        "description": description,
        "outbound_links": outbound,
    }
