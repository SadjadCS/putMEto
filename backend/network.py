"""Small, bounded HTTP client for public job sources (AI has separate networking)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


class SourceError(ValueError):
    """A public source cannot safely be reached or has an invalid response."""


def validate_url_shape(url: str) -> str:
    """Reject non-web URLs and obvious local addresses without doing network I/O."""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise SourceError("Use a complete http:// or https:// public website URL.")
        if parts.username is not None or parts.password is not None:
            raise SourceError("Website URLs cannot contain a username or password.")
        if parts.port not in {None, 80, 443}:
            raise SourceError("Job websites must use standard HTTP or HTTPS ports.")
        host = parts.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
            raise SourceError("Use a public job website, not a local network address.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise SourceError("Use a public website with a complete hostname.")
        else:
            if not address.is_global:
                raise SourceError("Private and reserved network addresses are not allowed.")
        if any(ord(character) < 32 for character in url) or "\\" in url:
            raise SourceError("The website URL contains invalid characters.")
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("The website URL is invalid.") from exc


async def validate_public_url(url: str) -> str:
    normalized = validate_url_shape(url)
    parts = urlsplit(normalized)
    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), 0, socket.SOCK_STREAM),
            timeout=6,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise SourceError("The website hostname could not be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise SourceError("This website resolves to a private or reserved network address.")
    return normalized


async def fetch_json(url: str, max_bytes: int = 12_000_000):
    """Validate every redirect, avoid ambient proxies, and bound time and body size."""
    try:
        async with asyncio.timeout(50):
            async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), follow_redirects=False, trust_env=False) as client:
                for _ in range(5):
                    url = await validate_public_url(url)
                    async with client.stream("GET", url, headers={"Accept": "application/json", "User-Agent": "PutMeTo/1.0 (local personal job search)"}) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise SourceError("The source returned an empty redirect.")
                            url = urljoin(url, location)
                            continue
                        if response.status_code == 429:
                            raise SourceError("This source is rate limited. Try again later.")
                        if response.status_code >= 400:
                            raise SourceError(f"The source returned HTTP {response.status_code}.")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise SourceError("The source response is too large to import.")
                        try:
                            return json.loads(body)
                        except (ValueError, UnicodeError) as exc:
                            raise SourceError("The source did not return valid JSON.") from exc
                raise SourceError("The source redirected too many times.")
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        raise SourceError("The source could not be reached. Check your connection and try again.") from exc
