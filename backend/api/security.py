"""Shared request security helpers for API routes."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from backend.core import settings

logger = logging.getLogger(__name__)


def parse_allowed_list(raw: str | None) -> set[str]:
    """Return a set of stripped items from a comma-separated config string."""
    try:
        if not raw:
            return set()
        return {item.strip() for item in str(raw).split(",") if item and item.strip()}
    except Exception:
        return set()


_ALLOWED_ORIGINS = parse_allowed_list(getattr(settings, "allowed_origins", None))
_ALLOWED_HOSTS = parse_allowed_list(getattr(settings, "allowed_hosts", None))


def enforce_origin_host(request: Request) -> None:
    """Best-effort origin/host check for critical FastAPI routes.

    When no allowlists are configured, this is a no-op. Otherwise, it will allow
    a request if either the full Origin header value matches an entry in
    settings.allowed_origins OR the parsed origin host / Host header matches an
    entry in settings.allowed_hosts.
    """
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    host_hdr = request.headers.get("host") or ""

    if not _ALLOWED_ORIGINS and not _ALLOWED_HOSTS:
        return

    try:
        origin_ok = False
        host_ok = False

        if origin and _ALLOWED_ORIGINS:
            origin_ok = origin in _ALLOWED_ORIGINS

        if _ALLOWED_HOSTS:
            origin_host = ""
            if origin:
                try:
                    parsed = urlparse(origin)
                    if parsed.hostname:
                        origin_host = parsed.hostname
                        if parsed.port:
                            origin_host = f"{parsed.hostname}:{parsed.port}"
                except Exception:
                    origin_host = ""

            if origin_host and origin_host in _ALLOWED_HOSTS:
                host_ok = True
            elif host_hdr and host_hdr in _ALLOWED_HOSTS:
                host_ok = True

        if not (origin_ok or host_ok):
            logger.warning(
                "SECURITY: Origin/Host blocked; allowlist origins=%s hosts=%s; got origin=%s host=%s",
                _ALLOWED_ORIGINS,
                _ALLOWED_HOSTS,
                origin,
                host_hdr,
            )
            raise HTTPException(status_code=403, detail="Origin/host not allowed")
    except HTTPException:
        raise
    except Exception:
        return
