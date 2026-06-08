from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import Engine


DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"


@lru_cache
def _engine() -> Engine:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} must be set.")
    return create_engine(url)


@lru_cache
def _case_run_trace() -> Table:
    return Table("case_run_trace", MetaData(), schema="rpt", autoload_with=_engine())


@lru_cache
def _case_run_trace_reporting() -> Table:
    return Table("case_run_trace_reporting", MetaData(), schema="rpt", autoload_with=_engine())


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


if __name__ == "__main__":
    for case in list_cases(limit=3):
        print(case)
