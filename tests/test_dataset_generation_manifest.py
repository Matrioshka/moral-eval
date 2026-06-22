from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from moral_eval.dataset_generation.manifest import load_manifest


def manifest_text(
    *,
    output_dir: str = "data/generated/test_run",
    cells_file: str = "data/generation_cells/test_cells.jsonl",
    generation_extra: str = "",
    safety: str = "",
    workflow: str = "  adjudication: true\n  revisions: true\n  manual_review: true",
    export: str = (
        "  inspect_jsonl: true\n"
        "  dataset_version: test_pilot_v1\n"
        "  output_path: data/generated/test_run/test_pilot_v1.jsonl\n"
        "  allow_reviewed_validation_errors: false"
    ),
) -> str:
    return f"""schema_version: dataset_generation_control_v1
run:
  slug: test_run
  name: Test run
  output_dir: {output_dir}
cells:
  file: {cells_file}
  limit: 2
generation:
  mode: single_pass
  provider: openai-parse
  generator_model: generator-model
  judge_model: judge-model
  seed: 1
  n_per_cell: 1
  max_workers: 1
{generation_extra}{safety}quality:
  min_mean_quality: 8.0
  max_duplicate_risk: 4
  near_duplicate_threshold: 0.86
  allow_validation_errors: false
workflow:
{workflow}
export:
{export}
"""


def write_manifest(repo: Path, text: str | None = None) -> Path:
    cells = repo / "data" / "generation_cells" / "test_cells.jsonl"
    cells.parent.mkdir(parents=True, exist_ok=True)
    cells.write_text('{"domain":"model_release_governance"}\n', encoding="utf-8")
    manifest = repo / "experiments" / "dataset_generation" / "test_run.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(text or manifest_text(), encoding="utf-8")
    return manifest


def test_manifest_acceptance_snapshot_and_stage_order(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)

    loaded = load_manifest(manifest_path, repo_root=tmp_path)

    assert loaded.manifest.run.output_dir == "data/generated/test_run"
    assert loaded.manifest.cells.file == "data/generation_cells/test_cells.jsonl"
    assert loaded.snapshot["manifest_path"] == "experiments/dataset_generation/test_run.yaml"
    assert loaded.snapshot["run"]["slug"] == "test_run"
    assert loaded.planned_stages == [
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
    ]


def test_manifest_rejects_unknown_top_level_and_nested_keys(tmp_path: Path) -> None:
    top_level = write_manifest(tmp_path, manifest_text() + "unexpected: true\n")
    with pytest.raises(ValidationError, match="unexpected"):
        load_manifest(top_level, repo_root=tmp_path)

    nested_text = manifest_text().replace("  name: Test run", "  name: Test run\n  token: secret")
    nested = write_manifest(tmp_path, nested_text)
    with pytest.raises(ValidationError, match="token"):
        load_manifest(nested, repo_root=tmp_path)


def test_manifest_rejects_paths_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-cells.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    manifest_path = write_manifest(
        tmp_path,
        manifest_text(cells_file=outside.as_posix()),
    )

    with pytest.raises(ValueError, match="inside the repository"):
        load_manifest(manifest_path, repo_root=tmp_path)


def test_manifest_hash_uses_stable_raw_bytes(tmp_path: Path) -> None:
    text = manifest_text()
    first = write_manifest(tmp_path, text)
    first_hash = load_manifest(first, repo_root=tmp_path).manifest_sha256
    second_hash = load_manifest(first, repo_root=tmp_path).manifest_sha256
    assert first_hash == second_hash

    first.write_text(text + "\n", encoding="utf-8")
    assert load_manifest(first, repo_root=tmp_path).manifest_sha256 != first_hash


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            manifest_text(generation_extra="  target_kept: 3\n"),
            "only valid in quota mode",
        ),
        (
            manifest_text(
                generation_extra="  mode: quota\n",
            ).replace("  mode: single_pass\n", ""),
            "quota mode requires",
        ),
        (
            manifest_text(
                workflow="  adjudication: false\n  revisions: true\n  manual_review: true"
            ),
            "revisions require adjudication",
        ),
        (
            manifest_text(
                workflow="  adjudication: true\n  revisions: false\n  manual_review: false"
            ),
            "requires manual_review",
        ),
        (
            manifest_text(
                export=(
                    "  inspect_jsonl: false\n"
                    "  dataset_version: unwanted\n"
                    "  output_path: data/generated/test_run/unwanted.jsonl"
                )
            ),
            "must be omitted",
        ),
    ],
)
def test_manifest_cross_field_validation(tmp_path: Path, text: str, message: str) -> None:
    manifest_path = write_manifest(tmp_path, text)
    with pytest.raises(ValidationError, match=message):
        load_manifest(manifest_path, repo_root=tmp_path)


def test_disabled_workflow_sections_omit_stages(tmp_path: Path) -> None:
    manifest_path = write_manifest(
        tmp_path,
        manifest_text(
            workflow="  adjudication: false\n  revisions: false\n  manual_review: false",
            export="  inspect_jsonl: false",
        ),
    )
    loaded = load_manifest(manifest_path, repo_root=tmp_path)
    assert loaded.planned_stages == ["generate_candidates"]

def test_model_call_safety_defaults_false_and_requires_explicit_opt_in(tmp_path: Path) -> None:
    default_manifest = load_manifest(write_manifest(tmp_path), repo_root=tmp_path)
    assert default_manifest.manifest.safety.allow_model_calls is False
    assert default_manifest.snapshot["safety"] == {"allow_model_calls": False}

    opted_in = write_manifest(
        tmp_path,
        manifest_text(safety="safety:\n  allow_model_calls: true\n"),
    )
    assert load_manifest(opted_in, repo_root=tmp_path).manifest.safety.allow_model_calls is True
