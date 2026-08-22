"""Context, source, and prompt message formatting helpers."""

from typing import Any, Dict, List


def format_query_plan(query_plan: Dict[str, Any]) -> str:
    """Format a compound query plan as neutral context for inference."""
    if not query_plan or not query_plan.get("is_compound"):
        return ""
    parent = str(query_plan.get("normalized_query") or "").strip()
    queries = [
        str(query or "").strip()
        for query in query_plan.get("queries") or []
        if str(query or "").strip()
    ]
    lines = [f"Parent normalized query: {parent}", "Subqueries:"]
    lines.extend(f"{index}. {query}" for index, query in enumerate(queries, start=1))
    return "\n".join(lines)


def format_context_lines(items: List[Dict[str, Any]]) -> str:
    """Format evidence with stable document metadata for inference and citations."""
    lines: List[str] = []
    for i, c in enumerate(items or []):
        pl = c.get("payload") or {}
        text = (
            pl.get("text")
            or pl.get("snippet")
            or pl.get("content")
            or c.get("text", "")
            or ""
        ).strip()
        section = pl.get("section") or c.get("section", "N/A")
        subsection = pl.get("subsection") or c.get("subsection", "N/A")
        source = (
            pl.get("base_url")
            or c.get("base_url")
            or pl.get("url")
            or c.get("url")
            or pl.get("source")
            or c.get("source")
            or "unknown"
        )
        title = pl.get("title") or c.get("title") or "Untitled document"
        section_path = str(section)
        if subsection and str(subsection) != "N/A":
            section_path = f"{section_path} > {subsection}"
        lines.append(
            f"[{i+1}]\n"
            f"Source: {source}\n"
            f"Document title: {title}\n"
            f"Section: {section_path}\n\n"
            f"{text}"
        )
    return "\n".join(lines)


def render_source_line(indices: list[int], url: str, section: str, subsection: str) -> str:
    idx_text = ", ".join(str(i) for i in sorted(set(indices)))
    return f"[{idx_text}] {url} (Section: {section} > {subsection})"


def collapse_sources(indexed_items: List[Dict[str, Any]]) -> str:
    """Group by (url, section, subsection) and collapse indices."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    for it in indexed_items:
        url = (it.get("url") or "unknown").strip()
        section = (it.get("section") or "N/A").strip()
        subsection = (it.get("subsection") or "N/A").strip()
        key = (url, section, subsection)
        if key not in groups:
            groups[key] = {"indices": [], "url": url, "section": section, "subsection": subsection}
        idx = int(it.get("index", 0) or 0)
        if idx > 0:
            groups[key]["indices"].append(idx)

    lines: List[str] = []
    for data in groups.values():
        if data["indices"]:
            lines.append(render_source_line(data["indices"], data["url"], data["section"], data["subsection"]))
    return "\n".join(lines)


def format_web_context_as_text(web_context: Any) -> str:
    try:
        return "\n".join(
            [
                f"{i+1}. {item.get('title','')}\n{item.get('snippet','')}\nURL: {item.get('url','')}"
                for i, item in enumerate(web_context, start=1)
            ]
        )
    except Exception:
        return ""


def build_inference_messages(
    *,
    system_prompt: str,
    summary_text: str,
    recent_block_str: str,
    context_text: str,
    web_context: Any,
    message: str,
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if summary_text:
        messages.append({"role": "user", "content": f"CONVERSATION SUMMARY: {summary_text}"})
    if recent_block_str:
        messages.append({"role": "user", "content": "RECENT CONVERSATION:\n" + recent_block_str.strip()})
    if context_text:
        messages.append({"role": "user", "content": f"CONTEXT:\n{context_text}"})
    if web_context:
        web_text = format_web_context_as_text(web_context)
        messages.append({"role": "user", "content": f"WEB SEARCH RESULTS:\n{web_text}"})
    messages.append({"role": "user", "content": message})
    return messages


def build_tools_synth_messages(
    *,
    system_prompt: str,
    summary_text: str,
    recent_block_str: str,
    context_text: str,
    tool_outputs_list: List[Dict[str, Any]],
    used_tools: List[str],
    tools_text: str,
    message: str,
) -> List[Dict[str, str]]:
    synth_messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if summary_text:
        synth_messages.append({"role": "user", "content": f"CONVERSATION SUMMARY:\n{summary_text}"})
    if recent_block_str:
        synth_messages.append({"role": "user", "content": "RECENT CONVERSATION:\n" + recent_block_str.strip()})
    synth_messages.append({"role": "user", "content": f"[SOURCE: KNOWLEDGE_BASE]\nCONTEXT:\n{context_text}"})
    synth_messages.append({"role": "user", "content": f"TOOLS USED:\n{', '.join(used_tools) if used_tools else ''}"})
    for t in tool_outputs_list:
        source_lines = []
        for source in t.get("sources") or []:
            citation = str(source.get("citation") or "").strip()
            url = str(source.get("url") or "").strip()
            if citation and url:
                source_lines.append(
                    f"[{citation}] {source.get('title') or url}\nURL: {url}\n{source.get('snippet') or ''}".strip()
                )
        source_block = "\n\nCITABLE TOOL SOURCES:\n" + "\n\n".join(source_lines) if source_lines else ""
        synth_messages.append(
            {
                "role": "user",
                "content": f"[SOURCE: TOOL - {t.get('name') or 'unknown'}]\n{str(t.get('output', ''))}{source_block}",
            }
        )
    synth_messages.append(
        {
            "role": "user",
            "content": f"Question: {message}\n\nTask: Produce the final answer to the Question using the Context and Tool results.",
        }
    )
    return synth_messages
