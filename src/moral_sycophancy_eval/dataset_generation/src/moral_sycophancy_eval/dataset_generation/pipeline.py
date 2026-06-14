"""Small orchestration layer for Phase 3 candidate generation."""

from __future__ import annotations

from pathlib import Path

from .dedupe_candidates import drop_near_duplicates, flag_near_duplicates
from .export_jsonl import write_inspect_jsonl, write_jsonl
from .generate_candidates import generate_candidates_for_cells
from .llm_clients import StructuredLLM
from .prompts import PromptConfig
from .qc_candidates import filter_candidate_records, score_candidate_records, summarise_records
from .schemas import CandidateRecord, MatrixCell


def generate_score_filter_export(
    *,
    generator_llm: StructuredLLM,
    judge_llm: StructuredLLM,
    generator_model: str,
    judge_model: str,
    cells: list[MatrixCell],
    output_dir: str | Path,
    n_per_cell: int = 1,
    prompt_config: PromptConfig | None = None,
    generation_workers: int | None = 6,
    judge_workers: int | None = 6,
    seed: int = 1,
    min_mean_quality: float = 8.0,
    max_duplicate_risk: int = 4,
    near_duplicate_threshold: float = 0.86,
) -> dict[str, object]:
    """Run the complete candidate-generation pipeline.

    This performs model calls. Use small cell sets first.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_records = generate_candidates_for_cells(
        llm=generator_llm,
        model=generator_model,
        cells=cells,
        n_per_cell=n_per_cell,
        prompt_config=prompt_config,
        max_workers=generation_workers,
        seed=seed,
    )
    write_jsonl(output_dir / "raw_candidates.jsonl", raw_records)

    scored_records = score_candidate_records(
        llm=judge_llm,
        model=judge_model,
        records=raw_records,
        max_workers=judge_workers,
    )
    write_jsonl(output_dir / "scored_candidates.jsonl", scored_records)

    filtered = filter_candidate_records(
        scored_records,
        min_mean_quality=min_mean_quality,
        max_duplicate_risk=max_duplicate_risk,
    )
    deduped = drop_near_duplicates(filtered, threshold=near_duplicate_threshold)

    write_jsonl(output_dir / "kept_candidates.jsonl", deduped)
    write_inspect_jsonl(output_dir / "kept_candidates.inspect.jsonl", deduped)

    duplicate_pairs = flag_near_duplicates(filtered, threshold=near_duplicate_threshold)
    return {
        "raw": summarise_records(raw_records),
        "scored": summarise_records(scored_records),
        "filtered": summarise_records(filtered),
        "deduped": summarise_records(deduped),
        "near_duplicate_pairs": duplicate_pairs,
        "output_dir": str(output_dir),
    }


def select_manual_pilot(records: list[CandidateRecord], *, max_items: int = 12) -> list[CandidateRecord]:
    """Simple balanced-ish pilot selector for manual audit.

    Keeps high-QC records while avoiding immediate domination by one evidence or
    pressure type. Replace with a stricter optimiser if the pilot grows.
    """
    scored = [r for r in records if r.qc is not None]
    scored.sort(key=lambda r: (r.qc.mean_quality_score if r.qc else 0.0), reverse=True)

    selected: list[CandidateRecord] = []
    evidence_seen: dict[str, int] = {}
    pressure_seen: dict[str, int] = {}

    for record in scored:
        if len(selected) >= max_items:
            break
        e = record.candidate.evidence_quality
        p = record.candidate.primary_pressure_type
        if evidence_seen.get(e, 0) >= max(1, max_items // 4 + 1):
            continue
        if pressure_seen.get(p, 0) >= max(1, max_items // 5 + 1):
            continue
        selected.append(record)
        evidence_seen[e] = evidence_seen.get(e, 0) + 1
        pressure_seen[p] = pressure_seen.get(p, 0) + 1

    for record in scored:
        if len(selected) >= max_items:
            break
        if record not in selected:
            selected.append(record)

    return selected
