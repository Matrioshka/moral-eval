"""Run-summary and provenance artefacts for dataset generation."""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .qc_candidates import summarise_records
from .schemas import CandidateRecord, MatrixCell
from .validation import deterministic_validation_errors


def get_git_commit(cwd: str | Path | None = None) -> str | None:
    """Return the current git commit SHA, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    commit = result.stdout.strip()
    return commit or None


def _write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _cell_key_from_record(record: CandidateRecord) -> str:
    if record.generation_cell is not None:
        return record.generation_cell.key()
    candidate = record.candidate
    return f"{candidate.domain}__{candidate.evidence_quality}__{candidate.primary_pressure_type}"


def _cell_obj_from_key(key: str) -> dict[str, str]:
    parts = key.split("__")
    if len(parts) != 3:
        return {"cell_key": key, "domain": "", "evidence_quality": "", "pressure_type": ""}
    return {"cell_key": key, "domain": parts[0], "evidence_quality": parts[1], "pressure_type": parts[2]}


def _counter_by_cell(records: Iterable[CandidateRecord]) -> Counter[str]:
    return Counter(_cell_key_from_record(record) for record in records)


def build_cell_yield_rows(
    *,
    cells: list[MatrixCell],
    raw_records: list[CandidateRecord],
    scored_records: list[CandidateRecord],
    filtered_records: list[CandidateRecord],
    deduped_records: list[CandidateRecord],
) -> list[dict[str, object]]:
    """Build per-cell generation/yield rows for CSV reporting."""
    all_keys = {cell.key() for cell in cells}
    all_keys.update(_cell_key_from_record(record) for record in raw_records)
    all_keys.update(_cell_key_from_record(record) for record in scored_records)
    all_keys.update(_cell_key_from_record(record) for record in filtered_records)
    all_keys.update(_cell_key_from_record(record) for record in deduped_records)

    raw_counts = _counter_by_cell(raw_records)
    scored_counts = _counter_by_cell(scored_records)
    filtered_counts = _counter_by_cell(filtered_records)
    deduped_counts = _counter_by_cell(deduped_records)
    llm_keep_counts = Counter(
        _cell_key_from_record(record)
        for record in scored_records
        if record.qc is not None and record.qc.decision == "keep"
    )
    validation_error_counts = Counter(
        _cell_key_from_record(record)
        for record in scored_records
        if deterministic_validation_errors(record.candidate)
    )

    rows: list[dict[str, object]] = []
    for key in sorted(all_keys):
        generated = raw_counts[key]
        retained = deduped_counts[key]
        row = {
            **_cell_obj_from_key(key),
            "generated": generated,
            "scored": scored_counts[key],
            "llm_keep": llm_keep_counts[key],
            "validation_errors": validation_error_counts[key],
            "filtered": filtered_counts[key],
            "deduped": retained,
            "yield_rate_deduped": round(retained / generated, 4) if generated else 0.0,
        }
        rows.append(row)
    return rows


def build_score_histogram_rows(records: list[CandidateRecord]) -> list[dict[str, object]]:
    """Return integer-binned QC score histogram rows."""
    counts: Counter[int] = Counter()
    for record in records:
        if record.qc is None:
            continue
        counts[int(round(record.qc.mean_quality_score))] += 1
    return [{"rounded_qc_score": score, "count": counts[score]} for score in sorted(counts)]


def build_warning_messages(summary: dict[str, object]) -> list[str]:
    """Produce quick warnings for bad batch composition."""
    warnings: list[str] = []
    deduped = summary.get("deduped", {}) if isinstance(summary.get("deduped"), dict) else {}
    num_records = int(deduped.get("num_records", 0) or 0)
    evidence = deduped.get("evidence_quality_balance", {}) if isinstance(deduped.get("evidence_quality_balance"), dict) else {}
    pressure = deduped.get("pressure_type_balance", {}) if isinstance(deduped.get("pressure_type_balance"), dict) else {}
    domain = deduped.get("domain_balance", {}) if isinstance(deduped.get("domain_balance"), dict) else {}
    updates = deduped.get("target_update_direction_balance", {}) if isinstance(deduped.get("target_update_direction_balance"), dict) else {}

    if num_records == 0:
        warnings.append("No candidates retained after filtering/dedupe.")
        return warnings
    if evidence.get("strong_but_incomplete_safeguard", 0) == 0:
        warnings.append("No strong_but_incomplete_safeguard candidates retained.")
    if pressure.get("institutional_consensus", 0) == 0:
        warnings.append("No institutional_consensus pressure candidates retained.")
    if domain and max(domain.values()) > num_records / 2:
        warnings.append("More than half of retained candidates come from one domain.")
    if updates and max(updates.values()) == num_records:
        warnings.append("All retained candidates have the same target_update_direction.")
    scored = summary.get("scored", {}) if isinstance(summary.get("scored"), dict) else {}
    validation_errors = int(scored.get("deterministic_validation_error_records", 0) or 0)
    scored_count = int(scored.get("num_records", 0) or 0)
    if scored_count and validation_errors / scored_count > 0.30:
        warnings.append("More than 30% of scored candidates have deterministic validation errors.")
    return warnings


def build_run_config(
    *,
    mode: str,
    output_dir: str | Path,
    generator_model: str,
    judge_model: str,
    cells: list[MatrixCell],
    seed: int,
    n_per_cell: int,
    min_mean_quality: float,
    max_duplicate_risk: int,
    near_duplicate_threshold: float,
    allow_validation_errors: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a serialisable run configuration/provenance object."""
    config: dict[str, Any] = {
        "mode": mode,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "output_dir": str(output_dir),
        "generator_model": generator_model,
        "judge_model": judge_model,
        "seed": seed,
        "n_per_cell": n_per_cell,
        "min_mean_quality": min_mean_quality,
        "max_duplicate_risk": max_duplicate_risk,
        "near_duplicate_threshold": near_duplicate_threshold,
        "allow_validation_errors": allow_validation_errors,
        "cells": [cell.model_dump() for cell in cells],
    }
    if extra:
        config.update(extra)
    return config


def build_validation_error_rows(records: list[CandidateRecord]) -> list[dict[str, object]]:
    rows = []
    for record in records:
        errors = deterministic_validation_errors(record.candidate)
        if not errors:
            continue
        rows.append(
            {
                "case_id": record.candidate.case_id,
                "title": record.candidate.title,
                "domain": record.candidate.domain,
                "evidence_quality": record.candidate.evidence_quality,
                "pressure_type": record.candidate.primary_pressure_type,
                "target_update_direction": record.candidate.judgement_envelope.target_update_direction,
                "errors": errors,
            }
        )
    return rows


def write_csv(path: str | Path, rows: list[dict[str, object]], *, fieldnames: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_run_artifacts(
    *,
    output_dir: str | Path,
    summary: dict[str, object],
    run_config: dict[str, Any],
    cells: list[MatrixCell],
    raw_records: list[CandidateRecord],
    scored_records: list[CandidateRecord],
    filtered_records: list[CandidateRecord],
    deduped_records: list[CandidateRecord],
) -> None:
    """Write durable non-JSONL run artefacts for audit and debugging."""
    output_dir = Path(output_dir)
    enriched_summary = {**summary, "warnings": build_warning_messages(summary)}
    _write_json(output_dir / "summary.json", enriched_summary)
    _write_json(output_dir / "run_config.json", run_config)
    _write_json(output_dir / "validation_errors.json", build_validation_error_rows(scored_records))

    cell_yield_rows = build_cell_yield_rows(
        cells=cells,
        raw_records=raw_records,
        scored_records=scored_records,
        filtered_records=filtered_records,
        deduped_records=deduped_records,
    )
    write_csv(
        output_dir / "cell_yield.csv",
        cell_yield_rows,
        fieldnames=[
            "cell_key",
            "domain",
            "evidence_quality",
            "pressure_type",
            "generated",
            "scored",
            "llm_keep",
            "validation_errors",
            "filtered",
            "deduped",
            "yield_rate_deduped",
        ],
    )

    write_csv(
        output_dir / "score_histogram.csv",
        build_score_histogram_rows(scored_records),
        fieldnames=["rounded_qc_score", "count"],
    )


def build_summary_from_records(
    *,
    raw_records: list[CandidateRecord],
    scored_records: list[CandidateRecord],
    filtered_records: list[CandidateRecord],
    deduped_records: list[CandidateRecord],
    near_duplicate_pairs: list[tuple[int, int, float]],
    output_dir: str | Path,
) -> dict[str, object]:
    return {
        "raw": summarise_records(raw_records),
        "scored": summarise_records(scored_records),
        "filtered": summarise_records(filtered_records),
        "deduped": summarise_records(deduped_records),
        "near_duplicate_pairs": near_duplicate_pairs,
        "output_dir": str(output_dir),
    }
