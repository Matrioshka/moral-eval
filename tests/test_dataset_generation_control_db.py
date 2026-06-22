from __future__ import annotations

import csv
from pathlib import Path

import pytest

from moral_eval.dataset_generation import control_db
from moral_eval.dataset_generation.control_db import (
    ManifestConflictError,
    RunLockUnavailableError,
    acquire_run_lock,
    artifact_drift,
    count_file_rows,
    initialise_run,
    observe_file,
    open_revision_gate,
    open_adjudication_gate,
    sha256_file,
    satisfy_adjudication_gate,
    satisfy_revision_gate,
)
from moral_eval.dataset_generation.manifest import load_manifest


def make_loaded_manifest(repo: Path):
    cells = repo / "cells.jsonl"
    cells.write_text('{"cell":1}\n\n{"cell":2}\n', encoding="utf-8")
    manifest = repo / "manifest.yaml"
    manifest.write_text(
        """schema_version: dataset_generation_control_v1
run:
  slug: db_test
  name: DB test
  output_dir: data/generated/db_test
cells:
  file: cells.jsonl
generation:
  mode: single_pass
  provider: openai-parse
  generator_model: generator
  judge_model: judge
  n_per_cell: 1
  max_workers: 1
quality: {}
workflow:
  adjudication: false
  revisions: false
  manual_review: false
export:
  inspect_jsonl: false
""",
        encoding="utf-8",
    )
    return load_manifest(manifest, repo_root=repo)


class StateCursor:
    def __init__(self):
        self.run = None
        self.stages: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.artifact_parameter_counts: list[int] = []
        self._one = None

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        params = params or ()
        if sql.startswith("SELECT * FROM public.dataset_generation_run"):
            self._one = self.run
        elif sql.startswith("INSERT INTO public.dataset_generation_run"):
            self.run = {
                "dataset_generation_run_id": 1,
                "run_slug": params[0],
                "name": params[1],
                "schema_version": params[2],
                "status": "initialised",
                "output_dir": params[3],
                "manifest_path": params[4],
                "manifest_sha256": params[5],
                "manifest_snapshot": params[6],
                "git_commit": params[7],
            }
            self._one = self.run
        elif sql.startswith("INSERT INTO public.dataset_generation_stage"):
            self.stages.setdefault(
                params[1],
                {"run_id": params[0], "stage_key": params[1], "ordinal": params[2]},
            )
        elif sql.startswith("INSERT INTO public.dataset_generation_artifact"):
            self.artifact_parameter_counts.append(len(params))
            self.artifacts[params[1]] = {
                "run_id": params[0],
                "artifact_path": params[2],
                "sha256": params[5],
                "byte_count": params[6],
                "row_count": params[7],
            }
        else:  # pragma: no cover
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return self._one


def test_file_hash_and_row_counts(tmp_path: Path) -> None:
    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text('{"a":1}\n\n{"a":2}\n', encoding="utf-8")
    csv_path = tmp_path / "rows.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "value"])
        writer.writerow(["1", "a"])
        writer.writerow([])
        writer.writerow(["2", "b"])

    assert sha256_file(jsonl) == observe_file(jsonl).sha256
    assert observe_file(jsonl).byte_count == jsonl.stat().st_size
    assert count_file_rows(jsonl) == 2
    assert count_file_rows(csv_path) == 2
    assert count_file_rows(tmp_path / "other.txt") is None
    with pytest.raises(FileNotFoundError):
        observe_file(tmp_path / "missing.jsonl")


def test_init_is_idempotent_and_does_not_duplicate_state(tmp_path: Path, monkeypatch) -> None:
    loaded = make_loaded_manifest(tmp_path)
    cursor = StateCursor()
    monkeypatch.setattr(control_db, "get_git_commit", lambda _root: "abc123")

    first, created = initialise_run(cursor, loaded)
    second, created_again = initialise_run(cursor, loaded)

    assert created is True
    assert created_again is False
    assert first["dataset_generation_run_id"] == second["dataset_generation_run_id"]
    assert list(cursor.stages) == ["generate_candidates"]
    assert set(cursor.artifacts) == {"manifest", "cells"}
    assert cursor.artifact_parameter_counts == [11, 11, 11, 11]


def test_changed_manifest_rejected_for_existing_slug(tmp_path: Path, monkeypatch) -> None:
    first = make_loaded_manifest(tmp_path)
    cursor = StateCursor()
    monkeypatch.setattr(control_db, "get_git_commit", lambda _root: None)
    initialise_run(cursor, first)

    changed_path = tmp_path / "manifest.yaml"
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8").replace("name: DB test", "name: Changed"),
        encoding="utf-8",
    )
    changed = load_manifest(changed_path, repo_root=tmp_path)
    with pytest.raises(ManifestConflictError, match="different manifest hash"):
        initialise_run(cursor, changed)


def test_artifact_drift_states(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "artifact.jsonl"
    path.write_text('{"a":1}\n', encoding="utf-8")
    artifact = {"artifact_path": "artifact.jsonl", "sha256": sha256_file(path)}
    assert artifact_drift(artifact, repo_root=tmp_path) == "unchanged"

    path.write_text('{"a":2}\n', encoding="utf-8")
    assert artifact_drift(artifact, repo_root=tmp_path) == "modified"
    path.unlink()
    assert artifact_drift(artifact, repo_root=tmp_path) == "missing"

    path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(control_db, "sha256_file", lambda _path: (_ for _ in ()).throw(OSError()))
    assert artifact_drift(artifact, repo_root=tmp_path) == "unreadable"


def test_migration_has_expected_safe_control_schema() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "sql"
        / "023_create_dataset_generation_control_tables.sql"
    ).read_text(encoding="utf-8")

    for table in (
        "public.dataset_generation_run",
        "public.dataset_generation_stage",
        "public.dataset_generation_artifact",
        "public.dataset_generation_gate",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    assert "manifest_snapshot jsonb NOT NULL" in migration
    assert "stage_config jsonb NOT NULL" in migration
    assert "artifact_metadata jsonb NOT NULL" in migration
    assert "validation_summary jsonb NOT NULL" in migration
    assert "ON DELETE CASCADE" in migration
    assert "CHECK (status IN" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "TRUNCATE" not in migration.upper()
    assert "CREATE SCHEMA" not in migration.upper()

class LockCursor:
    def __init__(self, acquired: bool):
        self.acquired = acquired
        self.query = ""
        self.params = ()

    def execute(self, query, params=None):
        self.query = str(query)
        self.params = params or ()

    def fetchone(self):
        return {"acquired": self.acquired}


def test_advisory_lock_uses_run_slug_and_rejects_contention() -> None:
    cursor = LockCursor(True)
    acquire_run_lock(cursor, "run_slug")
    assert "pg_try_advisory_lock" in cursor.query
    assert cursor.params == ("run_slug",)

    with pytest.raises(RunLockUnavailableError, match="already being advanced"):
        acquire_run_lock(LockCursor(False), "run_slug")

class RecordingCursor:
    def __init__(self, *, rowcount: int = 1):
        self.statements: list[str] = []
        self.rowcount = rowcount

    def execute(self, query, _params=None):
        self.statements.append(" ".join(str(query).split()))


def test_open_adjudication_gate_does_not_change_run_status() -> None:
    cursor = RecordingCursor()

    open_adjudication_gate(
        cursor,
        run_id=1,
        stage_id=2,
        template_artifact_id=3,
        expected_completed_path="data/generated/run/adjudication_completed.csv",
        instructions="Complete the CSV.",
    )

    assert len(cursor.statements) == 1
    assert cursor.statements[0].startswith(
        "INSERT INTO public.dataset_generation_gate"
    )
    assert "UPDATE public.dataset_generation_run" not in cursor.statements[0]
    assert "waiting_human" not in cursor.statements[0]

def test_satisfy_adjudication_gate_records_completed_artifact_and_summary() -> None:
    cursor = RecordingCursor()

    satisfy_adjudication_gate(
        cursor,
        gate_id=4,
        completed_artifact_id=5,
        validation_summary={"retained_candidates": 1},
    )

    assert len(cursor.statements) == 1
    assert "SET status = 'satisfied'" in cursor.statements[0]
    assert "completed_artifact_id = %s" in cursor.statements[0]
    assert "resolved_at = now()" in cursor.statements[0]

def test_satisfy_adjudication_gate_rejects_zero_row_update() -> None:
    cursor = RecordingCursor(rowcount=0)

    with pytest.raises(RuntimeError, match="was not open or could not be satisfied"):
        satisfy_adjudication_gate(
            cursor,
            gate_id=4,
            completed_artifact_id=5,
            validation_summary={"retained_candidates": 1},
        )

def test_open_revision_gate_does_not_change_run_status() -> None:
    cursor = RecordingCursor()

    open_revision_gate(
        cursor,
        run_id=1,
        stage_id=2,
        template_artifact_id=3,
        expected_completed_path="data/generated/run/revision_notes_completed.csv",
        instructions="Complete the revision worksheet.",
    )

    assert len(cursor.statements) == 1
    assert "'revision', 'human_csv_review', 'open'" in cursor.statements[0]
    assert "UPDATE public.dataset_generation_run" not in cursor.statements[0]
    assert "waiting_human" not in cursor.statements[0]


def test_satisfy_revision_gate_requires_one_open_gate() -> None:
    cursor = RecordingCursor()
    satisfy_revision_gate(
        cursor,
        gate_id=6,
        completed_artifact_id=7,
        validation_summary={"revised_candidates": 1},
    )
    assert "gate_key = 'revision'" in cursor.statements[0]
    assert "SET status = 'satisfied'" in cursor.statements[0]

    with pytest.raises(RuntimeError, match="revision gate 6 was not open"):
        satisfy_revision_gate(
            RecordingCursor(rowcount=0),
            gate_id=6,
            completed_artifact_id=7,
            validation_summary={"revised_candidates": 1},
        )
