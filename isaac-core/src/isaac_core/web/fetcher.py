from __future__ import annotations

from dataclasses import dataclass
import gzip
import http.client
import io
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from .security import validate_public_url


class WebFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    content_type: str
    body: bytes
    status: int


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PublicWebFetcher:
    def __init__(self, *, timeout: float = 10.0, max_bytes: int = 2_000_000, max_redirects: int = 4, resolver=socket.getaddrinfo):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.resolver = resolver

    def fetch(self, url: str) -> FetchResult:
        current = url
        for redirect_count in range(self.max_redirects + 1):
            target = validate_public_url(current, self.resolver)
            parsed = urlsplit(current)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection = (_PinnedHTTPSConnection(target.hostname, target.ip, target.port, self.timeout)
                          if target.scheme == "https" else http.client.HTTPConnection(target.ip, target.port, timeout=self.timeout))
            headers = {
                "Host": target.hostname if target.port in {80, 443} else f"{target.hostname}:{target.port}",
                "User-Agent": "ISAAC-ReadOnlyResearch/1.0",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
            }
            try:
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    response.read(1024)
                    if not location:
                        raise WebFetchError("Redirect omitted Location")
                    if redirect_count >= self.max_redirects:
                        raise WebFetchError("Too many redirects")
                    current = urljoin(current, location)
                    continue
                if response.status < 200 or response.status >= 300:
                    raise WebFetchError(f"Public page returned HTTP {response.status}")
                content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().casefold()
                if content_type not in {"text/html", "application/xhtml+xml", "text/plain", "application/rss+xml", "application/xml", "text/xml", "application/json"}:
                    raise WebFetchError(f"Unsupported content type: {content_type or 'unknown'}")
                length = response.getheader("Content-Length")
                if length and int(length) > self.max_bytes:
                    raise WebFetchError("Response exceeds size limit")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise WebFetchError("Response exceeds size limit")
                encoding = (response.getheader("Content-Encoding") or "").casefold().strip()
                if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                    try:
                        body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(self.max_bytes + 1)
                    except (OSError, EOFError) as error:
                        raise WebFetchError("Invalid gzip response") from error
                    if len(body) > self.max_bytes:
                        raise WebFetchError("Decompressed response exceeds size limit")
                elif encoding not in {"", "identity"}:
                    raise WebFetchError(f"Unsupported content encoding: {encoding}")
                return FetchResult(current, content_type, body, response.status)
            except (OSError, http.client.HTTPException, ssl.SSLError) as error:
                raise WebFetchError(f"Public page request failed: {type(error).__name__}") from error
            finally:
                connection.close()
        raise WebFetchError("Too many redirects")
