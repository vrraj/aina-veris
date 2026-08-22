"""Normalized contracts emitted by MCP result adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ToolSource:
    """A source record that can be cited in a final chat response."""

    title: str
    url: str
    snippet: str = ""
    provider: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class NormalizedToolResult:
    """Provider-specific MCP data converted to the application's stable contract."""

    data: Any
    sources: List[ToolSource] = field(default_factory=list)

