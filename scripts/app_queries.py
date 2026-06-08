from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import Engine


DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"


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
