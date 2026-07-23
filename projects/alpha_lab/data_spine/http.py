"""Small injectable HTTP client that never persists authorization material."""

from __future__ import annotations

import gzip
import zlib
from dataclasses import dataclass
from typing import Mapping
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    headers: Mapping[str, str]


def decode_content(body: bytes, headers: Mapping[str, str]) -> bytes:
    encoding = str(headers.get("content-encoding") or "").lower().strip()
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body


def get(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 90.0) -> Response:
    request = Request(url, headers=dict(headers or {}), method="GET")
    with urlopen(request, timeout=timeout) as response:
        response_headers = {
            str(key).lower(): str(value) for key, value in response.headers.items()
        }
        body = decode_content(response.read(), response_headers)
        return Response(
            body=body,
            status=int(getattr(response, "status", 200)),
            headers=response_headers,
        )


def head(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 30.0) -> Response:
    request = Request(url, headers=dict(headers or {}), method="HEAD")
    with urlopen(request, timeout=timeout) as response:
        return Response(
            body=b"",
            status=int(getattr(response, "status", 200)),
            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
        )
