"""Generate candidates until a retained-candidate quota is reached.

This is deliberately bounded. It is for candidate triage, not automatic dataset
construction. Manual review remains required before a pilot dataset is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dedupe_candidates import drop_near_duplicates, flag_near_duplicates
from .export_jsonl import write_inspect_jsonl, write_jsonl
from .generate_candidates import generate_candidates_for_cells
from .llm_clients import StructuredLLM
from .manual_review import write_manual_review_csv, write_manual_review_jsonl
from .prompts import PromptConfig
from .qc_candidates import filter_candidate_records, score_candidate_records
from .run_summary import build_run_config, build_summary_from_records, write_run_artifacts
from .schemas import CandidateRecord, MatrixCell
from .validation import annotate_record_validation


@dataclass(frozen=True)
class QuotaGenerationResult:
    summary: dict[str, object]
    batches_run: int
    target_reached: bool
    retained_count: int


def generate_until_quota(
    *,
    generator_llm: StructuredLLM,
    judge_llm: StructuredLLM,
    generator_model: str,
    judge_model: str,
    cells: list[MatrixCell],
    output_dir: str | Path,
    target_kept: int = 12,
    max_batches: int = 4,
    batch_n_per_cell: int = 1,
    prompt_config: PromptConfig | None = None,
    generation_workers: int | None = 6,
    judge_workers: int | None = 6,
    seed: int = 1,
    min_mean_quality: float = 8.0,
    max_duplicate_risk: int = 4,
    near_duplicate_threshold: float = 0.86,
    allow_validation_errors: bool = False,
) -> QuotaGenerationResult:
    """Run bounded generate-score-filter batches until enough candidates survive."""
    if target_kept < 1:
        raise ValueError("target_kept must be >= 1")
    if max_batches < 1:
        raise ValueError("max_batches must be >= 1")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_raw: list[CandidateRecord] = []
    all_scored: list[CandidateRecord] = []
    all_filtered: list[CandidateRecord] = []
    kept: list[CandidateRecord] = []

    batches_run = 0
    for batch_idx in range(max_batches):
        batches_run = batch_idx + 1
        batch_seed = seed + batch_idx
        raw_records = [
            annotate_record_validation(record)
            for record in generate_candidates_for_cells(
                llm=generator_llm,
                model=generator_model,
                cells=cells,
                n_per_cell=batch_n_per_cell,
                prompt_config=prompt_config,
                max_workers=generation_workers,
                seed=batch_seed,
                case_id_stem=f"jmcu_p3_b{batch_idx + 1:02d}",
            )
        ]
        all_raw.extend(raw_records)

        scored_records = score_candidate_records(
            llm=judge_llm,
            model=judge_model,
            records=raw_records,
            max_workers=judge_workers,
        )
        all_scored.extend(scored_records)

        filtered_records = filter_candidate_records(
            scored_records,
            min_mean_quality=min_mean_quality,
            max_duplicate_risk=max_duplicate_risk,
            allow_validation_errors=allow_validation_errors,
        )
        all_filtered.extend(filtered_records)

        kept = drop_near_duplicates([*kept, *filtered_records], threshold=near_duplicate_threshold)
        if len(kept) >= target_kept:
            break

    kept = kept[:target_kept]
    duplicate_pairs = flag_near_duplicates(all_filtered, threshold=near_duplicate_threshold)

    write_jsonl(output_dir / "raw_candidates.jsonl", all_raw)
    write_jsonl(output_dir / "scored_candidates.jsonl", all_scored)
    write_jsonl(output_dir / "filtered_candidates.jsonl", all_filtered)
    write_jsonl(output_dir / "kept_candidates.jsonl", kept)
    write_inspect_jsonl(output_dir / "kept_candidates.inspect.jsonl", kept)
    write_manual_review_csv(output_dir / "manual_review_template.csv", kept)
    write_manual_review_jsonl(output_dir / "manual_review_template.jsonl", kept)

    summary = build_summary_from_records(
        raw_records=all_raw,
        scored_records=all_scored,
        filtered_records=all_filtered,
        deduped_records=kept,
        near_duplicate_pairs=duplicate_pairs,
        output_dir=output_dir,
    )
    summary.update(
        {
            "quota_mode": True,
            "target_kept": target_kept,
            "max_batches": max_batches,
            "batches_run": batches_run,
            "target_reached": len(kept) >= target_kept,
        }
    )

    run_config = build_run_config(
        mode="quota",
        output_dir=output_dir,
        generator_model=generator_model,
        judge_model=judge_model,
        cells=cells,
        seed=seed,
        n_per_cell=batch_n_per_cell,
        min_mean_quality=min_mean_quality,
        max_duplicate_risk=max_duplicate_risk,
        near_duplicate_threshold=near_duplicate_threshold,
        allow_validation_errors=allow_validation_errors,
        extra={
            "target_kept": target_kept,
            "max_batches": max_batches,
            "batches_run": batches_run,
        },
    )
    write_run_artifacts(
        output_dir=output_dir,
        summary=summary,
        run_config=run_config,
        cells=cells,
        raw_records=all_raw,
        scored_records=all_scored,
        filtered_records=all_filtered,
        deduped_records=kept,
    )

    return QuotaGenerationResult(
        summary=summary,
        batches_run=batches_run,
        target_reached=len(kept) >= target_kept,
        retained_count=len(kept),
    )
