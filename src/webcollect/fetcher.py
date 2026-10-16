"""Bounded, cancellable HTTP fetching without automatic redirect traversal."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from time import perf_counter

import httpx

from webcollect.urls import normalize_url


class FetchError(Exception):
    """A classified fetch failure suitable for persistence and bounded retries."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]
    latency_seconds: float


async def _read_body(response: httpx.Response, max_bytes: int) -> bytes:
    """Keep the decoded buffer bounded, including compressed expansion."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"", "identity", "gzip", "x-gzip", "deflate"}:
        raise FetchError(
            "invalid_response",
            f"Unsupported Content-Encoding: {encoding}",
            status=response.status_code,
        )
    # A transport may supply an already-read response (notably MockTransport).
    if response.is_stream_consumed:
        if len(response.content) > max_bytes:
            raise FetchError(
                "response_too_large", "Response exceeds the byte limit", status=response.status_code
            )
        return response.content
    decoder = None
    if encoding in {"gzip", "x-gzip", "deflate"}:
        decoder = zlib.decompressobj(
            16 + zlib.MAX_WBITS if encoding != "deflate" else zlib.MAX_WBITS
        )
    body = bytearray()
    try:
        async for chunk in response.aiter_raw(chunk_size=min(65_536, max_bytes + 1)):
            if decoder is not None:
                # max_length prevents a small compressed chunk expanding without a bound.
                chunk = decoder.decompress(chunk, max_bytes - len(body) + 1)
            if len(body) + len(chunk) > max_bytes:
                raise FetchError(
                    "response_too_large",
                    "Response exceeds the byte limit",
                    status=response.status_code,
                )
            body.extend(chunk)
        if decoder is not None and (not decoder.eof or decoder.unused_data):
            raise FetchError(
                "invalid_response",
                "Incomplete or trailing compressed response data",
                status=response.status_code,
            )
    except zlib.error as exc:
        raise FetchError(
            "invalid_response", "Response decoding failed", status=response.status_code
        ) from exc
    return bytes(body)


async def fetch_response(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> FetchResult:
    """Stream one response and enforce the limit on decoded response bytes.

    Response statuses (including redirects and HTTP errors) are returned for the
    caller to handle. Context management closes the stream on failure/cancellation.
    Compression is discouraged; gzip/deflate are decoded within the byte ceiling.
    Other content encodings, malformed streams, and concatenated gzip are rejected.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    url = normalize_url(url)
    started = perf_counter()
    try:
        async with client.stream(
            "GET", url, follow_redirects=False, headers={"Accept-Encoding": "identity"}
        ) as response:
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = -1
                if declared_length > max_bytes:
                    raise FetchError(
                        "response_too_large",
                        f"Response exceeds the {max_bytes}-byte limit",
                        status=response.status_code,
                    )
            body = await _read_body(response, max_bytes)
            return FetchResult(
                url=url,
                status=response.status_code,
                body=body,
                headers=dict(response.headers),
                latency_seconds=perf_counter() - started,
            )
    except httpx.TimeoutException as exc:
        raise FetchError("timeout", "Request timed out", retryable=True) from exc
    except httpx.DecodingError as exc:
        raise FetchError("invalid_response", "Response decoding failed") from exc
    except httpx.RequestError as exc:
        raise FetchError("network_error", str(exc), retryable=True) from exc
