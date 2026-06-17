from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, or_, select
from sqlalchemy.engine import Engine


DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_TABLES = (
    "moral_domain",
    "turn_type",
    "pressure_type",
    "evidence_quality",
    "scorer",
    "rubric",
    "failure_class",
)
REPORTING_VIEWS = (
    {
        "view_name": "run_summary",
        "label": "Run summary",
        "description": "One row per model/eval run for run list pages.",
    },
    {
        "view_name": "run_detail",
        "label": "Run detail",
        "description": "Response-level rows for a model/eval run detail page.",
    },
    {
        "view_name": "case_run_trace",
        "label": "Case run trace",
        "description": "Case-level trace surface for case cards and response review.",
    },
    {
        "view_name": "case_run_trace_reporting",
        "label": "Case run trace reporting",
        "description": "Reporting-safe case trace view where present.",
    },
    {
        "view_name": "pipeline_operational_linkage",
        "label": "Pipeline linkage",
        "description": "Bridge from pipeline/Inspect provenance rows to operational runs, cases, and responses.",
    },
    {
        "view_name": "model_call_diagnostics",
        "label": "Model-call diagnostics",
        "description": "Passive per-model-call/provider usage diagnostics. Diagnostic-only and not headline behavioural results.",
    },
    {
        "view_name": "model_call_diagnostics_by_sample",
        "label": "Model-call diagnostics by sample",
        "description": "One row per Inspect sample summarising passive model-call diagnostics and linkage status.",
    },
    {
        "view_name": "technical_data_dictionary",
        "label": "Technical data dictionary",
        "description": "Live PostgreSQL catalog metadata for schemas, tables, views, and columns.",
    },
    {
        "view_name": "data_dictionary",
        "label": "Data dictionary",
        "description": "Joined technical metadata, PostgreSQL comments, and curated governance metadata.",
    },
    {
        "view_name": "data_dictionary_missing_comment",
        "label": "Missing comments",
        "description": "Objects or columns missing PostgreSQL comments, where the diagnostic view exists.",
    },
    {
        "view_name": "data_dictionary_governance_gap",
        "label": "Governance gaps",
        "description": "Data dictionary governance gaps, where the diagnostic view exists.",
    },
)
REPORTING_VIEW_NAMES = tuple(view["view_name"] for view in REPORTING_VIEWS)


def _load_db_env_file(path: Path) -> None:
    """Load DB-related .env values without requiring the PowerShell env loader.

    Existing shell values win. This intentionally loads DB/schema keys only, not
    model API keys, because the browser/query layer is read-only.
    """

    if not path.exists():
        return

    allowed_exact = {DATABASE_URL_ENV}
    allowed_prefixes = ("MORAL_EVALS_", "PG")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if key not in allowed_exact and not key.startswith(allowed_prefixes):
            continue
        if key in os.environ:
            continue

        os.environ[key] = value.strip().strip('"').strip("'")


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
    _load_db_env_file(PROJECT_ROOT / ".env")
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} must be set or present in .env.")
    return create_engine(_sqlalchemy_psycopg3_url(url))


@lru_cache
def _case_run_trace() -> Table:
    return Table("case_run_trace", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _case_run_trace_reporting() -> Table:
    return Table("case_run_trace_reporting", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _run_summary() -> Table:
    return Table("run_summary", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _run_detail() -> Table:
    return Table("run_detail", MetaData(), schema="rpt", autoload_with=_engine())


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
def _response_diagnostic() -> Table:
    return Table("response_diagnostic", MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _model_call_diagnostic() -> Table:
    return Table("model_call_diagnostic", MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _model_call_diagnostics_by_sample() -> Table:
    return Table("model_call_diagnostics_by_sample", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _reference_table(table_name: str) -> Table:
    if table_name not in REFERENCE_TABLES:
        raise ValueError(f"Unsupported reference table: {table_name}")
    return Table(table_name, MetaData(), schema="public", autoload_with=_engine())


@lru_cache
def _reporting_view(view_name: str) -> Table:
    if view_name not in REPORTING_VIEW_NAMES:
        raise ValueError(f"Unsupported reporting view: {view_name}")
    return Table(view_name, MetaData(), schema="rpt", autoload_with=_engine())


def _public_table_exists(table_name: str) -> bool:
    return inspect(_engine()).has_table(table_name, schema="public")


def _rpt_view_exists(view_name: str) -> bool:
    inspector = inspect(_engine())
    return view_name in inspector.get_view_names(schema="rpt") or inspector.has_table(view_name, schema="rpt")


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


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    run_summary = _run_summary()
    stmt = (
        select(run_summary)
        .order_by(
            run_summary.c.run_timestamp.desc().nulls_last(),
            run_summary.c.run_id.desc(),
        )
        .limit(limit)
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def get_run_summary(run_id: int) -> dict[str, Any] | None:
    run_summary = _run_summary()
    stmt = select(run_summary).where(run_summary.c.run_id == run_id).limit(1)
    with _engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def get_run_detail(run_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    run_detail = _run_detail()
    stmt = (
        select(run_detail)
        .where(run_detail.c.run_id == run_id)
        .order_by(
            run_detail.c.sample_id.asc().nulls_last(),
            run_detail.c.turn_index.asc().nulls_last(),
            run_detail.c.response_id.asc().nulls_last(),
        )
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_reporting_views() -> list[dict[str, str]]:
    return [dict(view) for view in REPORTING_VIEWS if _rpt_view_exists(view["view_name"])]


def list_reporting_view_rows(view_name: str, limit: int = 200) -> list[dict[str, Any]]:
    if view_name not in REPORTING_VIEW_NAMES or not _rpt_view_exists(view_name):
        return []

    view = _reporting_view(view_name)
    stmt = select(view).limit(limit)
    for column_name in (
        "run_id",
        "response_id",
        "sample_id",
        "object_schema",
        "parent_object_name",
        "object_name",
    ):
        if column_name in view.c:
            stmt = stmt.order_by(view.c[column_name].asc().nulls_last())
            break

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


def list_model_call_diagnostics_for_pipeline_run(run_id: int) -> list[dict[str, Any]]:
    model_call_diagnostic = _model_call_diagnostic()
    stmt = (
        select(model_call_diagnostic)
        .where(model_call_diagnostic.c.experiment_pipeline_run_id == run_id)
        .order_by(
            model_call_diagnostic.c.inspect_log_sample_id.asc(),
            model_call_diagnostic.c.model_call_index.asc(),
            model_call_diagnostic.c.source_event_index.asc(),
            model_call_diagnostic.c.model_call_diagnostic_id.asc(),
        )
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_model_call_diagnostics_for_sample(inspect_log_sample_id: int) -> list[dict[str, Any]]:
    model_call_diagnostic = _model_call_diagnostic()
    stmt = (
        select(model_call_diagnostic)
        .where(model_call_diagnostic.c.inspect_log_sample_id == inspect_log_sample_id)
        .order_by(
            model_call_diagnostic.c.model_call_index.asc(),
            model_call_diagnostic.c.source_event_index.asc(),
            model_call_diagnostic.c.model_call_diagnostic_id.asc(),
        )
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_model_call_diagnostics_summary_for_pipeline_run(run_id: int) -> list[dict[str, Any]]:
    summary = _model_call_diagnostics_by_sample()
    stmt = (
        select(summary)
        .where(summary.c.experiment_pipeline_run_id == run_id)
        .order_by(summary.c.inspect_log_sample_id.asc())
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_response_diagnostics_for_pipeline_run(run_id: int) -> list[dict[str, Any]]:
    response_diagnostic = _response_diagnostic()
    inspect_log_sample = _inspect_log_sample()
    stmt = (
        select(
            response_diagnostic,
            inspect_log_sample.c.inspect_log_sample_id,
            inspect_log_sample.c.sample_id,
        )
        .join(inspect_log_sample, inspect_log_sample.c.response_id == response_diagnostic.c.response_id)
        .where(inspect_log_sample.c.experiment_pipeline_run_id == run_id)
        .order_by(inspect_log_sample.c.inspect_log_sample_id.asc(), response_diagnostic.c.response_diagnostic_id.asc())
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def list_response_diagnostics_for_sample(inspect_log_sample_id: int) -> list[dict[str, Any]]:
    response_diagnostic = _response_diagnostic()
    inspect_log_sample = _inspect_log_sample()
    stmt = (
        select(response_diagnostic)
        .join(inspect_log_sample, inspect_log_sample.c.response_id == response_diagnostic.c.response_id)
        .where(inspect_log_sample.c.inspect_log_sample_id == inspect_log_sample_id)
        .order_by(response_diagnostic.c.response_diagnostic_id.asc())
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


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
    print("[pipeline runs]")
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

    print("[model runs]")
    for run in list_runs(limit=3):
        print(
            {
                "run_id": run["run_id"],
                "run_label": run["run_label"],
                "model_name": run["model_name"],
                "dataset_version": run["dataset_version"],
                "response_count": run["response_count"],
                "score_event_count": run["score_event_count"],
            }
        )

    print("[reporting views]")
    for view in list_reporting_views():
        print(view)
