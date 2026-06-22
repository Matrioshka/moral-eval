"""Advance manifest-driven dataset generation through the first human gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .adjudication import apply_adjudication as apply_adjudication_file
from .adjudication import write_adjudication_template
from .control_db import (
    acquire_run_lock,
    artifact_id,
    load_run_execution_state,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    observe_file,
    open_adjudication_gate,
    record_artifact,
    release_run_lock,
    satisfy_adjudication_gate,
)
from .llm_clients import OpenAICompatibleJSONClient, OpenAIParseClient
from .manifest import DatasetGenerationManifest, manifest_from_snapshot
from .pipeline import generate_score_filter_export
from .quota_generation import generate_until_quota
from .schemas import MatrixCell

SUPPORTED_STAGES = {"generate_candidates", "prepare_adjudication", "apply_adjudication"}
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

def _stage_config(manifest: DatasetGenerationManifest, stage_key: str) -> dict[str, Any]:
    if stage_key == "generate_candidates":
        return {
            "cells": manifest.cells.model_dump(mode="json"),
            "generation": manifest.generation.model_dump(mode="json"),
            "quality": manifest.quality.model_dump(mode="json"),
        }
    if stage_key == "apply_adjudication":
        return {
            "input_artifacts": ["kept_candidates", "adjudication_completed"],
            "output_artifacts": ["adjudicated_candidates", "adjudication_summary"],
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