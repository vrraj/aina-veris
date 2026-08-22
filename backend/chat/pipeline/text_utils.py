"""Small text cleanup helpers shared by chat pipeline stages."""

import re


def strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fences if present."""
    if not isinstance(text, str):
        return ""
    s = text.strip()
    if s.startswith("```json") and s.endswith("```"):
        return s[7:-3].strip()
    if s.startswith("```") and s.endswith("```"):
        return s[3:-3].strip()
    return s


def strip_trailing_sources_block(text: str) -> str:
    """Remove a trailing Sources block from an assistant message, if present."""
    try:
        s = (text or "").rstrip()
        match = re.search(r"(?:\r?\n)(?:<sources>Sources</sources>|Sources|sources):\s*\r?\n[\s\S]*\Z", s)
        if match:
            s = s[:match.start()]
        return s.rstrip()
    except Exception:
        return text or ""
