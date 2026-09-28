"""Tests for enforce_origin_host origin/host allowlist enforcement."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest
from fastapi import HTTPException, Request

from backend.api import security


@pytest.fixture(autouse=True)
def allowlists(monkeypatch):
    monkeypatch.setattr(
        security, "_ALLOWED_ORIGINS", {"http://localhost:8100", "http://aina-veris:8100"}
    )
    monkeypatch.setattr(
        security, "_ALLOWED_HOSTS", {"localhost:8100", "aina-veris:8100"}
    )


def _request(origin=None, referer=None, host="localhost:8100"):
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    if host is not None:
        headers.append((b"host", host.encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def test_no_allowlists_is_noop(monkeypatch):
    monkeypatch.setattr(security, "_ALLOWED_ORIGINS", set())
    monkeypatch.setattr(security, "_ALLOWED_HOSTS", set())
    security.enforce_origin_host(_request(origin="http://evil.example.com"))


def test_matching_origin_allowed():
    security.enforce_origin_host(_request(origin="http://localhost:8100"))
    security.enforce_origin_host(_request(origin="http://aina-veris:8100"))


def test_origin_host_matching_allowed_hosts_allowed():
    # Origin not verbatim in ALLOWED_ORIGINS but its host:port is in ALLOWED_HOSTS.
    security.enforce_origin_host(_request(origin="http://localhost:8100/foo"))


def test_bad_origin_rejected_even_with_good_host():
    """A forged Origin must not be rescued by a valid Host header."""
    with pytest.raises(HTTPException) as exc:
        security.enforce_origin_host(_request(origin="http://evil.example.com"))
    assert exc.value.status_code == 403


def test_rebinding_blocked():
    """Both origin and host foreign -> blocked."""
    with pytest.raises(HTTPException):
        security.enforce_origin_host(
            _request(origin="http://evil.example.com", host="evil.example.com")
        )


def test_no_origin_good_host_allowed():
    # Non-browser tooling (curl etc.) sends no Origin; Host still checked.
    security.enforce_origin_host(_request(host="localhost:8100"))
    security.enforce_origin_host(_request(host="aina-veris:8100"))


def test_no_origin_bad_host_rejected():
    with pytest.raises(HTTPException):
        security.enforce_origin_host(_request(host="evil.example.com"))


def test_referer_used_when_no_origin():
    security.enforce_origin_host(_request(referer="http://localhost:8100/x", host="anything"))
    with pytest.raises(HTTPException):
        security.enforce_origin_host(_request(referer="http://evil.example.com/"))


def test_empty_origin_falls_back_to_host():
    security.enforce_origin_host(_request(origin="", host="localhost:8100"))
