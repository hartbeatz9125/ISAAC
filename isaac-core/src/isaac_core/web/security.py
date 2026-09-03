from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeWebTarget(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    ip: str


_BLOCKED_NAMES = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa")


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global and not any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved, address.is_multicast, address.is_unspecified))


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> ValidatedTarget:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeWebTarget("Only public HTTP/HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeWebTarget("URL credentials are not allowed")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname or hostname in _BLOCKED_NAMES or hostname.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeWebTarget("Local or internal hostnames are blocked")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as error:
        raise UnsafeWebTarget("Invalid URL port") from error
    if port not in {80, 443}:
        raise UnsafeWebTarget("Only standard public web ports are allowed")
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
        addresses = [str(literal)]
    except ValueError:
        try:
            answers = resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise UnsafeWebTarget("DNS resolution failed") from error
        addresses = sorted({answer[4][0].split("%", 1)[0] for answer in answers})
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise UnsafeWebTarget("Destination resolves to a blocked or non-public address")
    return ValidatedTarget(url=url, scheme=parsed.scheme.lower(), hostname=hostname, port=port, ip=addresses[0])
