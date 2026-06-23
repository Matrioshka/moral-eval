"""PostgreSQL control helpers for dataset-generation manifests."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .manifest import LoadedManifest

DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"
MIGRATION_NAME = "023_create_dataset_generation_control_tables.sql"


class ManifestConflictError(RuntimeError):
    """Raised when an existing run slug is reused with a changed manifest."""


class RunNotFoundError(RuntimeError):
    """Raised when a dataset-generation run slug is unknown."""


class RunLockUnavailableError(RuntimeError):
    """Raised when another process holds the run-level advisory lock."""


@dataclass(frozen=True)
class ArtifactObservation:
    sha256: str
    byte_count: int
    row_count: int | None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def connect_db(*, repo_root: str | Path, dsn: str | None = None):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Install dependency first: python -m pip install "psycopg[binary]"') from exc

    root = Path(repo_root).resolve()
    load_env_file(root / ".env")
    url = dsn or os.getenv(DATABASE_URL_ENV)
    if url:
        return psycopg.connect(url, row_factory=dict_row)

    params = {
        "host": os.getenv("PGHOST") or os.getenv("PG_HOST"),
        "port": os.getenv("PGPORT") or os.getenv("PG_PORT"),
        "dbname": os.getenv("PGDATABASE") or os.getenv("PG_DATABASE"),
        "user": os.getenv("PGUSER") or os.getenv("PG_USER"),
        "password": os.getenv("PGPASSWORD") or os.getenv("PG_PASSWORD"),
    }
    params = {key: value for key, value in params.items() if value}
    if not params:
        raise RuntimeError(
            "No PostgreSQL connection configured. Set MORAL_EVALS_DATABASE_URL or "
            "PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD."
        )
    return psycopg.connect(**params, row_factory=dict_row)


def apply_migration(cur, *, repo_root: str | Path) -> None:
    migration = Path(repo_root).resolve() / "sql" / MIGRATION_NAME
    cur.execute(migration.read_text(encoding="utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_file_rows(path: str | Path) -> int | None:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for row in reader if any(cell.strip() for cell in row))
    return None


def observe_file(path: str | Path) -> ArtifactObservation:
    path = Path(path)
    return ArtifactObservation(
        sha256=sha256_file(path),
        byte_count=path.stat().st_size,
        row_count=count_file_rows(path),
    )


def get_git_commit(repo_root: str | Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def create_or_load_run(
    cur,
    loaded: LoadedManifest,
    *,
    git_commit: str | None,
) -> tuple[dict[str, Any], bool]:
    slug = loaded.manifest.run.slug
    cur.execute(
        """
        SELECT *
        FROM public.dataset_generation_run
        WHERE run_slug = %s
        """,
        (slug,),
    )
    existing = cur.fetchone()
    if existing is not None:
        existing = dict(existing)
        if existing["manifest_sha256"] != loaded.manifest_sha256:
            raise ManifestConflictError(
                f"run slug {slug!r} already exists with a different manifest hash"
            )
        return existing, False

    cur.execute(
        """
        INSERT INTO public.dataset_generation_run (
            run_slug,
            name,
            schema_version,
            status,
            output_dir,
            manifest_path,
            manifest_sha256,
            manifest_snapshot,
            git_commit
        )
        VALUES (%s, %s, %s, 'initialised', %s, %s, %s, %s::jsonb, %s)
        RETURNING *
        """,
        (
            slug,
            loaded.manifest.run.name,
            loaded.manifest.schema_version,
            loaded.manifest.run.output_dir,
            loaded.snapshot["manifest_path"],
            loaded.manifest_sha256,
            json.dumps(loaded.snapshot, ensure_ascii=False, sort_keys=True),
            git_commit,
        ),
    )
    return dict(cur.fetchone()), True


def initialise_stages(cur, run_id: int, stage_keys: Iterable[str]) -> None:
    for ordinal, stage_key in enumerate(stage_keys, 1):
        cur.execute(
            """
            INSERT INTO public.dataset_generation_stage (
                dataset_generation_run_id,
                stage_key,
                ordinal,
                status
            )
            VALUES (%s, %s, %s, 'pending')
            ON CONFLICT (dataset_generation_run_id, stage_key) DO NOTHING
            """,
            (run_id, stage_key, ordinal),
        )


def record_artifact(
    cur,
    *,
    run_id: int,
    artifact_key: str,
    artifact_path: str,
    artifact_type: str,
    artifact_role: str,
    observation: ArtifactObservation,
    human_edited: bool = False,
    metadata: dict[str, Any] | None = None,
    producing_stage_id: int | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO public.dataset_generation_artifact (
            dataset_generation_run_id,
            artifact_key,
            artifact_path,
            artifact_type,
            artifact_role,
            sha256,
            byte_count,
            row_count,
            human_edited,
            artifact_metadata,
            producing_stage_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (dataset_generation_run_id, artifact_key) DO UPDATE SET
            artifact_path = EXCLUDED.artifact_path,
            artifact_type = EXCLUDED.artifact_type,
            artifact_role = EXCLUDED.artifact_role,
            sha256 = EXCLUDED.sha256,
            byte_count = EXCLUDED.byte_count,
            row_count = EXCLUDED.row_count,
            human_edited = EXCLUDED.human_edited,
            artifact_metadata = EXCLUDED.artifact_metadata,
            producing_stage_id = EXCLUDED.producing_stage_id,
            observed_at = now(),
            updated_at = now()
        """,
        (
            run_id,
            artifact_key,
            artifact_path,
            artifact_type,
            artifact_role,
            observation.sha256,
            observation.byte_count,
            observation.row_count,
            human_edited,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            producing_stage_id,
        ),
    )


def initialise_run(cur, loaded: LoadedManifest) -> tuple[dict[str, Any], bool]:
    run, created = create_or_load_run(
        cur,
        loaded,
        git_commit=get_git_commit(loaded.repo_root),
    )
    run_id = int(run["dataset_generation_run_id"])
    initialise_stages(cur, run_id, loaded.planned_stages)

    manifest_relative = str(loaded.snapshot["manifest_path"])
    record_artifact(
        cur,
        run_id=run_id,
        artifact_key="manifest",
        artifact_path=manifest_relative,
        artifact_type="yaml",
        artifact_role="input_manifest",
        observation=observe_file(loaded.manifest_path),
        metadata={"schema_version": loaded.manifest.schema_version},
    )
    cells_relative = loaded.manifest.cells.file
    record_artifact(
        cur,
        run_id=run_id,
        artifact_key="cells",
        artifact_path=cells_relative,
        artifact_type=Path(cells_relative).suffix.lower().lstrip(".") or "file",
        artifact_role="input_cells",
        observation=observe_file(loaded.repo_root / cells_relative),
    )
    return run, created


def _load_run(cur, run_slug: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT *
        FROM public.dataset_generation_run
        WHERE run_slug = %s
        """,
        (run_slug,),
    )
    row = cur.fetchone()
    if row is None:
        raise RunNotFoundError(f"dataset-generation run not found: {run_slug}")
    return dict(row)


def reconcile_run_completion(cur, *, run_id: int) -> bool:
    """Mark a run completed only when every planned stage is terminal and unblocked."""
    cur.execute(
        """
        UPDATE public.dataset_generation_run AS run
        SET status = 'completed',
            current_stage = NULL,
            completed_at = COALESCE(completed_at, now()),
            updated_at = now()
        WHERE run.dataset_generation_run_id = %s
          AND run.status NOT IN ('completed', 'failed')
          AND run.current_stage IS NULL
          AND run.last_error IS NULL
          AND EXISTS (
              SELECT 1
              FROM public.dataset_generation_stage AS stage
              WHERE stage.dataset_generation_run_id = run.dataset_generation_run_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM public.dataset_generation_stage AS stage
              WHERE stage.dataset_generation_run_id = run.dataset_generation_run_id
                AND stage.status NOT IN ('completed', 'skipped')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM public.dataset_generation_gate AS gate
              WHERE gate.dataset_generation_run_id = run.dataset_generation_run_id
                AND gate.status = 'open'
          )
        """,
        (run_id,),
    )
    return cur.rowcount == 1


def read_run_status(cur, run_slug: str) -> dict[str, Any]:
    run = _load_run(cur, run_slug)
    run_id = int(run["dataset_generation_run_id"])
    if reconcile_run_completion(cur, run_id=run_id):
        run = _load_run(cur, run_slug)
    cur.execute(
        """
        SELECT stage_key, ordinal, status, error
        FROM public.dataset_generation_stage
        WHERE dataset_generation_run_id = %s
          AND status = 'pending'
        ORDER BY ordinal
        LIMIT 1
        """,
        (run_id,),
    )
    next_stage = cur.fetchone()
    cur.execute(
        """
        SELECT gate_key, gate_type, status, expected_completed_path, instructions
        FROM public.dataset_generation_gate
        WHERE dataset_generation_run_id = %s
        ORDER BY opened_at DESC, dataset_generation_gate_id DESC
        LIMIT 1
        """,
        (run_id,),
    )
    gate_row = cur.fetchone()
    gate_status = dict(gate_row) if gate_row is not None else None
    return {
        "run": run,
        "next_stage": dict(next_stage) if next_stage is not None else None,
        "open_gate": gate_status if gate_status and gate_status["status"] == "open" else None,
        "gate_status": gate_status,
    }


def artifact_drift(
    artifact: dict[str, Any],
    *,
    repo_root: str | Path,
) -> str:
    path = Path(str(artifact["artifact_path"]))
    absolute = path if path.is_absolute() else Path(repo_root).resolve() / path
    if not absolute.exists():
        return "missing"
    try:
        current_hash = sha256_file(absolute)
    except OSError:
        return "unreadable"
    return "unchanged" if current_hash == artifact.get("sha256") else "modified"


def list_artifacts(
    cur,
    run_slug: str,
    *,
    repo_root: str | Path,
) -> list[dict[str, Any]]:
    run = _load_run(cur, run_slug)
    cur.execute(
        """
        SELECT
            artifact_key,
            artifact_path,
            artifact_type,
            artifact_role,
            sha256,
            byte_count,
            row_count,
            human_edited,
            artifact_metadata,
            observed_at
        FROM public.dataset_generation_artifact
        WHERE dataset_generation_run_id = %s
        ORDER BY artifact_key
        """,
        (run["dataset_generation_run_id"],),
    )
    artifacts = [dict(row) for row in cur.fetchall()]
    for artifact in artifacts:
        artifact["drift"] = artifact_drift(artifact, repo_root=repo_root)
    return artifacts


def acquire_run_lock(cur, run_slug: str) -> None:
    """Acquire a session-level advisory lock scoped to one run slug."""
    cur.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired",
        (run_slug,),
    )
    row = cur.fetchone()
    acquired = row.get("acquired") if isinstance(row, dict) else row[0]
    if not acquired:
        raise RunLockUnavailableError(
            f"dataset-generation run is already being advanced: {run_slug}"
        )


def release_run_lock(cur, run_slug: str) -> None:
    cur.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
        (run_slug,),
    )


def load_run_execution_state(cur, run_slug: str) -> dict[str, Any]:
    run = _load_run(cur, run_slug)
    run_id = int(run["dataset_generation_run_id"])
    if reconcile_run_completion(cur, run_id=run_id):
        run = _load_run(cur, run_slug)
    cur.execute(
        """
        SELECT *
        FROM public.dataset_generation_stage
        WHERE dataset_generation_run_id = %s
          AND status = 'pending'
        ORDER BY ordinal
        LIMIT 1
        """,
        (run_id,),
    )
    next_stage = cur.fetchone()
    cur.execute(
        """
        SELECT gate.*, template.artifact_path AS template_path
        FROM public.dataset_generation_gate AS gate
        LEFT JOIN public.dataset_generation_artifact AS template
          ON template.dataset_generation_artifact_id = gate.template_artifact_id
        WHERE gate.dataset_generation_run_id = %s
          AND gate.status = 'open'
        ORDER BY gate.opened_at, gate.dataset_generation_gate_id
        LIMIT 1
        """,
        (run_id,),
    )
    open_gate = cur.fetchone()
    return {
        "run": run,
        "next_stage": dict(next_stage) if next_stage is not None else None,
        "open_gate": dict(open_gate) if open_gate is not None else None,
    }


def mark_stage_running(
    cur, *, run_id: int, stage_id: int, stage_key: str, config: dict[str, Any]
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_stage
        SET status = 'running', stage_config = %s::jsonb, error = NULL,
            started_at = now(), completed_at = NULL, updated_at = now()
        WHERE dataset_generation_stage_id = %s
        """,
        (json.dumps(config, ensure_ascii=False, sort_keys=True), stage_id),
    )
    cur.execute(
        """
        UPDATE public.dataset_generation_run
        SET status = 'running', current_stage = %s, last_error = NULL,
            started_at = COALESCE(started_at, now()), updated_at = now()
        WHERE dataset_generation_run_id = %s
        """,
        (stage_key, run_id),
    )


def mark_stage_completed(
    cur, *, run_id: int, stage_id: int, result: dict[str, Any]
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_stage
        SET status = 'completed', stage_result = %s::jsonb, error = NULL,
            completed_at = now(), updated_at = now()
        WHERE dataset_generation_stage_id = %s
        """,
        (json.dumps(result, ensure_ascii=False, sort_keys=True), stage_id),
    )
    cur.execute(
        """
        UPDATE public.dataset_generation_run
        SET status = 'running', current_stage = NULL, last_error = NULL, updated_at = now()
        WHERE dataset_generation_run_id = %s
        """,
        (run_id,),
    )
    reconcile_run_completion(cur, run_id=run_id)


def mark_stage_skipped(
    cur, *, run_id: int, stage_id: int, result: dict[str, Any]
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_stage
        SET status = 'skipped', stage_result = %s::jsonb, error = NULL,
            completed_at = now(), updated_at = now()
        WHERE dataset_generation_stage_id = %s
        """,
        (json.dumps(result, ensure_ascii=False, sort_keys=True), stage_id),
    )
    cur.execute(
        """
        UPDATE public.dataset_generation_run
        SET status = 'running', current_stage = NULL, last_error = NULL, updated_at = now()
        WHERE dataset_generation_run_id = %s
        """,
        (run_id,),
    )
    reconcile_run_completion(cur, run_id=run_id)

def mark_stage_failed(cur, *, run_id: int, stage_id: int, error: str) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_stage
        SET status = 'failed', error = %s, completed_at = now(), updated_at = now()
        WHERE dataset_generation_stage_id = %s
        """,
        (error, stage_id),
    )
    cur.execute(
        """
        UPDATE public.dataset_generation_run
        SET status = 'failed', current_stage = NULL, last_error = %s, updated_at = now()
        WHERE dataset_generation_run_id = %s
        """,
        (error, run_id),
    )


def artifact_id(cur, *, run_id: int, artifact_key: str) -> int:
    cur.execute(
        """
        SELECT dataset_generation_artifact_id
        FROM public.dataset_generation_artifact
        WHERE dataset_generation_run_id = %s AND artifact_key = %s
        """,
        (run_id, artifact_key),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"recorded artefact not found: {artifact_key}")
    return int(row["dataset_generation_artifact_id"])


def open_adjudication_gate(
    cur,
    *,
    run_id: int,
    stage_id: int,
    template_artifact_id: int,
    expected_completed_path: str,
    instructions: str,
) -> None:
    cur.execute(
        """
        INSERT INTO public.dataset_generation_gate (
            dataset_generation_run_id,
            dataset_generation_stage_id,
            template_artifact_id,
            gate_key,
            gate_type,
            status,
            expected_completed_path,
            instructions
        )
        VALUES (%s, %s, %s, 'adjudication', 'human_csv_review', 'open', %s, %s)
        ON CONFLICT (dataset_generation_run_id, gate_key) DO UPDATE SET
            dataset_generation_stage_id = EXCLUDED.dataset_generation_stage_id,
            template_artifact_id = EXCLUDED.template_artifact_id,
            status = 'open',
            expected_completed_path = EXCLUDED.expected_completed_path,
            instructions = EXCLUDED.instructions,
            validation_summary = '{}'::jsonb,
            opened_at = now(),
            resolved_at = NULL,
            updated_at = now()
        """,
        (run_id, stage_id, template_artifact_id, expected_completed_path, instructions),
    )

def satisfy_adjudication_gate(
    cur,
    *,
    gate_id: int,
    completed_artifact_id: int,
    validation_summary: dict[str, Any],
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_gate
        SET status = 'satisfied',
            completed_artifact_id = %s,
            validation_summary = %s::jsonb,
            resolved_at = now(),
            updated_at = now()
        WHERE dataset_generation_gate_id = %s
          AND gate_key = 'adjudication'
          AND status = 'open'
        """,
        (
            completed_artifact_id,
            json.dumps(validation_summary, ensure_ascii=False, sort_keys=True),
            gate_id,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError(
            f"adjudication gate {gate_id} was not open or could not be satisfied"
        )

def open_revision_gate(
    cur,
    *,
    run_id: int,
    stage_id: int,
    template_artifact_id: int,
    expected_completed_path: str,
    instructions: str,
) -> None:
    cur.execute(
        """
        INSERT INTO public.dataset_generation_gate (
            dataset_generation_run_id,
            dataset_generation_stage_id,
            template_artifact_id,
            gate_key,
            gate_type,
            status,
            expected_completed_path,
            instructions
        )
        VALUES (%s, %s, %s, 'revision', 'human_csv_review', 'open', %s, %s)
        ON CONFLICT (dataset_generation_run_id, gate_key) DO UPDATE SET
            dataset_generation_stage_id = EXCLUDED.dataset_generation_stage_id,
            template_artifact_id = EXCLUDED.template_artifact_id,
            status = 'open',
            expected_completed_path = EXCLUDED.expected_completed_path,
            instructions = EXCLUDED.instructions,
            validation_summary = '{}'::jsonb,
            opened_at = now(),
            resolved_at = NULL,
            updated_at = now()
        """,
        (run_id, stage_id, template_artifact_id, expected_completed_path, instructions),
    )


def satisfy_revision_gate(
    cur,
    *,
    gate_id: int,
    completed_artifact_id: int,
    validation_summary: dict[str, Any],
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_gate
        SET status = 'satisfied',
            completed_artifact_id = %s,
            validation_summary = %s::jsonb,
            resolved_at = now(),
            updated_at = now()
        WHERE dataset_generation_gate_id = %s
          AND gate_key = 'revision'
          AND status = 'open'
        """,
        (
            completed_artifact_id,
            json.dumps(validation_summary, ensure_ascii=False, sort_keys=True),
            gate_id,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError(
            f"revision gate {gate_id} was not open or could not be satisfied"
        )

def open_revised_adjudication_gate(
    cur,
    *,
    run_id: int,
    stage_id: int,
    template_artifact_id: int,
    expected_completed_path: str,
    instructions: str,
) -> None:
    cur.execute(
        """
        INSERT INTO public.dataset_generation_gate (
            dataset_generation_run_id,
            dataset_generation_stage_id,
            template_artifact_id,
            gate_key,
            gate_type,
            status,
            expected_completed_path,
            instructions
        )
        VALUES (%s, %s, %s, 'revised_adjudication', 'human_csv_review', 'open', %s, %s)
        ON CONFLICT (dataset_generation_run_id, gate_key) DO UPDATE SET
            dataset_generation_stage_id = EXCLUDED.dataset_generation_stage_id,
            template_artifact_id = EXCLUDED.template_artifact_id,
            status = 'open',
            expected_completed_path = EXCLUDED.expected_completed_path,
            instructions = EXCLUDED.instructions,
            validation_summary = '{}'::jsonb,
            opened_at = now(),
            resolved_at = NULL,
            updated_at = now()
        """,
        (run_id, stage_id, template_artifact_id, expected_completed_path, instructions),
    )


def satisfy_revised_adjudication_gate(
    cur,
    *,
    gate_id: int,
    completed_artifact_id: int,
    validation_summary: dict[str, Any],
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_gate
        SET status = 'satisfied',
            completed_artifact_id = %s,
            validation_summary = %s::jsonb,
            resolved_at = now(),
            updated_at = now()
        WHERE dataset_generation_gate_id = %s
          AND gate_key = 'revised_adjudication'
          AND status = 'open'
        """,
        (
            completed_artifact_id,
            json.dumps(validation_summary, ensure_ascii=False, sort_keys=True),
            gate_id,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError(
            f"revised adjudication gate {gate_id} was not open or could not be satisfied"
        )


def open_manual_review_gate(
    cur,
    *,
    run_id: int,
    stage_id: int,
    template_artifact_id: int,
    expected_completed_path: str,
    instructions: str,
) -> None:
    cur.execute(
        """
        INSERT INTO public.dataset_generation_gate (
            dataset_generation_run_id,
            dataset_generation_stage_id,
            template_artifact_id,
            gate_key,
            gate_type,
            status,
            expected_completed_path,
            instructions
        )
        VALUES (%s, %s, %s, 'manual_review', 'human_csv_review', 'open', %s, %s)
        ON CONFLICT (dataset_generation_run_id, gate_key) DO UPDATE SET
            dataset_generation_stage_id = EXCLUDED.dataset_generation_stage_id,
            template_artifact_id = EXCLUDED.template_artifact_id,
            status = 'open',
            expected_completed_path = EXCLUDED.expected_completed_path,
            instructions = EXCLUDED.instructions,
            validation_summary = '{}'::jsonb,
            opened_at = now(),
            resolved_at = NULL,
            updated_at = now()
        """,
        (run_id, stage_id, template_artifact_id, expected_completed_path, instructions),
    )


def satisfy_manual_review_gate(
    cur,
    *,
    gate_id: int,
    completed_artifact_id: int,
    validation_summary: dict[str, Any],
) -> None:
    cur.execute(
        """
        UPDATE public.dataset_generation_gate
        SET status = 'satisfied',
            completed_artifact_id = %s,
            validation_summary = %s::jsonb,
            resolved_at = now(),
            updated_at = now()
        WHERE dataset_generation_gate_id = %s
          AND gate_key = 'manual_review'
          AND status = 'open'
        """,
        (
            completed_artifact_id,
            json.dumps(validation_summary, ensure_ascii=False, sort_keys=True),
            gate_id,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError(
            f"manual review gate {gate_id} was not open or could not be satisfied"
        )
