"""Advance manifest-driven dataset generation through bounded review stages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .adjudication import apply_adjudication as apply_adjudication_file
from .adjudication import write_adjudication_template
from .export_jsonl import read_jsonl, write_behaviour_dataset_jsonl
from .control_db import (
    acquire_run_lock,
    artifact_id,
    load_run_execution_state,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    mark_stage_skipped,
    observe_file,
    open_adjudication_gate,
    open_manual_review_gate,
    open_revision_gate,
    open_revised_adjudication_gate,
    record_artifact,
    release_run_lock,
    satisfy_adjudication_gate,
    satisfy_manual_review_gate,
    satisfy_revision_gate,
    satisfy_revised_adjudication_gate,
)
from .llm_clients import OpenAICompatibleJSONClient, OpenAIParseClient
from .manifest import DatasetGenerationManifest, manifest_from_snapshot, sha256_bytes
from .manual_review import apply_manual_review as apply_manual_review_file
from .manual_review import prepare_manual_review_inputs
from .pipeline import generate_score_filter_export
from .prompts import PromptConfig
from .quota_generation import generate_until_quota
from .revision import apply_candidate_revisions, extract_revise_candidates
from .schemas import MatrixCell

SUPPORTED_STAGES = {
    "generate_candidates",
    "prepare_adjudication",
    "apply_adjudication",
    "prepare_revision",
    "apply_revision",
    "prepare_revised_adjudication",
    "apply_revised_adjudication",
    "prepare_manual_review",
    "apply_manual_review",
    "export_inspect_jsonl",
}
GENERATION_ARTIFACTS = (
    ("raw_candidates", "raw_candidates.jsonl", "jsonl", "generated_candidates"),
    ("scored_candidates", "scored_candidates.jsonl", "jsonl", "scored_candidates"),
    ("filtered_candidates", "filtered_candidates.jsonl", "jsonl", "filtered_candidates"),
    ("kept_candidates", "kept_candidates.jsonl", "jsonl", "retained_candidates"),
    ("kept_candidates_inspect", "kept_candidates.inspect.jsonl", "jsonl", "inspect_preview"),
    ("manual_review_template_csv", "manual_review_template.csv", "csv", "manual_review_template"),
    ("manual_review_template_jsonl", "manual_review_template.jsonl", "jsonl", "manual_review_template"),
    ("summary", "summary.json", "json", "generation_summary"),
    ("run_config", "run_config.json", "json", "generation_config"),
    ("validation_errors", "validation_errors.json", "json", "validation_results"),
    ("cell_yield", "cell_yield.csv", "csv", "generation_diagnostics"),
    ("score_histogram", "score_histogram.csv", "csv", "generation_diagnostics"),
)


class StageExecutionError(RuntimeError):
    """Raised after a failed stage has been persisted as failed."""


@dataclass(frozen=True)
class NextResult:
    outcome: str
    message: str
    stage_key: str | None = None
    template_path: str | None = None
    completed_path: str | None = None


def _load_cells(path: Path, limit: int | None) -> list[MatrixCell]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        payload: list[Any] = []
    elif text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError(f"expected a JSON list in {path}")
        payload = value
    else:
        payload = [json.loads(line) for line in text.splitlines() if line.strip()]
    cells = [MatrixCell.model_validate(item) for item in payload]
    return cells[:limit] if limit is not None else cells


def execute_generation(manifest: DatasetGenerationManifest, repo_root: Path) -> None:
    """Execute the existing candidate pipeline. Caller must enforce the safety interlock."""
    generation = manifest.generation
    cells = _load_cells(repo_root / manifest.cells.file, manifest.cells.limit)
    if not cells:
        raise ValueError("cells file contains no generation cells")

    if generation.provider == "openai-parse":
        generator_llm = OpenAIParseClient()
        judge_llm = OpenAIParseClient()
    else:
        generator_llm = OpenAICompatibleJSONClient(base_url=generation.base_url)
        judge_llm = OpenAICompatibleJSONClient(base_url=generation.base_url)

    prompt_config = PromptConfig()
    if manifest.generation_context is not None:
        context = manifest.generation_context
        seed_brief_path = repo_root / context.seed_brief_path
        seed_brief_bytes = seed_brief_path.read_bytes()
        observed_sha256 = sha256_bytes(seed_brief_bytes)
        if observed_sha256 != context.seed_brief_sha256:
            raise ValueError(
                "generation context seed brief changed after manifest initialisation: "
                f"{context.seed_brief_path}"
            )
        prompt_config = PromptConfig(
            topic_focus=context.topic_focus,
            seed_guidance=seed_brief_bytes.decode("utf-8"),
        )

    common = {
        "generator_llm": generator_llm,
        "judge_llm": judge_llm,
        "generator_model": generation.generator_model,
        "judge_model": generation.judge_model,
        "cells": cells,
        "output_dir": repo_root / manifest.run.output_dir,
        "generation_workers": generation.max_workers,
        "judge_workers": generation.max_workers,
        "seed": generation.seed,
        "min_mean_quality": manifest.quality.min_mean_quality,
        "max_duplicate_risk": manifest.quality.max_duplicate_risk,
        "near_duplicate_threshold": manifest.quality.near_duplicate_threshold,
        "allow_validation_errors": manifest.quality.allow_validation_errors,
        "prompt_config": prompt_config,
    }
    if generation.mode == "quota":
        generate_until_quota(
            **common,
            target_kept=generation.target_kept,
            max_batches=generation.max_batches,
            batch_n_per_cell=generation.n_per_cell,
        )
    else:
        generate_score_filter_export(
            **common,
            n_per_cell=generation.n_per_cell,
        )


def execute_prepare_adjudication(manifest: DatasetGenerationManifest, repo_root: Path) -> Path:
    output_dir = repo_root / manifest.run.output_dir
    template = output_dir / "adjudication_template.csv"
    write_adjudication_template(output_dir / "kept_candidates.jsonl", template)
    return template


def execute_apply_adjudication(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
    completed_csv: Path,
) -> tuple[Path, Path, int]:
    output_dir = repo_root / manifest.run.output_dir
    output_jsonl = output_dir / "adjudicated_candidates.jsonl"
    summary_json = output_dir / "adjudication_summary.json"
    conflicts = [path for path in (output_jsonl, summary_json) if path.exists()]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(f"refusing to overwrite adjudication output(s): {rendered}")
    retained = apply_adjudication_file(
        output_dir / "kept_candidates.jsonl",
        completed_csv,
        output_jsonl,
    )
    return output_jsonl, summary_json, len(retained)


def execute_prepare_revision(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
) -> tuple[Path, Path, int]:
    output_dir = repo_root / manifest.run.output_dir
    revise_jsonl = output_dir / "revise_candidates.jsonl"
    revision_notes = output_dir / "revision_notes.csv"
    conflicts = [path for path in (revise_jsonl, revision_notes) if path.exists()]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(f"refusing to overwrite revision output(s): {rendered}")
    records = extract_revise_candidates(
        output_dir / "adjudicated_candidates.jsonl",
        revise_jsonl,
        revision_notes,
        adjudication_csv=output_dir / "adjudication_completed.csv",
    )
    return revise_jsonl, revision_notes, len(records)


def execute_apply_revision(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
    completed_csv: Path,
) -> tuple[Path, int]:
    output_dir = repo_root / manifest.run.output_dir
    revised_jsonl = output_dir / "revised_candidates.jsonl"
    if revised_jsonl.exists():
        raise FileExistsError(f"refusing to overwrite revision output: {revised_jsonl}")
    records = apply_candidate_revisions(
        output_dir / "revise_candidates.jsonl",
        completed_csv,
        revised_jsonl,
    )
    return revised_jsonl, len(records)

def execute_prepare_revised_adjudication(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
) -> Path:
    output_dir = repo_root / manifest.run.output_dir
    template = output_dir / "revised_adjudication_template.csv"
    if template.exists():
        raise FileExistsError(
            f"refusing to overwrite revised adjudication template: {template}"
        )
    write_adjudication_template(output_dir / "revised_candidates.jsonl", template)
    return template


def execute_apply_revised_adjudication(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
    completed_csv: Path,
) -> tuple[Path, Path, int]:
    output_dir = repo_root / manifest.run.output_dir
    output_jsonl = output_dir / "adjudicated_revised_candidates.jsonl"
    summary_json = output_dir / "revised_adjudication_summary.json"
    conflicts = [path for path in (output_jsonl, summary_json) if path.exists()]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(
            f"refusing to overwrite revised adjudication output(s): {rendered}"
        )
    retained = apply_adjudication_file(
        output_dir / "revised_candidates.jsonl",
        completed_csv,
        output_jsonl,
        summary_output_path=summary_json,
    )
    return output_jsonl, summary_json, len(retained)


def execute_prepare_manual_review(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
) -> tuple[Path, Path, Path, Path, dict[str, int]]:
    output_dir = repo_root / manifest.run.output_dir
    ready_jsonl = output_dir / "ready_for_manual_review.jsonl"
    template_csv = output_dir / "manual_review_gate_template.csv"
    unresolved_jsonl = output_dir / "unresolved_for_manual_review.jsonl"
    summary_json = output_dir / "manual_review_preparation_summary.json"
    conflicts = [
        path
        for path in (ready_jsonl, template_csv, unresolved_jsonl, summary_json)
        if path.exists()
    ]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(
            f"refusing to overwrite manual-review preparation output(s): {rendered}"
        )
    summary = prepare_manual_review_inputs(
        adjudicated_candidates_jsonl=output_dir / "adjudicated_candidates.jsonl",
        ready_output_jsonl=ready_jsonl,
        manual_review_template_csv=template_csv,
        unresolved_output_jsonl=unresolved_jsonl,
        summary_output_json=summary_json,
        revisions_enabled=manifest.workflow.revisions,
        revise_candidates_jsonl=output_dir / "revise_candidates.jsonl",
        revised_candidates_jsonl=output_dir / "revised_candidates.jsonl",
        revised_adjudication_csv=output_dir / "revised_adjudication_completed.csv",
        adjudicated_revised_candidates_jsonl=(
            output_dir / "adjudicated_revised_candidates.jsonl"
        ),
    )
    return ready_jsonl, template_csv, unresolved_jsonl, summary_json, summary


def execute_apply_manual_review(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
    completed_csv: Path,
) -> tuple[Path, Path, dict[str, int]]:
    output_dir = repo_root / manifest.run.output_dir
    input_jsonl = output_dir / "ready_for_manual_review.jsonl"
    reviewed_jsonl = output_dir / "reviewed_candidates.jsonl"
    summary_json = output_dir / "manual_review_summary.json"
    conflicts = [path for path in (reviewed_jsonl, summary_json) if path.exists()]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(
            f"refusing to overwrite manual-review output(s): {rendered}"
        )
    selected = apply_manual_review_file(
        review_input_jsonl=input_jsonl,
        manual_review_csv=completed_csv,
        pilot_output_jsonl=reviewed_jsonl,
        allow_reviewed_validation_errors=(
            manifest.export.allow_reviewed_validation_errors
        ),
    )
    ready_count = observe_file(input_jsonl).row_count or 0
    completed_count = observe_file(completed_csv).row_count or 0
    summary = {
        "ready_candidates": ready_count,
        "completed_review_rows": completed_count,
        "reviewed_candidates": len(selected),
        "excluded_candidates": ready_count - len(selected),
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return reviewed_jsonl, summary_json, summary


def execute_export_inspect_jsonl(
    manifest: DatasetGenerationManifest,
    repo_root: Path,
) -> tuple[Path, Path, dict[str, int | str]]:
    input_jsonl = repo_root / manifest.run.output_dir / "reviewed_candidates.jsonl"
    output_jsonl = repo_root / str(manifest.export.output_path)
    summary_json = (
        repo_root / manifest.run.output_dir / "inspect_export_summary.json"
    )
    conflicts = [path for path in (output_jsonl, summary_json) if path.exists()]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(f"refusing to overwrite Inspect export output(s): {rendered}")
    records = read_jsonl(input_jsonl)
    write_behaviour_dataset_jsonl(
        output_jsonl,
        records,
        dataset_version=str(manifest.export.dataset_version),
        allow_reviewed_validation_errors=(
            manifest.export.allow_reviewed_validation_errors
        ),
    )
    summary: dict[str, int | str] = {
        "dataset_version": str(manifest.export.dataset_version),
        "reviewed_candidates": len(records),
        "exported_items": len(records),
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_jsonl, summary_json, summary


def _revised_candidate_count(output_dir: Path) -> int | None:
    revised = output_dir / "revised_candidates.jsonl"
    if revised.is_file():
        return observe_file(revised).row_count or 0
    revise = output_dir / "revise_candidates.jsonl"
    if revise.is_file() and (observe_file(revise).row_count or 0) == 0:
        return 0
    return None

def _stage_config(manifest: DatasetGenerationManifest, stage_key: str) -> dict[str, Any]:
    if stage_key == "generate_candidates":
        return {
            "cells": manifest.cells.model_dump(mode="json"),
            "generation": manifest.generation.model_dump(mode="json"),
            "generation_context": (
                manifest.generation_context.model_dump(mode="json")
                if manifest.generation_context is not None
                else None
            ),
            "quality": manifest.quality.model_dump(mode="json"),
        }
    if stage_key == "apply_adjudication":
        return {
            "input_artifacts": ["kept_candidates", "adjudication_completed"],
            "output_artifacts": ["adjudicated_candidates", "adjudication_summary"],
        }
    if stage_key == "prepare_revision":
        return {
            "input_artifacts": ["adjudicated_candidates", "adjudication_completed"],
            "output_artifacts": ["revise_candidates", "revision_notes"],
        }
    if stage_key == "apply_revision":
        return {
            "input_artifacts": ["revise_candidates", "revision_notes_completed"],
            "output_artifacts": ["revised_candidates"],
        }
    if stage_key == "prepare_revised_adjudication":
        return {
            "input_artifacts": ["revised_candidates"],
            "output_artifacts": ["revised_adjudication_template"],
        }
    if stage_key == "apply_revised_adjudication":
        return {
            "input_artifacts": [
                "revised_candidates",
                "revised_adjudication_completed",
            ],
            "output_artifacts": [
                "adjudicated_revised_candidates",
                "revised_adjudication_summary",
            ],
        }
    if stage_key == "prepare_manual_review":
        return {
            "input_artifacts": [
                "adjudicated_candidates",
                "adjudicated_revised_candidates",
            ],
            "output_artifacts": [
                "ready_for_manual_review",
                "manual_review_gate_template",
                "unresolved_for_manual_review",
                "manual_review_preparation_summary",
            ],
        }
    if stage_key == "apply_manual_review":
        return {
            "input_artifacts": [
                "ready_for_manual_review",
                "manual_review_completed",
            ],
            "output_artifacts": [
                "reviewed_candidates",
                "manual_review_summary",
            ],
        }
    if stage_key == "export_inspect_jsonl":
        return {
            "input_artifacts": ["reviewed_candidates"],
            "output_artifacts": [
                "inspect_dataset",
                "inspect_export_summary",
            ],
            "dataset_version": manifest.export.dataset_version,
            "output_path": manifest.export.output_path,
        }
    return {"input_artifact": "kept_candidates", "output": "adjudication_template.csv"}


def _repo_relative(path: Path, repo_root: Path) -> str:
    return path.resolve(strict=False).relative_to(repo_root.resolve()).as_posix()


def _record_generation_artifacts(cur, *, run_id: int, stage_id: int, output_dir: Path, repo_root: Path) -> list[str]:
    recorded: list[str] = []
    for artifact_key, filename, artifact_type, artifact_role in GENERATION_ARTIFACTS:
        path = output_dir / filename
        if not path.is_file():
            raise RuntimeError(f"generation did not produce expected artefact: {path}")
        record_artifact(
            cur,
            run_id=run_id,
            artifact_key=artifact_key,
            artifact_path=_repo_relative(path, repo_root),
            artifact_type=artifact_type,
            artifact_role=artifact_role,
            observation=observe_file(path),
            producing_stage_id=stage_id,
        )
        recorded.append(artifact_key)
    return recorded


def _gate_task_message(run_slug: str, gate: dict[str, Any]) -> str:
    template = gate.get("template_path") or "-"
    completed = gate.get("expected_completed_path") or "-"
    instructions = gate.get("instructions") or "Complete the adjudication CSV."
    return (
        f"Review task opened at gate A ({gate.get('gate_key', 'adjudication')}).\n"
        f"Template CSV: {template}\n"
        f"CSV to edit: {completed}\n"
        f"Instructions: {instructions}\n"
        f"Next command: python scripts/moral_gen.py next {run_slug}"
    )


def _revision_gate_task_message(run_slug: str, gate: dict[str, Any]) -> str:
    template = gate.get("template_path") or "-"
    completed = gate.get("expected_completed_path") or "-"
    instructions = gate.get("instructions") or "Complete the revision worksheet."
    return (
        f"Review task opened at gate B ({gate.get('gate_key', 'revision')}).\n"
        f"Template CSV: {template}\n"
        f"CSV to edit: {completed}\n"
        f"Instructions: {instructions}\n"
        f"Next command: python scripts/moral_gen.py next {run_slug}"
    )

def _stage_waiting_for_gate_message(stage_key: str, gate: dict[str, Any]) -> str:
    completed = gate.get("expected_completed_path") or "-"
    instructions = gate.get("instructions") or "Complete the adjudication CSV."
    return (
        f"Stage {stage_key} is waiting for its required human-reviewed CSV.\n"
        f"Required file: {completed}\n"
        f"Instructions: {instructions}"
    )


def advance_one(
    connection,
    run_slug: str,
    *,
    repo_root: str | Path,
    generation_executor: Callable[[DatasetGenerationManifest, Path], None] = execute_generation,
    adjudication_executor: Callable[[DatasetGenerationManifest, Path], Path] = execute_prepare_adjudication,
    apply_adjudication_executor: Callable[
        [DatasetGenerationManifest, Path, Path], tuple[Path, Path, int]
    ] = execute_apply_adjudication,
    prepare_revision_executor: Callable[
        [DatasetGenerationManifest, Path], tuple[Path, Path, int]
    ] = execute_prepare_revision,
    apply_revision_executor: Callable[
        [DatasetGenerationManifest, Path, Path], tuple[Path, int]
    ] = execute_apply_revision,
    revised_adjudication_executor: Callable[
        [DatasetGenerationManifest, Path], Path
    ] = execute_prepare_revised_adjudication,
    apply_revised_adjudication_executor: Callable[
        [DatasetGenerationManifest, Path, Path], tuple[Path, Path, int]
    ] = execute_apply_revised_adjudication,
    prepare_manual_review_executor: Callable[
        [DatasetGenerationManifest, Path],
        tuple[Path, Path, Path, Path, dict[str, int]],
    ] = execute_prepare_manual_review,
    apply_manual_review_executor: Callable[
        [DatasetGenerationManifest, Path, Path],
        tuple[Path, Path, dict[str, int]],
    ] = execute_apply_manual_review,
    export_inspect_jsonl_executor: Callable[
        [DatasetGenerationManifest, Path],
        tuple[Path, Path, dict[str, int | str]],
    ] = execute_export_inspect_jsonl,
) -> NextResult:
    """Advance at most one supported stage for a run."""
    root = Path(repo_root).resolve()
    lock_acquired = False
    with connection.cursor() as cur:
        try:
            acquire_run_lock(cur, run_slug)
            lock_acquired = True
            connection.commit()
            state = load_run_execution_state(cur, run_slug)
            run = state["run"]
            gate = state["open_gate"]
            if run.get("status") == "failed":
                return NextResult(
                    "failed",
                    f"Run {run_slug} is failed: {run.get('last_error') or 'unknown error'}",
                )
            if run.get("current_stage"):
                return NextResult(
                    "in_progress",
                    f"Run {run_slug} still records an active stage: {run['current_stage']}",
                    stage_key=str(run["current_stage"]),
                )

            stage = state["next_stage"]
            if stage is None:
                return NextResult("idle", f"No pending stage for {run_slug}.")
            stage_key = str(stage["stage_key"])
            completed_adjudication_file: Path | None = None
            completed_revision_file: Path | None = None
            completed_revised_adjudication_file: Path | None = None
            completed_manual_review_file: Path | None = None
            if stage_key == "apply_adjudication":
                if gate is None or gate.get("gate_key") != "adjudication":
                    return NextResult(
                        "stage_waiting_gate",
                        "Stage apply_adjudication requires an open adjudication gate.",
                        stage_key=stage_key,
                    )
                completed_path = gate.get("expected_completed_path")
                completed_adjudication_file = root / completed_path if completed_path else None
                if (
                    completed_adjudication_file is None
                    or not completed_adjudication_file.is_file()
                ):
                    return NextResult(
                        "stage_waiting_human",
                        _stage_waiting_for_gate_message(stage_key, gate),
                        stage_key=stage_key,
                        template_path=gate.get("template_path"),
                        completed_path=completed_path,
                    )
            if stage_key == "apply_revision":
                revise_candidates = root / str(run["output_dir"]) / "revise_candidates.jsonl"
                if not revise_candidates.is_file():
                    return NextResult(
                        "stage_waiting_input",
                        f"Stage apply_revision requires {revise_candidates.relative_to(root).as_posix()}.",
                        stage_key=stage_key,
                    )
                if observe_file(revise_candidates).row_count == 0:
                    run_id = int(run["dataset_generation_run_id"])
                    stage_id = int(stage["dataset_generation_stage_id"])
                    mark_stage_skipped(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"revision_candidates": 0, "reason": "no revise candidates"},
                    )
                    connection.commit()
                    return NextResult(
                        "skipped",
                        "Skipped stage: apply_revision; there are no revise candidates.",
                        stage_key=stage_key,
                    )
                if gate is None or gate.get("gate_key") != "revision":
                    return NextResult(
                        "stage_waiting_gate",
                        "Stage apply_revision requires an open revision gate.",
                        stage_key=stage_key,
                    )
                completed_path = gate.get("expected_completed_path")
                completed_revision_file = root / completed_path if completed_path else None
                if completed_revision_file is None or not completed_revision_file.is_file():
                    return NextResult(
                        "stage_waiting_human",
                        _stage_waiting_for_gate_message(stage_key, gate),
                        stage_key=stage_key,
                        template_path=gate.get("template_path"),
                        completed_path=completed_path,
                    )
            if stage_key in {
                "prepare_revised_adjudication",
                "apply_revised_adjudication",
            }:
                output_dir = root / str(run["output_dir"])
                revised_count = _revised_candidate_count(output_dir)
                if revised_count == 0:
                    run_id = int(run["dataset_generation_run_id"])
                    stage_id = int(stage["dataset_generation_stage_id"])
                    mark_stage_skipped(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "revised_candidates": 0,
                            "reason": "no revised candidates",
                        },
                    )
                    connection.commit()
                    return NextResult(
                        "skipped",
                        f"Skipped stage: {stage_key}; there are no revised candidates.",
                        stage_key=stage_key,
                    )
                if revised_count is None:
                    return NextResult(
                        "stage_waiting_input",
                        f"Stage {stage_key} requires "
                        f"{(output_dir / 'revised_candidates.jsonl').relative_to(root).as_posix()}.",
                        stage_key=stage_key,
                    )
            if stage_key == "apply_revised_adjudication":
                if gate is None or gate.get("gate_key") != "revised_adjudication":
                    return NextResult(
                        "stage_waiting_gate",
                        "Stage apply_revised_adjudication requires an open revised_adjudication gate.",
                        stage_key=stage_key,
                    )
                completed_path = gate.get("expected_completed_path")
                completed_revised_adjudication_file = (
                    root / completed_path if completed_path else None
                )
                if (
                    completed_revised_adjudication_file is None
                    or not completed_revised_adjudication_file.is_file()
                ):
                    return NextResult(
                        "stage_waiting_human",
                        _stage_waiting_for_gate_message(stage_key, gate),
                        stage_key=stage_key,
                        template_path=gate.get("template_path"),
                        completed_path=completed_path,
                    )
            if stage_key == "apply_manual_review":
                ready_jsonl = (
                    root / str(run["output_dir"]) / "ready_for_manual_review.jsonl"
                )
                if not ready_jsonl.is_file():
                    return NextResult(
                        "stage_waiting_input",
                        f"Stage apply_manual_review requires "
                        f"{ready_jsonl.relative_to(root).as_posix()}.",
                        stage_key=stage_key,
                    )
                if (observe_file(ready_jsonl).row_count or 0) == 0:
                    run_id = int(run["dataset_generation_run_id"])
                    stage_id = int(stage["dataset_generation_stage_id"])
                    mark_stage_skipped(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "ready_candidates": 0,
                            "reason": "no candidates ready for manual review",
                        },
                    )
                    connection.commit()
                    return NextResult(
                        "skipped",
                        "Skipped stage: apply_manual_review; no candidates are ready "
                        "for manual review.",
                        stage_key=stage_key,
                    )
                if gate is None or gate.get("gate_key") != "manual_review":
                    return NextResult(
                        "stage_waiting_gate",
                        "Stage apply_manual_review requires an open manual_review gate.",
                        stage_key=stage_key,
                    )
                completed_path = gate.get("expected_completed_path")
                completed_manual_review_file = (
                    root / completed_path if completed_path else None
                )
                if (
                    completed_manual_review_file is None
                    or not completed_manual_review_file.is_file()
                ):
                    return NextResult(
                        "stage_waiting_human",
                        _stage_waiting_for_gate_message(stage_key, gate),
                        stage_key=stage_key,
                        template_path=gate.get("template_path"),
                        completed_path=completed_path,
                    )
            if stage_key == "export_inspect_jsonl":
                reviewed_jsonl = (
                    root / str(run["output_dir"]) / "reviewed_candidates.jsonl"
                )
                if not reviewed_jsonl.is_file():
                    run_id = int(run["dataset_generation_run_id"])
                    stage_id = int(stage["dataset_generation_stage_id"])
                    mark_stage_skipped(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "reason": (
                                "reviewed_candidates.jsonl is absent; "
                                "manual review produced no exportable candidates"
                            )
                        },
                    )
                    connection.commit()
                    return NextResult(
                        "skipped",
                        "Skipped stage: export_inspect_jsonl; "
                        "reviewed_candidates.jsonl is absent because manual review "
                        "produced no exportable candidates.",
                        stage_key=stage_key,
                    )
            if stage_key not in SUPPORTED_STAGES:
                return NextResult(
                    "not_implemented",
                    f"Next stage {stage_key!r} is outside the implemented workflow slice.",
                    stage_key=stage_key,
                )

            manifest = manifest_from_snapshot(run["manifest_snapshot"])
            if stage_key == "generate_candidates" and not manifest.safety.allow_model_calls:
                return NextResult(
                    "model_calls_disabled",
                    "Generation remains pending: manifest safety.allow_model_calls is false. "
                    "Set it explicitly to true, then initialise a new run slug because the manifest hash is immutable.",
                    stage_key=stage_key,
                )

            run_id = int(run["dataset_generation_run_id"])
            stage_id = int(stage["dataset_generation_stage_id"])
            mark_stage_running(
                cur,
                run_id=run_id,
                stage_id=stage_id,
                stage_key=stage_key,
                config=_stage_config(manifest, stage_key),
            )
            connection.commit()

            try:
                if stage_key == "generate_candidates":
                    generation_executor(manifest, root)
                    artifact_keys = _record_generation_artifacts(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        output_dir=root / manifest.run.output_dir,
                        repo_root=root,
                    )
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"artifact_keys": artifact_keys},
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        f"Completed stage: {stage_key}. Run next again to prepare adjudication.",
                        stage_key=stage_key,
                    )

                if stage_key == "apply_adjudication":
                    if completed_adjudication_file is None or gate is None:
                        raise RuntimeError("adjudication gate input was not resolved")
                    output_jsonl, summary_json, retained_count = apply_adjudication_executor(
                        manifest, root, completed_adjudication_file
                    )
                    for path in (output_jsonl, summary_json):
                        if not path.is_file():
                            raise RuntimeError(
                                f"adjudication did not produce expected artefact: {path}"
                            )
                    completed_observation = observe_file(completed_adjudication_file)
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="adjudication_completed",
                        artifact_path=_repo_relative(completed_adjudication_file, root),
                        artifact_type="csv",
                        artifact_role="human_gate_completed",
                        observation=completed_observation,
                        human_edited=True,
                        producing_stage_id=stage_id,
                    )
                    output_artifacts = (
                        ("adjudicated_candidates", output_jsonl, "jsonl", "adjudicated_candidates"),
                        ("adjudication_summary", summary_json, "json", "adjudication_summary"),
                    )
                    for artifact_key, path, artifact_type, artifact_role in output_artifacts:
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    completed_artifact_id = artifact_id(
                        cur, run_id=run_id, artifact_key="adjudication_completed"
                    )
                    validation_summary = {
                        "completed_csv_rows": completed_observation.row_count,
                        "retained_candidates": retained_count,
                    }
                    satisfy_adjudication_gate(
                        cur,
                        gate_id=int(gate["dataset_generation_gate_id"]),
                        completed_artifact_id=completed_artifact_id,
                        validation_summary=validation_summary,
                    )
                    artifact_keys = [
                        "adjudication_completed",
                        "adjudicated_candidates",
                        "adjudication_summary",
                    ]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"artifact_keys": artifact_keys, **validation_summary},
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        f"Completed stage: {stage_key}. Gate A is satisfied.",
                        stage_key=stage_key,
                        completed_path=_repo_relative(completed_adjudication_file, root),
                    )
                if stage_key == "prepare_revision":
                    revise_jsonl, revision_notes, revise_count = prepare_revision_executor(
                        manifest, root
                    )
                    for path in (revise_jsonl, revision_notes):
                        if not path.is_file():
                            raise RuntimeError(
                                f"revision preparation did not produce expected artefact: {path}"
                            )
                    revision_artifacts = (
                        ("revise_candidates", revise_jsonl, "jsonl", "revision_candidates"),
                        ("revision_notes", revision_notes, "csv", "human_gate_template"),
                    )
                    for artifact_key, path, artifact_type, artifact_role in revision_artifacts:
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    artifact_keys = ["revise_candidates", "revision_notes"]
                    if revise_count == 0:
                        mark_stage_completed(
                            cur,
                            run_id=run_id,
                            stage_id=stage_id,
                            result={
                                "artifact_keys": artifact_keys,
                                "revision_candidates": 0,
                            },
                        )
                        connection.commit()
                        return NextResult(
                            "completed",
                            "Completed stage: prepare_revision; adjudication produced no revise candidates.",
                            stage_key=stage_key,
                        )
                    notes_id = artifact_id(cur, run_id=run_id, artifact_key="revision_notes")
                    notes_relative = _repo_relative(revision_notes, root)
                    completed_relative = _repo_relative(
                        revision_notes.with_name("revision_notes_completed.csv"), root
                    )
                    instructions = (
                        f"Copy {notes_relative} to {completed_relative}, revise every listed "
                        "candidate, complete revision_notes, then run the next command."
                    )
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "artifact_keys": artifact_keys,
                            "revision_candidates": revise_count,
                        },
                    )
                    open_revision_gate(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        template_artifact_id=notes_id,
                        expected_completed_path=completed_relative,
                        instructions=instructions,
                    )
                    connection.commit()
                    revision_gate = {
                        "gate_key": "revision",
                        "template_path": notes_relative,
                        "expected_completed_path": completed_relative,
                        "instructions": instructions,
                    }
                    return NextResult(
                        "completed",
                        _revision_gate_task_message(run_slug, revision_gate),
                        stage_key=stage_key,
                        template_path=notes_relative,
                        completed_path=completed_relative,
                    )

                if stage_key == "apply_revision":
                    if completed_revision_file is None or gate is None:
                        raise RuntimeError("revision gate input was not resolved")
                    revised_jsonl, revised_count = apply_revision_executor(
                        manifest, root, completed_revision_file
                    )
                    if not revised_jsonl.is_file():
                        raise RuntimeError(
                            f"revision application did not produce expected artefact: {revised_jsonl}"
                        )
                    completed_observation = observe_file(completed_revision_file)
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="revision_notes_completed",
                        artifact_path=_repo_relative(completed_revision_file, root),
                        artifact_type="csv",
                        artifact_role="human_gate_completed",
                        observation=completed_observation,
                        human_edited=True,
                        producing_stage_id=stage_id,
                    )
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="revised_candidates",
                        artifact_path=_repo_relative(revised_jsonl, root),
                        artifact_type="jsonl",
                        artifact_role="revised_candidates",
                        observation=observe_file(revised_jsonl),
                        producing_stage_id=stage_id,
                    )
                    completed_artifact_id = artifact_id(
                        cur, run_id=run_id, artifact_key="revision_notes_completed"
                    )
                    validation_summary = {
                        "completed_csv_rows": completed_observation.row_count,
                        "revised_candidates": revised_count,
                    }
                    satisfy_revision_gate(
                        cur,
                        gate_id=int(gate["dataset_generation_gate_id"]),
                        completed_artifact_id=completed_artifact_id,
                        validation_summary=validation_summary,
                    )
                    artifact_keys = ["revision_notes_completed", "revised_candidates"]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"artifact_keys": artifact_keys, **validation_summary},
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        "Completed stage: apply_revision. Gate B is satisfied; revised candidates await re-adjudication.",
                        stage_key=stage_key,
                        completed_path=_repo_relative(completed_revision_file, root),
                    )
                if stage_key == "prepare_revised_adjudication":
                    template_path = revised_adjudication_executor(manifest, root)
                    if not template_path.is_file():
                        raise RuntimeError(
                            "revised adjudication preparation did not produce expected "
                            f"artefact: {template_path}"
                        )
                    template_relative = _repo_relative(template_path, root)
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="revised_adjudication_template",
                        artifact_path=template_relative,
                        artifact_type="csv",
                        artifact_role="human_gate_template",
                        observation=observe_file(template_path),
                        producing_stage_id=stage_id,
                    )
                    template_id = artifact_id(
                        cur,
                        run_id=run_id,
                        artifact_key="revised_adjudication_template",
                    )
                    completed_relative = _repo_relative(
                        template_path.with_name(
                            "revised_adjudication_completed.csv"
                        ),
                        root,
                    )
                    instructions = (
                        f"Copy {template_relative} to {completed_relative}, adjudicate every "
                        "revised candidate, then run the next command."
                    )
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "artifact_keys": ["revised_adjudication_template"],
                            "revised_candidates": observe_file(
                                root / manifest.run.output_dir / "revised_candidates.jsonl"
                            ).row_count,
                        },
                    )
                    open_revised_adjudication_gate(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        template_artifact_id=template_id,
                        expected_completed_path=completed_relative,
                        instructions=instructions,
                    )
                    connection.commit()
                    revised_gate = {
                        "gate_key": "revised_adjudication",
                        "template_path": template_relative,
                        "expected_completed_path": completed_relative,
                        "instructions": instructions,
                    }
                    return NextResult(
                        "completed",
                        "Review task opened at revised_adjudication gate.\n"
                        f"Template CSV: {template_relative}\n"
                        f"CSV to edit: {completed_relative}\n"
                        f"Instructions: {instructions}\n"
                        f"Next command: python scripts/moral_gen.py next {run_slug}",
                        stage_key=stage_key,
                        template_path=template_relative,
                        completed_path=completed_relative,
                    )

                if stage_key == "apply_revised_adjudication":
                    if completed_revised_adjudication_file is None or gate is None:
                        raise RuntimeError(
                            "revised adjudication gate input was not resolved"
                        )
                    output_jsonl, summary_json, retained_count = (
                        apply_revised_adjudication_executor(
                            manifest,
                            root,
                            completed_revised_adjudication_file,
                        )
                    )
                    for path in (output_jsonl, summary_json):
                        if not path.is_file():
                            raise RuntimeError(
                                "revised adjudication did not produce expected "
                                f"artefact: {path}"
                            )
                    completed_observation = observe_file(
                        completed_revised_adjudication_file
                    )
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="revised_adjudication_completed",
                        artifact_path=_repo_relative(
                            completed_revised_adjudication_file, root
                        ),
                        artifact_type="csv",
                        artifact_role="human_gate_completed",
                        observation=completed_observation,
                        human_edited=True,
                        producing_stage_id=stage_id,
                    )
                    outputs = (
                        (
                            "adjudicated_revised_candidates",
                            output_jsonl,
                            "jsonl",
                            "adjudicated_revised_candidates",
                        ),
                        (
                            "revised_adjudication_summary",
                            summary_json,
                            "json",
                            "revised_adjudication_summary",
                        ),
                    )
                    for artifact_key, path, artifact_type, artifact_role in outputs:
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    completed_artifact_id = artifact_id(
                        cur,
                        run_id=run_id,
                        artifact_key="revised_adjudication_completed",
                    )
                    validation_summary = {
                        "completed_csv_rows": completed_observation.row_count,
                        "retained_candidates": retained_count,
                    }
                    satisfy_revised_adjudication_gate(
                        cur,
                        gate_id=int(gate["dataset_generation_gate_id"]),
                        completed_artifact_id=completed_artifact_id,
                        validation_summary=validation_summary,
                    )
                    artifact_keys = [
                        "revised_adjudication_completed",
                        "adjudicated_revised_candidates",
                        "revised_adjudication_summary",
                    ]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "artifact_keys": artifact_keys,
                            **validation_summary,
                        },
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        "Completed stage: apply_revised_adjudication. "
                        "The revised outputs are not eligible for manual review or export.",
                        stage_key=stage_key,
                        completed_path=_repo_relative(
                            completed_revised_adjudication_file, root
                        ),
                    )
                if stage_key == "prepare_manual_review":
                    (
                        ready_jsonl,
                        template_csv,
                        unresolved_jsonl,
                        summary_json,
                        summary,
                    ) = prepare_manual_review_executor(manifest, root)
                    outputs = (
                        (
                            "ready_for_manual_review",
                            ready_jsonl,
                            "jsonl",
                            "manual_review_input",
                        ),
                        (
                            "manual_review_gate_template",
                            template_csv,
                            "csv",
                            "human_gate_template",
                        ),
                        (
                            "unresolved_for_manual_review",
                            unresolved_jsonl,
                            "jsonl",
                            "unresolved_after_revision",
                        ),
                        (
                            "manual_review_preparation_summary",
                            summary_json,
                            "json",
                            "manual_review_preparation_summary",
                        ),
                    )
                    for artifact_key, path, artifact_type, artifact_role in outputs:
                        if not path.is_file():
                            raise RuntimeError(
                                "manual-review preparation did not produce expected "
                                f"artefact: {path}"
                            )
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    artifact_keys = [item[0] for item in outputs]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"artifact_keys": artifact_keys, **summary},
                    )
                    if summary["ready_total"] > 0:
                        template_id = artifact_id(
                            cur,
                            run_id=run_id,
                            artifact_key="manual_review_gate_template",
                        )
                        completed_relative = _repo_relative(
                            template_csv.with_name("manual_review_completed.csv"),
                            root,
                        )
                        template_relative = _repo_relative(template_csv, root)
                        instructions = (
                            f"Copy {template_relative} to {completed_relative}, "
                            "complete every manual-review row, then run the next command."
                        )
                        open_manual_review_gate(
                            cur,
                            run_id=run_id,
                            stage_id=stage_id,
                            template_artifact_id=template_id,
                            expected_completed_path=completed_relative,
                            instructions=instructions,
                        )
                        connection.commit()
                        return NextResult(
                            "completed",
                            "Review task opened at manual_review gate.\n"
                            f"Template CSV: {template_relative}\n"
                            f"CSV to edit: {completed_relative}\n"
                            f"Instructions: {instructions}\n"
                            f"Next command: python scripts/moral_gen.py next {run_slug}",
                            stage_key=stage_key,
                            template_path=template_relative,
                            completed_path=completed_relative,
                        )
                    connection.commit()
                    return NextResult(
                        "completed",
                        "Completed stage: prepare_manual_review; no candidates are ready, "
                        "so no manual-review gate was opened.",
                        stage_key=stage_key,
                    )
                if stage_key == "apply_manual_review":
                    if completed_manual_review_file is None or gate is None:
                        raise RuntimeError("manual-review gate input was not resolved")
                    reviewed_jsonl, summary_json, summary = (
                        apply_manual_review_executor(
                            manifest,
                            root,
                            completed_manual_review_file,
                        )
                    )
                    for path in (reviewed_jsonl, summary_json):
                        if not path.is_file():
                            raise RuntimeError(
                                "manual review did not produce expected artefact: "
                                f"{path}"
                            )
                    completed_observation = observe_file(
                        completed_manual_review_file
                    )
                    record_artifact(
                        cur,
                        run_id=run_id,
                        artifact_key="manual_review_completed",
                        artifact_path=_repo_relative(
                            completed_manual_review_file, root
                        ),
                        artifact_type="csv",
                        artifact_role="human_gate_completed",
                        observation=completed_observation,
                        human_edited=True,
                        producing_stage_id=stage_id,
                    )
                    outputs = (
                        (
                            "reviewed_candidates",
                            reviewed_jsonl,
                            "jsonl",
                            "reviewed_candidates",
                        ),
                        (
                            "manual_review_summary",
                            summary_json,
                            "json",
                            "manual_review_summary",
                        ),
                    )
                    for artifact_key, path, artifact_type, artifact_role in outputs:
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    completed_artifact_id = artifact_id(
                        cur,
                        run_id=run_id,
                        artifact_key="manual_review_completed",
                    )
                    validation_summary = {
                        "completed_csv_rows": completed_observation.row_count,
                        **summary,
                    }
                    satisfy_manual_review_gate(
                        cur,
                        gate_id=int(gate["dataset_generation_gate_id"]),
                        completed_artifact_id=completed_artifact_id,
                        validation_summary=validation_summary,
                    )
                    artifact_keys = [
                        "manual_review_completed",
                        "reviewed_candidates",
                        "manual_review_summary",
                    ]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={
                            "artifact_keys": artifact_keys,
                            **validation_summary,
                        },
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        "Completed stage: apply_manual_review. "
                        "The manual_review gate is satisfied.",
                        stage_key=stage_key,
                        completed_path=_repo_relative(
                            completed_manual_review_file, root
                        ),
                    )
                if stage_key == "export_inspect_jsonl":
                    output_jsonl, summary_json, summary = (
                        export_inspect_jsonl_executor(manifest, root)
                    )
                    for path in (output_jsonl, summary_json):
                        if not path.is_file():
                            raise RuntimeError(
                                "Inspect export did not produce expected artefact: "
                                f"{path}"
                            )
                    outputs = (
                        (
                            "inspect_dataset",
                            output_jsonl,
                            "jsonl",
                            "inspect_dataset",
                        ),
                        (
                            "inspect_export_summary",
                            summary_json,
                            "json",
                            "inspect_export_summary",
                        ),
                    )
                    for artifact_key, path, artifact_type, artifact_role in outputs:
                        record_artifact(
                            cur,
                            run_id=run_id,
                            artifact_key=artifact_key,
                            artifact_path=_repo_relative(path, root),
                            artifact_type=artifact_type,
                            artifact_role=artifact_role,
                            observation=observe_file(path),
                            producing_stage_id=stage_id,
                        )
                    artifact_keys = [item[0] for item in outputs]
                    mark_stage_completed(
                        cur,
                        run_id=run_id,
                        stage_id=stage_id,
                        result={"artifact_keys": artifact_keys, **summary},
                    )
                    connection.commit()
                    return NextResult(
                        "completed",
                        "Completed stage: export_inspect_jsonl. "
                        f"Exported {summary['exported_items']} reviewed candidate(s).",
                        stage_key=stage_key,
                        completed_path=_repo_relative(output_jsonl, root),
                    )
                template_path = adjudication_executor(manifest, root)
                if not template_path.is_file():
                    raise RuntimeError(f"adjudication template was not created: {template_path}")
                template_relative = _repo_relative(template_path, root)
                completed_relative = _repo_relative(
                    template_path.with_name("adjudication_completed.csv"), root
                )
                record_artifact(
                    cur,
                    run_id=run_id,
                    artifact_key="adjudication_template",
                    artifact_path=template_relative,
                    artifact_type="csv",
                    artifact_role="human_gate_template",
                    observation=observe_file(template_path),
                    producing_stage_id=stage_id,
                )
                template_id = artifact_id(
                    cur, run_id=run_id, artifact_key="adjudication_template"
                )
                instructions = (
                    f"Copy {template_relative} to {completed_relative}, complete every required "
                    "adjudication field, then run the resume command."
                )
                mark_stage_completed(
                    cur,
                    run_id=run_id,
                    stage_id=stage_id,
                    result={"artifact_keys": ["adjudication_template"]},
                )
                open_adjudication_gate(
                    cur,
                    run_id=run_id,
                    stage_id=stage_id,
                    template_artifact_id=template_id,
                    expected_completed_path=completed_relative,
                    instructions=instructions,
                )
                connection.commit()
                gate = {
                    "gate_key": "adjudication",
                    "template_path": template_relative,
                    "expected_completed_path": completed_relative,
                    "instructions": instructions,
                }
                return NextResult(
                    "completed",
                    _gate_task_message(run_slug, gate),
                    stage_key=stage_key,
                    template_path=template_relative,
                    completed_path=completed_relative,
                )
            except Exception as exc:
                connection.rollback()
                error = f"{type(exc).__name__}: {exc}"
                mark_stage_failed(cur, run_id=run_id, stage_id=stage_id, error=error)
                connection.commit()
                raise StageExecutionError(f"stage {stage_key} failed: {exc}") from exc
        finally:
            if lock_acquired:
                release_run_lock(cur, run_slug)
                connection.commit()
