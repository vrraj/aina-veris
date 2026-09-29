"""MCP tool-call timeouts must actually bound the call.

Regression coverage for the case where a streamed/long-running MCP tool
(e.g. tavily_research) kept the HTTP request alive: future.result() fired
its TimeoutError inside `with ThreadPoolExecutor(...)`, whose __exit__
waited on the still-running worker thread, so the error never surfaced.
"""

import asyncio
import os
import time

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.integrations.mcp import client as mcp_client


def test_run_coro_sync_timeout_without_running_loop():
    async def slow():
        await asyncio.sleep(60)

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        mcp_client._run_coro_sync(slow(), timeout=0.2)
    assert time.monotonic() - t0 < 5


def test_run_coro_sync_timeout_with_running_loop():
    """Inside a running loop the executor path is used; the wait_for inside
    the worker cancels the coroutine, so shutdown(wait=False) must not wait
    on a stuck worker thread."""

    async def slow():
        await asyncio.sleep(60)

    async def driver():
        return mcp_client._run_coro_sync(slow(), timeout=0.2)

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(driver())
    assert time.monotonic() - t0 < 5


def test_run_coro_sync_returns_result_before_timeout():
    async def fast():
        return {"ok": True}

    assert mcp_client._run_coro_sync(fast(), timeout=5.0) == {"ok": True}


def test_call_mcp_tool_sync_uses_settings_default(monkeypatch):
    seen = {}

    async def fake_call(server, url, name, args, *, tool_runtime=None, timeout=None):
        seen["timeout"] = timeout
        return "done"

    monkeypatch.setattr(mcp_client, "call_mcp_tool", fake_call)
    monkeypatch.setattr(
        mcp_client.settings, "mcp_tool_timeout_seconds", 12.5, raising=False
    )
    result = mcp_client.call_mcp_tool_sync("srv", "http://x/mcp", "tool", {})
    assert result == "done"
    assert seen["timeout"] == 12.5


def test_call_mcp_tool_sync_times_out_cancelled_call(monkeypatch):
    async def slow_call(server, url, name, args, *, tool_runtime=None, timeout=None):
        await asyncio.sleep(60)

    monkeypatch.setattr(mcp_client, "call_mcp_tool", slow_call)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        mcp_client.call_mcp_tool_sync("srv", "http://x/mcp", "tool", {}, timeout=0.2)
    assert time.monotonic() - t0 < 5
