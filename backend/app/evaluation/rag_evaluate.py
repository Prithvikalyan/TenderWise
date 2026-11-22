"""Run a human-labelled retrieval benchmark against a real TenderWise database.

Example:
    python -m app.evaluation.rag_evaluate --dataset app/evaluation/golden_dataset.jsonl --k 5

The dataset contains tender_id, question and relevant_sources. A relevant source
is identified by document_name and at least one location key such as page,
worksheet_name, row_start, paragraph_index or chunk_id.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.evaluation.metrics import ranking_metrics
from app.services.advanced_retrieval import retrieve_tender_evidence

logger = logging.getLogger("tenderwise.rag_evaluate")


def _source_key(item: dict) -> str:
    if item.get("chunk_id"):
        return f"chunk:{item['chunk_id']}"
    name = str(item.get("document_name", "")).strip().casefold()
    location = item.get("location") or item.get("source_ref") or {}
    keys = ("page", "worksheet_name", "row_start", "paragraph_index", "table_index")
    location_value = tuple((key, location.get(key)) for key in keys if key in location)
    return f"{name}|{location_value}"


def _matches_reference(retrieved_item: dict, reference: dict) -> bool:
    if reference.get("chunk_id"):
        return retrieved_item.get("chunk_id") == reference["chunk_id"]
    expected_name = str(reference.get("document_name", "")).strip().casefold()
    actual_name = str(retrieved_item.get("document_name", "")).strip().casefold()
    if expected_name and expected_name != actual_name:
        return False
    expected_location = reference.get("location") or {}
    actual_locations = retrieved_item.get("source_refs") or [retrieved_item.get("source_ref") or {}]
    location_keys = (
        "page", "worksheet_name", "row_start", "row_end",
        "paragraph_index", "table_index", "block_index",
    )
    required_keys = [key for key in location_keys if key in expected_location]
    if not required_keys:
        return bool(expected_name and expected_name == actual_name)
    # A labelled source reference can specify multiple coordinates. They must
    # all match the same extracted reference; matching only one field could
    # falsely count a different table/page as relevant.
    return any(
        all(location.get(key) == expected_location[key] for key in required_keys)
        for location in actual_locations
    )


def evaluate_dataset(session: Session, dataset_path: Path, k: int = 5) -> dict:
    samples = []
    aggregate = defaultdict(list)
    for line_number, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(line)
            tender_id = UUID(case["tender_id"])
            question = case["question"]
            expected = case["relevant_sources"]
            if not isinstance(expected, list) or not expected:
                raise ValueError("relevant_sources must be a non-empty list")
        except Exception as exc:
            raise ValueError(f"Invalid evaluation case on line {line_number}: {exc}") from exc

        result = retrieve_tender_evidence(session, tender_id, question)
        ranked = result.trace.ranked_evidence
        relevant_ids = {_source_key(item) for item in expected}
        retrieved_ids: list[str] = []
        seen_retrieved: set[str] = set()
        for item in ranked:
            matched = next((reference for reference in expected if _matches_reference(item, reference)), None)
            # Multiple chunks from the same labelled source page must not be
            # counted as several independent relevant hits in P@k / nDCG.
            key = _source_key(matched) if matched else _source_key(item)
            if key not in seen_retrieved:
                retrieved_ids.append(key)
                seen_retrieved.add(key)
        metrics = ranking_metrics(retrieved_ids, relevant_ids, k=k)
        for name, value in metrics.items():
            aggregate[name].append(value)
        samples.append({
            "tender_id": str(tender_id),
            "question": question,
            "retrieval_mode": result.retrieval_mode,
            "chunks_scanned": result.trace.chunks_scanned,
            "lexical_candidates": result.trace.lexical_candidates,
            "dense_candidates": result.trace.dense_candidates,
            "rerank_mode": result.trace.rerank_mode,
            "embedding_coverage": result.trace.embedding_coverage,
            "metrics": metrics,
        })
    summary = {name: (sum(values) / len(values) if values else 0.0) for name, values in aggregate.items()}
    return {"case_count": len(samples), "k": k, "summary": summary, "cases": samples}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate TenderWise retrieval using human-labelled source references")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with Session(get_engine()) as session:
            report = evaluate_dataset(session, args.dataset, args.k)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    except Exception as exc:
        logger.error("RAG evaluation failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
