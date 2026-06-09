from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, or_, select
from sqlalchemy.engine import Engine


DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"REFERENCE_TABLES = (
    "moral_domain",
    "turn_type",
    "pressure_type",
    "evidence_quality",
    "scorer",
    "rubric",
    "failure_class",
)


def _sqlalchemy_psycopg3_url(url: str) -> str:
    """Use SQLAlchemy's psycopg3 dialect when a generic Postgres URL is supplied.

    SQLAlchemy's plain postgresql:// URL defaults to the psycopg2 DBAPI. This
    project uses psycopg3, so local MORAL_EVALS_DATABASE_URL values can remain
    normal libpq-style URLs while app queries still use the installed driver.
    """

    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+psycopg2://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


@lru_cache
def _engine() -> Engine:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} must be set.")
    return create_engine(_sqlalchemy_psycopg3_url(url))


@lru_cache
def _case_run_trace() -> Table:
    return Table("case_run_trace", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _case_run_trace_reporting() -> Table:
    return Table("case_run_trace_reporting", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _experiment_manifest() -> Table:
    return Table("experiment_manifest", MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _experiment_pipeline_run() -> Table:
    return Table("experiment_pipeline_run", MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _inspect_log_sample() -> Table:
    return Table("inspect_log_sample", MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _reference_table(table_name: str) -> Table:
    if table_name not in REFERENCE_TABLES:
        raise ValueError(f"Unsupported reference table: {table_name}")
    return Table(table_name, MetaData(), schema="public", autoload_with=_engine())


def _public_table_exists(table_name: str) -> bool:
    return inspect(_engine()).has_table(table_name, schema="public")


def get_case_by_response_id(response_id: int) -> dict[str, Any] | None:
    case_run_trace = _case_run_trace()
    stmt = select(case_run_trace).where(case_run_trace.c.response_id == response_id).limit(1)
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_cases(limit: int = 20) -> list[dict[str, Any]]:
    case_run_trace_reporting = _case_run_trace_reporting()
    stmt = select(case_run_trace_reporting).limit(limit)
    if "response_id" in case_run_trace_reporting.c:
        stmt = stmt.order_by(case_run_trace_reporting.c.response_id.desc())
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_pipeline_runs(limit: int = 50) -> list[dict[str, Any]]:
    experiment_pipeline_run = _experiment_pipeline_run()
    stmt = (
        select(experiment_pipeline_run)
        .order_by(
            experiment_pipeline_run.c.started_at.desc(),
            experiment_pipeline_run.c.experiment_pipeline_run_id.desc(),
        )
        .limit(limit)
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_experiment_manifests(limit: int = 100) -> list[dict[str, Any]]:
    experiment_manifest = _experiment_manifest()
    stmt = select(experiment_manifest).order_by(experiment_manifest.c.experiment_slug.asc()).limit(limit)
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def get_experiment_manifest(experiment_slug: str) -> dict[str, Any] | None:
    experiment_manifest = _experiment_manifest()
    stmt = (
        select(experiment_manifest)
        .where(experiment_manifest.c.experiment_slug == experiment_slug)
        .limit(1)
    )
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_reference_tables() -> list[dict[str, str]]:
    return [
        {"table_name": table_name}
        for table_name in REFERENCE_TABLES
        if _public_table_exists(table_name)
    ]


def list_reference_rows(table_name: str, limit: int = 500) -> list[dict[str, Any]]:
    if table_name not in REFERENCE_TABLES or not _public_table_exists(table_name):
        return []

    table = _reference_table(table_name)
    stmt = select(table).limit(limit)
    for column_name in (
        f"{table_name}_id",
        "id",
        "slug",
        "name",
        "rubric_name",
        "model_name",
    ):
        if column_name in table.c:
            stmt = stmt.order_by(table.c[column_name].asc())
            break

    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def get_pipeline_run(run_id: int) -> dict[str, Any] | None:
    experiment_pipeline_run = _experiment_pipeline_run()
    stmt = (
        select(experiment_pipeline_run)
        .where(experiment_pipeline_run.c.experiment_pipeline_run_id == run_id)
        .limit(1)
    )
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_runs_for_experiment(experiment_slug: str, limit: int = 50) -> list[dict[str, Any]]:
    experiment_pipeline_run = _experiment_pipeline_run()
    stmt = (
        select(experiment_pipeline_run)
        .where(experiment_pipeline_run.c.experiment_slug == experiment_slug)
        .order_by(
            experiment_pipeline_run.c.started_at.desc(),
            experiment_pipeline_run.c.experiment_pipeline_run_id.desc(),
        )
        .limit(limit)
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_run_samples(run_id: int) -> list[dict[str, Any]]:
    inspect_log_sample = _inspect_log_sample()
    stmt = (
        select(inspect_log_sample)
        .where(inspect_log_sample.c.experiment_pipeline_run_id == run_id)
        .order_by(inspect_log_sample.c.inspect_log_sample_id.asc())
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def get_run_sample(run_id: int, sample_id: str) -> dict[str, Any] | None:
    inspect_log_sample = _inspect_log_sample()
    stmt = (
        select(inspect_log_sample)
        .where(
            inspect_log_sample.c.experiment_pipeline_run_id == run_id,
            inspect_log_sample.c.sample_id == sample_id,
        )
        .limit(1)
    )
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def search_browser(query: str, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    query = query.strip()
    if not query:
        return {"experiments": [], "runs": [], "samples": []}

    pattern = f"%{query}%"
    experiment_manifest = _experiment_manifest()
    experiment_pipeline_run = _experiment_pipeline_run()
    inspect_log_sample = _inspect_log_sample()

    experiments_stmt = (
        select(experiment_manifest)
        .where(
            or_(
                experiment_manifest.c.experiment_slug.ilike(pattern),
                experiment_manifest.c.dataset_version.ilike(pattern),
            )
        )
        .order_by(experiment_manifest.c.experiment_slug.asc())
        .limit(limit)
    )
    runs_stmt = (
        select(experiment_pipeline_run)
        .where(
            or_(
                experiment_pipeline_run.c.experiment_slug.ilike(pattern),
                experiment_pipeline_run.c.dataset_version.ilike(pattern),
            )
        )
        .order_by(
            experiment_pipeline_run.c.started_at.desc(),
            experiment_pipeline_run.c.experiment_pipeline_run_id.desc(),
        )
        .limit(limit)
    )
    samples_stmt = (
        select(
            inspect_log_sample,
            experiment_pipeline_run.c.experiment_slug.label("experiment_slug"),
            experiment_pipeline_run.c.status.label("run_status"),
        )
        .join(
            experiment_pipeline_run,
            inspect_log_sample.c.experiment_pipeline_run_id
            == experiment_pipeline_run.c.experiment_pipeline_run_id,
        )
        .where(
            or_(
                inspect_log_sample.c.sample_id.ilike(pattern),
                inspect_log_sample.c.dataset_version.ilike(pattern),
                inspect_log_sample.c.input_text.ilike(pattern),
                inspect_log_sample.c.target_text.ilike(pattern),
                inspect_log_sample.c.final_response.ilike(pattern),
            )
        )
        .order_by(inspect_log_sample.c.inspect_log_sample_id.desc())
        .limit(limit)
    )
    with _engine().connect() as conn:
        return {
            "experiments": [dict(row) for row in conn.execute(experiments_stmt).mappings()],
            "runs": [dict(row) for row in conn.execute(runs_stmt).mappings()],
            "samples": [dict(row) for row in conn.execute(samples_stmt).mappings()],
        }


def get_latest_run_for_experiment(experiment_slug: str) -> dict[str, Any] | None:
    experiment_pipeline_run = _experiment_pipeline_run()
    stmt = (
        select(experiment_pipeline_run)
        .where(experiment_pipeline_run.c.experiment_slug == experiment_slug)
        .order_by(
            experiment_pipeline_run.c.started_at.desc(),
            experiment_pipeline_run.c.experiment_pipeline_run_id.desc(),
        )
        .limit(1)
    )
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


if __name__ == "__main__":
    for run in list_pipeline_runs(limit=3):
        run_id = run["experiment_pipeline_run_id"]
        print(
            {
                "experiment_pipeline_run_id": run_id,
                "experiment_slug": run["experiment_slug"],
                "status": run["status"],
                "started_at": run["started_at"],
                "sample_count": len(list_run_samples(run_id)),
            }
        )
