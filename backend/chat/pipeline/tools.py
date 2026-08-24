"""Tool parsing, registry, and artifact helpers for chat pipeline stages."""

from typing import Any, Dict, List
import json
import logging
import re
from pathlib import Path

import bleach
import yaml

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_CACHE: Dict[str, Dict[str, Any]] = {}


def _extract_nested_value(obj: Any, key: str) -> str | None:
    """Recursively search for a string value under `key` inside dict/list structures."""
    if not key:
        return None
    if isinstance(obj, dict):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        for candidate in obj.values():
            nested = _extract_nested_value(candidate, key)
            if nested:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _extract_nested_value(item, key)
            if nested:
                return nested
    return None

# --- Tool-call parsing helpers (module-level, pure) ---
def extract_tool_calls(resp: Any) -> List[Dict[str, Any]]:
    """Extract tool/function calls from a Responses API object or dict.
    Returns a list of {name, args, id}.
    """
    # Unwrap adapter-style responses first (e.g., AdapterResponse from llm_adapter)
    # so we always inspect the provider-native object for tool_calls.
    base = getattr(resp, "adapter_response", resp)

    def _dedup(_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Best-effort deduplication across providers/wrappers.
        Dedup key prefers call id when present, otherwise falls back to (name, args).
        """
        out: List[Dict[str, Any]] = []
        seen: set[tuple] = set()
        for c in _calls:
            try:
                name = (c.get("name") or "").strip()
                cid = (c.get("id") or "").strip()
                args = c.get("args")
                if isinstance(args, str):
                    akey = args
                elif isinstance(args, dict):
                    try:
                        akey = json.dumps(args, sort_keys=True, ensure_ascii=False)
                    except Exception:
                        akey = str(args)
                else:
                    akey = str(args)
                key = (cid,) if cid else (name, akey)
                if name and key not in seen:
                    seen.add(key)
                    out.append({"name": name, "args": args, "id": c.get("id")})
            except Exception:
                # If anything goes wrong, keep the call to avoid dropping tool execution.
                out.append(c)
        return out

    try:
        # If it's already a normalized response, extract tool_calls directly
        if isinstance(resp, dict) and resp.get("tool_calls"):
            calls = list(resp.get("tool_calls") or [])
        else:
            # For raw responses, try to extract tool_calls directly
            if hasattr(base, "tool_calls"):
                calls = list(base.tool_calls or [])
            elif isinstance(base, dict):
                calls = list(base.get("tool_calls") or [])
            else:
                calls = []
    except Exception:
        calls = []

    calls = [c for c in calls if c.get("name")]
    # Deduplicate calls
    deduped = _dedup(calls)

    return deduped


def parse_tool_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _tool_registry_path(settings_obj: Any) -> Path:
    raw_path = str(getattr(settings_obj, "tool_registry_path", "") or "").strip() or "prompts/tool_registry.yaml"
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / candidate


def clear_tool_registry_cache(path: str | None = None) -> int:
    """Clear cached tool registry entries and return number removed."""
    if path:
        key = str(path)
        return 1 if _TOOL_REGISTRY_CACHE.pop(key, None) is not None else 0
    removed = len(_TOOL_REGISTRY_CACHE)
    _TOOL_REGISTRY_CACHE.clear()
    return removed


def _load_tool_registry(settings_obj: Any) -> Dict[str, Dict[str, Any]]:
    path = _tool_registry_path(settings_obj)
    cache_key = str(path)
    try:
        mtime = float(path.stat().st_mtime) if path.exists() else -1.0
    except Exception:
        mtime = -1.0

    cached = _TOOL_REGISTRY_CACHE.get(cache_key)
    if isinstance(cached, dict):
        try:
            if float(cached.get("mtime", -2.0)) == mtime and isinstance(cached.get("value"), dict):
                return dict(cached.get("value") or {})
        except Exception:
            pass

    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}

    tools = data.get("tools") if isinstance(data, dict) else []
    if not isinstance(tools, list):
        tools = []

    tool_entries: List[Dict[str, Any]] = []
    by_name: Dict[str, Dict[str, Any]] = {}
    for item in tools:
        if not isinstance(item, dict):
            continue
        tool_entries.append(item)
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        by_name[name] = item

    # Load MCP servers
    mcp_servers = data.get("mcp_servers") if isinstance(data, dict) else {}
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    artifact_injection = data.get("artifact_injection") if isinstance(data, dict) else {}
    if not isinstance(artifact_injection, dict):
        artifact_injection = {}

    loaded = {
        "tools_by_name": by_name,
        "artifact_injection": artifact_injection,
        "mcp_servers": mcp_servers,
        "tool_entries": tool_entries,
    }
    _TOOL_REGISTRY_CACHE[cache_key] = {"mtime": mtime, "value": loaded}
    return loaded


def _extract_artifacts_from_tool_outputs(
    tool_outputs_list: List[Dict[str, Any]],
    tool_registry: Dict[str, Any],
) -> List[Dict[str, str]]:
    artifacts: List[Dict[str, str]] = []
    default_max_artifact_chars = 120000
    placeholder_re = re.compile(r"^\{\{ARTIFACT:[A-Za-z0-9_.:-]{1,64}\}\}$")
    artifact_injection = tool_registry.get("artifact_injection") if isinstance(tool_registry, dict) else {}
    if not isinstance(artifact_injection, dict):
        artifact_injection = {}
    if not bool(artifact_injection.get("enabled", True)):
        return artifacts

    security_cfg = artifact_injection.get("security") if isinstance(artifact_injection, dict) else {}
    if not isinstance(security_cfg, dict):
        security_cfg = {}

    try:
        max_artifact_chars = int(security_cfg.get("max_artifact_chars", default_max_artifact_chars) or default_max_artifact_chars)
    except Exception:
        max_artifact_chars = default_max_artifact_chars
    if max_artifact_chars <= 0:
        max_artifact_chars = default_max_artifact_chars

    allowed_artifact_types = {
        str(v).strip().lower()
        for v in (security_cfg.get("allowed_artifact_types") or ["svg"])
        if isinstance(v, str) and str(v).strip()
    }
    if not allowed_artifact_types:
        allowed_artifact_types = {"svg"}

    allowed_injection_modes = {
        str(v).strip().lower()
        for v in (security_cfg.get("allowed_injection_modes") or ["verbatim"])
        if isinstance(v, str) and str(v).strip()
    }
    if not allowed_injection_modes:
        allowed_injection_modes = {"verbatim"}

    enforce_placeholder_format = bool(security_cfg.get("enforce_placeholder_format", True))

    raw_allowed = artifact_injection.get("allowed_tools")
    allowed_artifact_tools = {
        str(n).strip()
        for n in (raw_allowed or [])
        if isinstance(n, str) and str(n).strip()
    }

    tools_by_name = tool_registry.get("tools_by_name") if isinstance(tool_registry, dict) else {}
    if not isinstance(tools_by_name, dict):
        tools_by_name = {}

    if not tool_outputs_list:
        pass
    for t in (tool_outputs_list or []):
        tool_name = str(t.get("name") or "").strip()
        if not tool_name:
            continue
        
        # Handle MCP tools with _svg_artifact (no registry entry)
        if "_svg_artifact" in t:
            svg_payload = t.get("_svg_artifact")
            if isinstance(svg_payload, str) and svg_payload.strip():
                placeholder = str(t.get("artifact_placeholder") or "").strip()

                if not placeholder:
                    try:
                        parsed = json.loads(str(t.get("output", "")))
                        if isinstance(parsed, dict):
                            placeholder = parsed.get("artifact_placeholder", "")
                    except Exception:
                        pass
                
                if not placeholder:
                    placeholder = f"{{{{ARTIFACT:{tool_name}_svg}}}}"
                
                # Sanitize SVG
                if len(svg_payload) > max_artifact_chars:
                    continue
                else:
                    sanitized_payload = svg_payload
                    lower_payload = svg_payload.lower()
                    if (
                        "<script" in lower_payload
                        or "javascript:" in lower_payload
                        or "<foreignobject" in lower_payload
                        or re.search(r"\son[a-z]+\s*=", lower_payload) is not None
                    ):
                        continue
                    else:
                        try:
                            sanitized_payload = bleach.clean(
                                svg_payload,
                                tags=["svg", "polyline", "line", "rect", "path", "text", "g"],
                                attributes={
                                    "svg": ["width", "height", "viewBox", "role", "aria-hidden", "xmlns"],
                                    "polyline": ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "points"],
                                    "line": ["x1", "y1", "x2", "y2", "stroke", "stroke-width", "stroke-linecap"],
                                    "rect": ["x", "y", "width", "height", "fill", "rx", "ry", "stroke", "stroke-width"],
                                    "path": ["d", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"],
                                    "text": ["x", "y", "text-anchor", "fill", "font-size", "font-weight"],
                                    "g": ["transform", "fill", "stroke", "stroke-width"],
                                },
                                protocols=["http", "https"],
                                strip=True,
                            ).strip()
                            if "<svg" not in sanitized_payload.lower() or "</svg>" not in sanitized_payload.lower():
                                continue
                                sanitized_payload = ""
                        except Exception:
                            continue
                            sanitized_payload = ""
                    
                    if sanitized_payload:
                        artifacts.append(
                            {
                                "tool": tool_name,
                                "payload": sanitized_payload,
                                "placeholder": placeholder,
                                "injection_mode": "verbatim",
                                "artifact_type": "svg",
                            }
                        )
            continue
        
        # Original registry-based artifact extraction for local tools
        if allowed_artifact_tools and tool_name not in allowed_artifact_tools:
            continue
        cfg = tools_by_name.get(tool_name) or {}
        artifact_cfg = cfg.get("artifact") if isinstance(cfg, dict) else None
        if not isinstance(artifact_cfg, dict):
            continue
        if not bool(artifact_cfg.get("produces_artifact")):
            continue

        artifact_key = str(artifact_cfg.get("artifact_key") or "").strip()
        placeholder = str(artifact_cfg.get("placeholder") or "").strip()
        artifact_type = str(artifact_cfg.get("artifact_type") or "").strip().lower()
        injection_mode = str(artifact_cfg.get("injection_mode") or "").strip().lower()
        if not artifact_key:
            continue
        if artifact_type not in allowed_artifact_types:
            continue
        if injection_mode not in allowed_injection_modes:
            continue
        if enforce_placeholder_format and placeholder and not placeholder_re.match(placeholder):
            continue

        payload = ""
        out = str(t.get("output") or "")
        parsed = None
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                v = parsed.get(artifact_key)
                if isinstance(v, str) and v.strip():
                    payload = v.strip()
                else:
                    nested = _extract_nested_value(parsed, artifact_key)
                    if nested:
                        payload = nested
                    else:
                        pass
        except Exception:
            parsed = None

        if not payload and artifact_type == "svg":
            try:
                m = re.search(r"<svg\\b[\\s\\S]*?</svg>", out, flags=re.IGNORECASE)
                if m and m.group(0).strip():
                    payload = m.group(0).strip()
                else:
                    pass
            except Exception:
                payload = ""

        if payload and len(payload) > max_artifact_chars:
            payload = ""

        if payload and artifact_type == "svg":
            # Runtime hardening: reject known-unsafe patterns and sanitize SVG before injection.
            lower_payload = payload.lower()
            if (
                "<script" in lower_payload
                or "javascript:" in lower_payload
                or "<foreignobject" in lower_payload
                or re.search(r"\son[a-z]+\s*=", lower_payload) is not None
            ):
                payload = ""
            else:
                try:
                    sanitized = bleach.clean(
                        payload,
                        tags=["svg", "polyline", "line", "rect", "path", "text", "g"],
                        attributes={
                            "svg": ["width", "height", "viewBox", "role", "aria-hidden", "xmlns"],
                            "polyline": ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "points"],
                            "line": ["x1", "y1", "x2", "y2", "stroke", "stroke-width", "stroke-linecap"],
                            "rect": ["x", "y", "width", "height", "fill", "rx", "ry", "stroke", "stroke-width"],
                            "path": ["d", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"],
                            "text": ["x", "y", "text-anchor", "fill", "font-size", "font-weight"],
                            "g": ["transform", "fill", "stroke", "stroke-width"],
                        },
                        protocols=["http", "https"],
                        strip=True,
                    ).strip()
                    if "<svg" not in sanitized.lower() or "</svg>" not in sanitized.lower():
                        continue
                        payload = ""
                    else:
                        payload = sanitized
                except Exception:
                    continue
                    payload = ""

        if payload:
            artifacts.append(
                {
                    "tool": tool_name,
                    "payload": payload,
                    "placeholder": placeholder,
                    "injection_mode": injection_mode,
                    "artifact_type": artifact_type,
                }
            )
    # A compound query can cause the planner to call the same chart tool more
    # than once.  A placeholder identifies a single display location, so keep
    # only its first valid artifact instead of appending duplicate charts.
    unique_artifacts: List[Dict[str, str]] = []
    seen_locations = set()
    for artifact in artifacts:
        location = (
            str(artifact.get("tool") or ""),
            str(artifact.get("placeholder") or ""),
        )
        if location in seen_locations:
            continue
        seen_locations.add(location)
        unique_artifacts.append(artifact)
    return unique_artifacts


def _inject_registered_artifacts(text: str, artifacts: List[Dict[str, str]]) -> str:
    combined = str(text or "")
    for a in artifacts:
        tool_name = str(a.get("tool") or "unknown")
        payload = str(a.get("payload") or "").strip()
        placeholder = str(a.get("placeholder") or "").strip()
        injection_mode = str(a.get("injection_mode") or "").strip().lower()
        artifact_type = str(a.get("artifact_type") or "").strip().lower()
        if not payload:
            continue
            continue

        if placeholder and placeholder in combined:
            combined = combined.replace(placeholder, payload)
            pass
            continue
        if placeholder:
            pass
            try:
                token_match = re.match(r"^\{\{(ARTIFACT:[A-Za-z0-9_.:-]{1,64})\}\}$", placeholder)
                token = token_match.group(1) if token_match else ""
                if token:
                    loose_pattern = re.compile(r"\{+\s*" + re.escape(token) + r"\s*\}+")
                    combined, loose_count = loose_pattern.subn(payload, combined)
                    if loose_count > 0:
                        continue
            except Exception:
                pass

        # If model output contains a truncated SVG fragment, remove the broken tail before
        # injecting canonical SVG. This prevents malformed nested markup in finalHtml.
        if artifact_type == "svg":
            has_svg_open = "<svg" in combined.lower()
            has_svg_close = "</svg>" in combined.lower()
            if has_svg_open and not has_svg_close:
                try:
                    prefix = combined.split("<svg", 1)[0].rstrip()
                    combined = prefix
                    pass
                except Exception:
                    pass

        if injection_mode == "verbatim" and payload not in combined:
            if combined.strip():
                combined = f"{combined.strip()}\n\n{payload}"
            else:
                combined = payload
            pass
            pass
        elif injection_mode == "verbatim":
            pass
    return combined


def _redact_tool_outputs_for_synth(tool_outputs_list: List[Dict[str, Any]], tool_registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return tool outputs safe for tools_synth prompt by removing artifact payload fields.

    The second LLM synthesis pass should not receive large binary-like artifacts (e.g., SVG).
    We keep compact metadata so the model can still reference tool results in prose.
    """
    redacted: List[Dict[str, Any]] = []
    tools_by_name = tool_registry.get("tools_by_name") if isinstance(tool_registry, dict) else {}
    if not isinstance(tools_by_name, dict):
        tools_by_name = {}

    for t in (tool_outputs_list or []):
        item = dict(t or {})
        tool_name = str(item.get("name") or "").strip()
        output_text = str(item.get("output") or "")

        # Remove _svg_artifact field from synthesis output (internal field only)
        if "_svg_artifact" in item:
            item.pop("_svg_artifact")

        cfg = tools_by_name.get(tool_name) if tool_name else None
        artifact_cfg = cfg.get("artifact") if isinstance(cfg, dict) else None
        produces_artifact = bool(isinstance(artifact_cfg, dict) and artifact_cfg.get("produces_artifact"))
        artifact_key = str((artifact_cfg or {}).get("artifact_key") or "").strip()
        placeholder = str((artifact_cfg or {}).get("placeholder") or "").strip()

        # MCP adapters may surface an artifact through the internal field even
        # before a matching registry entry has been populated.
        has_inline_artifact = bool(t.get("_svg_artifact"))
        if not produces_artifact and not has_inline_artifact:
            redacted.append(item)
            continue

        compact = ""
        try:
            parsed = json.loads(output_text)
            if isinstance(parsed, dict):
                if artifact_key and artifact_key in parsed:
                    parsed.pop(artifact_key, None)
                # Chart tools commonly attach their source series here.  It is
                # useful to render the chart, but flooding the synthesis prompt
                # with it makes the model echo raw JSON into the final answer.
                parsed.pop("structured_payload", None)
                effective_placeholder = placeholder or str(
                    parsed.get("artifact_placeholder") or item.get("artifact_placeholder") or ""
                ).strip()
                if effective_placeholder:
                    parsed["artifact_placeholder"] = effective_placeholder
                parsed["artifact_payload_omitted"] = True
                compact = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            compact = ""

        if not compact:
            compact = f"Artifact payload omitted for synthesis. placeholder={placeholder or '(none)'}"

        item["output"] = compact
        redacted.append(item)

    return redacted


def _strip_svg_from_messages(messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """Best-effort removal of raw SVG blocks from model input messages.

    This is a last-mile safety guard to ensure tools_synth never receives raw
    artifact payloads, even if an upstream path accidentally includes them.
    """
    stripped_count = 0
    out: List[Dict[str, Any]] = []
    for m in (messages or []):
        item = dict(m or {})
        content = item.get("content")
        if isinstance(content, str):
            new_content, n = re.subn(r"<svg\b[\s\S]*?</svg>", "[SVG_ARTIFACT_OMITTED]", content, flags=re.IGNORECASE)
            if n > 0:
                stripped_count += int(n)
            item["content"] = new_content
        out.append(item)
    return out, stripped_count

#
# --- History slicing helper moved to utils.py ---
