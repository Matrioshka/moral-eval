"""Strict manifest parsing for dataset-generation control runs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

SCHEMA_VERSION = "dataset_generation_control_v1"
RUN_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunManifest(StrictModel):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    output_dir: str = Field(min_length=1)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        value = value.strip()
        if not RUN_SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must contain only lowercase letters, digits, hyphens, and underscores")
        return value

    @field_validator("name", "output_dir")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field cannot be blank")
        return value


class CellsManifest(StrictModel):
    file: str = Field(min_length=1)
    limit: PositiveInt | None = None


class GenerationManifest(StrictModel):
    mode: Literal["single_pass", "quota"] = "single_pass"
    provider: Literal["openai-parse", "openai-compatible-json"] = "openai-parse"
    base_url: str | None = None
    generator_model: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    seed: int = 1
    n_per_cell: PositiveInt = 1
    max_workers: PositiveInt = 1
    target_kept: PositiveInt | None = None
    max_batches: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "GenerationManifest":
        if self.mode == "quota" and (self.target_kept is None or self.max_batches is None):
            raise ValueError("quota mode requires target_kept and max_batches")
        if self.mode == "single_pass" and (
            self.target_kept is not None or self.max_batches is not None
        ):
            raise ValueError("target_kept and max_batches are only valid in quota mode")
        return self


class QualityManifest(StrictModel):
    min_mean_quality: float = Field(default=8.0, ge=0, le=10)
    max_duplicate_risk: int = Field(default=4, ge=0, le=10)
    near_duplicate_threshold: float = Field(default=0.86, ge=0, le=1)
    allow_validation_errors: bool = False


class WorkflowManifest(StrictModel):
    adjudication: bool = True
    revisions: bool = True
    manual_review: bool = True

    @model_validator(mode="after")
    def validate_dependencies(self) -> "WorkflowManifest":
        if self.revisions and not self.adjudication:
            raise ValueError("revisions require adjudication")
        return self


class ExportManifest(StrictModel):
    inspect_jsonl: bool = True
    dataset_version: str | None = None
    output_path: str | None = None
    allow_reviewed_validation_errors: bool = False

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "ExportManifest":
        if self.inspect_jsonl:
            if not self.dataset_version or not self.dataset_version.strip():
                raise ValueError("dataset_version is required when inspect_jsonl is enabled")
            if not self.output_path or not self.output_path.strip():
                raise ValueError("output_path is required when inspect_jsonl is enabled")
        elif self.dataset_version is not None or self.output_path is not None:
            raise ValueError(
                "dataset_version and output_path must be omitted when inspect_jsonl is disabled"
            )
        return self


class DatasetGenerationManifest(StrictModel):
    schema_version: Literal["dataset_generation_control_v1"]
    run: RunManifest
    cells: CellsManifest
    generation: GenerationManifest
    quality: QualityManifest = Field(default_factory=QualityManifest)
    workflow: WorkflowManifest = Field(default_factory=WorkflowManifest)
    export: ExportManifest

    @model_validator(mode="after")
    def validate_workflow(self) -> "DatasetGenerationManifest":
        if self.export.inspect_jsonl and not self.workflow.manual_review:
            raise ValueError("Inspect JSONL export requires manual_review")
        return self


@dataclass(frozen=True)
class LoadedManifest:
    manifest: DatasetGenerationManifest
    manifest_path: Path
    manifest_sha256: str
    snapshot: dict[str, Any]
    repo_root: Path

    @property
    def planned_stages(self) -> list[str]:
        return planned_stage_keys(self.manifest)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_repo_path(
    value: str | Path,
    *,
    repo_root: Path,
    label: str,
    must_exist: bool,
) -> tuple[Path, str]:
    path = Path(value)
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"{label} must resolve inside the repository: {value}") from exc
    if must_exist and not resolved.exists():
        raise ValueError(f"{label} does not exist: {relative.as_posix()}")
    if must_exist and not resolved.is_file():
        raise ValueError(f"{label} must be a file: {relative.as_posix()}")
    return resolved, relative.as_posix()


def load_manifest(path: str | Path, *, repo_root: str | Path) -> LoadedManifest:
    root = Path(repo_root).resolve()
    manifest_path, manifest_relative = _safe_repo_path(
        path,
        repo_root=root,
        label="manifest path",
        must_exist=True,
    )
    raw_bytes = manifest_path.read_bytes()
    raw_data = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError("manifest must contain a YAML mapping")

    manifest = DatasetGenerationManifest.model_validate(raw_data)
    _, cells_relative = _safe_repo_path(
        manifest.cells.file,
        repo_root=root,
        label="cells file",
        must_exist=True,
    )
    _, output_relative = _safe_repo_path(
        manifest.run.output_dir,
        repo_root=root,
        label="output directory",
        must_exist=False,
    )

    export = manifest.export
    export_relative: str | None = None
    if export.output_path is not None:
        _, export_relative = _safe_repo_path(
            export.output_path,
            repo_root=root,
            label="export output path",
            must_exist=False,
        )

    normalised = manifest.model_copy(
        update={
            "run": manifest.run.model_copy(update={"output_dir": output_relative}),
            "cells": manifest.cells.model_copy(update={"file": cells_relative}),
            "export": export.model_copy(update={"output_path": export_relative}),
        }
    )
    snapshot = normalised.model_dump(mode="json", exclude_none=False)
    snapshot["manifest_path"] = manifest_relative
    return LoadedManifest(
        manifest=normalised,
        manifest_path=manifest_path,
        manifest_sha256=sha256_bytes(raw_bytes),
        snapshot=snapshot,
        repo_root=root,
    )


def planned_stage_keys(manifest: DatasetGenerationManifest) -> list[str]:
    stages = ["generate_candidates"]
    if manifest.workflow.adjudication:
        stages.extend(["prepare_adjudication", "apply_adjudication"])
    if manifest.workflow.revisions:
        stages.extend(["prepare_revision", "apply_revision"])
    if manifest.workflow.manual_review:
        stages.extend(["prepare_manual_review", "apply_manual_review"])
    if manifest.export.inspect_jsonl:
        stages.append("export_inspect_jsonl")
    return stages
