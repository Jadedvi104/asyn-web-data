"""Response size limits, transport classification, and stream cleanup."""

import asyncio
import gzip

import httpx
import pytest

from webcollect.fetcher import FetchError, fetch_response


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.read_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_success_and_redirects_are_returned_without_following():
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://outside.example/"}, content=b"go")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        result = await fetch_response(client, "https://example.com/", max_bytes=100)
    assert result.status == 302
    assert result.body == b"go"
    assert result.headers["location"] == "https://outside.example/"
    assert result.latency_seconds >= 0
    assert seen == ["https://example.com/"]


@pytest.mark.asyncio
async def test_declared_size_is_checked_before_reading():
    stream = ChunkStream([b"too much"])

    async def handler(request):
        return httpx.Response(200, headers={"content-length": "8"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as raised:
            await fetch_response(client, "https://example.com/", max_bytes=4)
    assert raised.value.code == "response_too_large"
    assert raised.value.status == 200
    assert not raised.value.retryable
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_streamed_size_is_checked_and_stream_closed():
    stream = ChunkStream([b"123", b"456", b"789"])

    async def handler(request):
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError, match="byte limit"):
            await fetch_response(client, "https://example.com/", max_bytes=4)
    assert stream.read_count == 2
    assert stream.closed


@pytest.mark.asyncio
async def test_compressed_response_limit_uses_decoded_bytes():
    compressed = gzip.compress(b"x" * 1000)

    async def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=compressed)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as raised:
            await fetch_response(client, "https://example.com/", max_bytes=100)
    assert raised.value.code == "response_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error")]
)
async def test_transport_errors_are_retryable(error, code):
    async def handler(request):
        raise error("fixture failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as raised:
            await fetch_response(client, "https://example.com/", max_bytes=100)
    assert raised.value.code == code
    assert raised.value.retryable


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_response():
    started = asyncio.Event()

    class WaitingStream(ChunkStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Future()
            yield b"unreachable"

    stream = WaitingStream([])

    async def handler(request):
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(fetch_response(client, "https://example.com/", max_bytes=100))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
async def test_live_compressed_streams_are_bounded_and_closed(encoding):
    import zlib

    payload = b"x" * 1_000_000
    compressed = gzip.compress(payload) if encoding == "gzip" else zlib.compress(payload)
    stream = ChunkStream([compressed])

    async def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as raised:
            await fetch_response(client, "https://example.com/", max_bytes=100)
    assert raised.value.code == "response_too_large"
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoding,body",
    [
        ("gzip", b"not gzip"),
        ("gzip", gzip.compress(b"hello")[:-3]),
        ("deflate", b"invalid"),
        ("unknown", b"bytes"),
    ],
)
async def test_malformed_or_unsupported_compression_is_a_fetch_failure(encoding, body):
    stream = ChunkStream([body])

    async def handler(request):
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as raised:
            await fetch_response(client, "https://example.com/", max_bytes=100)
    assert raised.value.code == "invalid_response"
    assert not raised.value.retryable
    assert stream.closed


@pytest.mark.asyncio
async def test_valid_compressed_stream_decodes_and_exact_ceiling_is_allowed():
    body = b"valid response"
    stream = ChunkStream([gzip.compress(body)])

    async def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_response(client, "https://example.com/", max_bytes=len(body))
    assert result.body == body
    assert stream.closed
