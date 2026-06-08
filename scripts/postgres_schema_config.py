from __future__ import annotations

import os

from psycopg import sql


RAW_SCHEMA_ENV = "MORAL_EVALS_RAW_SCHEMA"
OP_SCHEMA_ENV = "MORAL_EVALS_OP_SCHEMA"
RPT_SCHEMA_ENV = "MORAL_EVALS_RPT_SCHEMA"


def apply_search_path(conn_or_cur) -> None:
    """Route unqualified SQL through the active raw/public/rpt runtime layout."""

    conn_or_cur.execute(
        sql.SQL("SET search_path TO {}").format(
            sql.SQL(", ").join(sql.Identifier(schema) for schema in _search_path())
        )
    )


def relation(schema: str, name: str) -> sql.Composed:
    return sql.Identifier(schema, name)


def raw_relation(name: str) -> sql.Composed:
    return relation(_schema_from_env(RAW_SCHEMA_ENV), name)


def op_relation(name: str) -> sql.Composed:
    return relation(_schema_from_env(OP_SCHEMA_ENV), name)


def rpt_relation(name: str) -> sql.Composed:
    return relation(_schema_from_env(RPT_SCHEMA_ENV), name)


def _search_path() -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for schema in (
        _schema_from_env(RAW_SCHEMA_ENV),
        _schema_from_env(OP_SCHEMA_ENV),
        _schema_from_env(RPT_SCHEMA_ENV),
        "public",
    ):
        if schema not in seen:
            seen.add(schema)
            ordered.append(schema)
    return tuple(ordered)


def _schema_from_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise SystemExit(f"{name} must be set to the active PostgreSQL schema name.")
    return value.strip()
