"""SSRF-safe URL policy for future network boundaries."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def validate_public_https_url(url: str, *, allowed_hosts: frozenset[str]) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise ValueError("URL must be HTTPS without credentials")
    if host not in {item.lower().rstrip(".") for item in allowed_hosts}:
        raise ValueError("URL host is not allowlisted")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("URL address is not public")
    return url
