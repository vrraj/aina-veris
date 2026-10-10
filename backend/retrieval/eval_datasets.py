"""Labeled evaluation datasets and run-set judgment for retrieval evals.

Datasets live as YAML files under the repo's ``evals/`` directory. Each case
defines a query plus fuzzy expectations (the retrieved chunk won't match
expected text verbatim — document substring, page membership, and
must-contain substring checks). ``run_eval_set`` drives the shared
``run_retrieval_orchestration`` per query and domain and summarizes
hit/page/content metrics plus MRR.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml

from backend.retrieval.orchestration import run_retrieval_orchestration
from backend.retrieval.retrieval_eval_service import RetrievalEvalService

_DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class EvalDatasetError(ValueError):
    """Raised when a dataset name or payload fails validation."""


def _evals_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "evals"


def _dataset_path(name: str) -> Path:
    if not _DATASET_NAME_RE.match(name or ""):
        raise EvalDatasetError(
            "dataset name must match ^[A-Za-z0-9_-]+$ (no path separators)"
        )
    path = (_evals_dir() / f"{name}.yaml").resolve()
    if path.parent != _evals_dir().resolve():
        raise EvalDatasetError("dataset path escapes the evals directory")
    return path


def list_datasets() -> List[str]:
    directory = _evals_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml") if p.is_file())


def _validate_case(case: Any, index: int) -> Dict[str, Any]:
    if not isinstance(case, dict):
        raise EvalDatasetError(f"queries[{index}] must be a mapping")
    query = str(case.get("query") or "").strip()
    if not query:
        raise EvalDatasetError(f"queries[{index}].query is required")
    expected_document = str(case.get("expected_document") or "").strip()
    if not expected_document:
        raise EvalDatasetError(f"queries[{index}].expected_document is required")

    expected_page = case.get("expected_page")
    if expected_page is not None:
        try:
            expected_page = int(expected_page)
        except (TypeError, ValueError):
            raise EvalDatasetError(
                f"queries[{index}].expected_page must be an integer"
            )

    must_contain = case.get("must_contain") or []
    if not isinstance(must_contain, list) or not all(
        isinstance(s, str) and s.strip() for s in must_contain
    ):
        raise EvalDatasetError(
            f"queries[{index}].must_contain must be a list of non-empty strings"
        )

    mode = str(case.get("must_contain_mode") or "any").strip().lower()
    if mode not in {"any", "all"}:
        raise EvalDatasetError(
            f"queries[{index}].must_contain_mode must be 'any' or 'all'"
        )

    return {
        "query": query,
        "expected_document": expected_document,
        "expected_page": expected_page,
        "must_contain": [s.strip() for s in must_contain],
        "must_contain_mode": mode,
    }


def validate_dataset(data: Any) -> Dict[str, Any]:
    """Normalize and validate a dataset mapping; raises EvalDatasetError."""
    if not isinstance(data, dict):
        raise EvalDatasetError("dataset must be a YAML mapping")
    queries = data.get("queries")
    if not isinstance(queries, list) or not queries:
        raise EvalDatasetError("dataset.queries must be a non-empty list")
    return {
        "description": str(data.get("description") or "").strip(),
        "queries": [_validate_case(c, i) for i, c in enumerate(queries)],
    }


def load_dataset(name: str) -> Dict[str, Any]:
    path = _dataset_path(name)
    if not path.is_file():
        raise EvalDatasetError(f"dataset '{name}' not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvalDatasetError(f"dataset '{name}' is not valid YAML: {exc}")
    dataset = validate_dataset(data)
    dataset["name"] = name
    return dataset


def save_dataset(name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and persist a dataset, keeping a .bak of the previous file."""
    path = _dataset_path(name)
    dataset = validate_dataset(data)
    directory = _evals_dir()
    directory.mkdir(parents=True, exist_ok=True)
    backup_path = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(dataset, fh, sort_keys=False, allow_unicode=True)
    return {
        "ok": True,
        "name": name,
        "dataset_path": str(path),
        "backup_path": str(backup_path) if backup_path.exists() else None,
        "queries": len(dataset["queries"]),
    }


def delete_dataset(name: str) -> Dict[str, Any]:
    path = _dataset_path(name)
    if not path.is_file():
        raise EvalDatasetError(f"dataset '{name}' not found")
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    return {"ok": True, "name": name, "backup_path": str(backup_path)}


# ---------------------------------------------------------------------------
# Judgment


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _item_payload(item: Any) -> Dict[str, Any]:
    """Unwrap reranked rows ({item: {...}}) to the raw retrieval item."""
    if isinstance(item, dict) and isinstance(item.get("item"), dict):
        item = item["item"]
    if isinstance(item, dict):
        payload = item.get("payload")
        if isinstance(payload, dict):
            return payload
        return item
    return {}


def _doc_blob(payload: Dict[str, Any]) -> str:
    fields = ("source", "url", "document_id", "title", "base_url", "file_name")
    return _norm(" ".join(str(payload.get(f) or "") for f in fields))


def _text_blob(payload: Dict[str, Any]) -> str:
    return _norm(" ".join(
        str(payload.get(f) or "") for f in ("text", "display_text")
    ))


def judge_item(item: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    payload = _item_payload(item)
    doc_hit = _norm(case["expected_document"]) in _doc_blob(payload)

    page_hit: Optional[bool] = None
    if case.get("expected_page") is not None:
        pages = payload.get("page_numbers") or []
        try:
            page_hit = int(case["expected_page"]) in {int(p) for p in pages}
        except (TypeError, ValueError):
            page_hit = False

    content_hit: Optional[bool] = None
    matched: List[str] = []
    if case.get("must_contain"):
        blob = _text_blob(payload)
        matched = [s for s in case["must_contain"] if _norm(s) in blob]
        content_hit = (
            len(matched) == len(case["must_contain"])
            if case.get("must_contain_mode") == "all"
            else bool(matched)
        )

    return {
        "doc_hit": doc_hit,
        "page_hit": page_hit if doc_hit else None,
        "content_hit": content_hit if doc_hit else None,
        "matched_strings": matched if doc_hit else [],
    }


def _rank_of_first_doc_hit(items: Iterable[Any], case: Dict[str, Any]) -> Optional[int]:
    needle = _norm(case["expected_document"])
    for index, item in enumerate(items, start=1):
        if needle in _doc_blob(_item_payload(item)):
            return index
    return None


def judge_query_result(
    orchestration_result: Dict[str, Any], case: Dict[str, Any]
) -> Dict[str, Any]:
    """Judge one orchestration result against a labeled case."""
    reranked_rows = (orchestration_result.get("reranked") or {}).get("items") or []
    final_items = [r.get("item") or r for r in reranked_rows]
    retrieval_items = (orchestration_result.get("retrieval") or {}).get("results") or []

    row: Dict[str, Any] = {
        "query": case["query"],
        "expected_document": case["expected_document"],
        "error": None,
        "num_results": len(final_items),
        "retrieval_first_hit_rank": _rank_of_first_doc_hit(retrieval_items, case),
        "first_hit_rank": _rank_of_first_doc_hit(final_items, case),
        "doc_hit": False,
        "page_hit": None,
        "content_hit": None,
        "matched_strings": [],
        "decomposed_queries": (orchestration_result.get("decomposition") or {}).get("queries"),
    }

    for item in final_items:
        verdict = judge_item(item, case)
        if verdict["doc_hit"]:
            row.update(
                doc_hit=True,
                page_hit=verdict["page_hit"],
                content_hit=verdict["content_hit"],
                matched_strings=verdict["matched_strings"],
            )
            break
    row["retrieval_doc_hit"] = row["retrieval_first_hit_rank"] is not None
    return row


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    errors = sum(1 for r in rows if r.get("error"))
    doc_hits = sum(1 for r in rows if r.get("doc_hit"))
    retrieval_hits = sum(1 for r in rows if r.get("retrieval_doc_hit"))
    page_rows = [r for r in rows if r.get("page_hit") is not None]
    content_rows = [r for r in rows if r.get("content_hit") is not None]

    def _mrr(key: str) -> float:
        ranks = [r[key] for r in rows if r.get(key)]
        return sum(1.0 / rank for rank in ranks) / total if total else 0.0

    return {
        "queries": total,
        "errors": errors,
        "doc_hits": doc_hits,
        "hit_rate": doc_hits / total if total else 0.0,
        "mrr": _mrr("first_hit_rank"),
        "retrieval_doc_hits": retrieval_hits,
        "retrieval_hit_rate": retrieval_hits / total if total else 0.0,
        "retrieval_mrr": _mrr("retrieval_first_hit_rank"),
        "page_hits": sum(1 for r in page_rows if r["page_hit"]),
        "page_hit_rate": (
            sum(1 for r in page_rows if r["page_hit"]) / len(page_rows)
            if page_rows else None
        ),
        "content_hits": sum(1 for r in content_rows if r["content_hit"]),
        "content_hit_rate": (
            sum(1 for r in content_rows if r["content_hit"]) / len(content_rows)
            if content_rows else None
        ),
    }


def run_eval_set(
    dataset: Dict[str, Any],
    *,
    domains: List[str],
    retrieval_knobs: Dict[str, Any],
    service_factory: Optional[Callable[[str], Any]] = None,
    decomposition_generator: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Run every labeled query against every domain; judge and summarize."""
    runs: Dict[str, Any] = {}
    for domain in domains:
        service = (
            service_factory(domain)
            if service_factory
            else RetrievalEvalService(active_domain=domain)
        )
        rows = []
        for case in dataset["queries"]:
            try:
                result = run_retrieval_orchestration(
                    query=case["query"],
                    active_domain=domain,
                    service=service,
                    decomposition_generator=decomposition_generator,
                    **retrieval_knobs,
                )
                rows.append(judge_query_result(result, case))
            except Exception as exc:  # surface per-query failure, keep set running
                rows.append({
                    "query": case["query"],
                    "expected_document": case["expected_document"],
                    "error": str(exc),
                    "doc_hit": False,
                    "page_hit": None,
                    "content_hit": None,
                    "first_hit_rank": None,
                    "retrieval_first_hit_rank": None,
                    "retrieval_doc_hit": False,
                    "num_results": 0,
                    "matched_strings": [],
                    "decomposed_queries": None,
                })
        runs[domain] = {"per_query": rows, "aggregate": summarize(rows)}
    return {"dataset": dataset.get("name"), "domains": domains, "runs": runs}
