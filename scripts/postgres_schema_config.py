from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from psycopg import sql


DEFAULT_RAW_SCHEMA = "raw"
DEFAULT_OP_SCHEMA = "public"
DEFAULT_RPT_SCHEMA = "rpt"
DEFAULT_SCHEMAS = {
    "raw": DEFAULT_RAW_SCHEMA,
    "op": DEFAULT_OP_SCHEMA,
    "rpt": DEFAULT_RPT_SCHEMA,
}


@dataclass(frozen=True)
class PostgresSchemas:
    raw: str = DEFAULT_RAW_SCHEMA
    op: str = DEFAULT_OP_SCHEMA
    rpt: str = DEFAULT_RPT_SCHEMA

    @property
    def search_path(self) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for schema in (self.raw, self.op, self.rpt, "public"):
            if schema not in seen:
                seen.add(schema)
                ordered.append(schema)
        return tuple(ordered)


def add_schema_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--raw-schema",
        default=os.getenv("MORAL_EVALS_RAW_SCHEMA", DEFAULT_RAW_SCHEMA),
        help="Schema for imported/source-shaped provenance tables. Default: MORAL_EVALS_RAW_SCHEMA or raw.",
    )
    parser.add_argument(
        "--op-schema",
        default=os.getenv("MORAL_EVALS_OP_SCHEMA", DEFAULT_OP_SCHEMA),
        help="Schema for operational tables. Default: MORAL_EVALS_OP_SCHEMA or public.",
    )
    parser.add_argument(
        "--rpt-schema",
        default=os.getenv("MORAL_EVALS_RPT_SCHEMA", DEFAULT_RPT_SCHEMA),
        help="Schema for reporting/query views. Default: MORAL_EVALS_RPT_SCHEMA or rpt.",
    )


def schemas_from_args(args: argparse.Namespace) -> PostgresSchemas:
    return PostgresSchemas(
        raw=_clean_schema(args.raw_schema, "raw"),
        op=_clean_schema(args.op_schema, "op"),
        rpt=_clean_schema(args.rpt_schema, "rpt"),
    )


def apply_search_path(conn_or_cur, schemas: PostgresSchemas) -> None:
    """Route unqualified runtime SQL through the configured schemas.

    This only changes the session search path. It does not create schemas,
    tables, views, or compatibility aliases.
    """

    conn_or_cur.execute(
        sql.SQL("SET search_path TO {}").format(
            sql.SQL(", ").join(sql.Identifier(schema) for schema in schemas.search_path)
        )
    )


def relation(schema: str, name: str) -> sql.Composed:
    return sql.Identifier(schema, name)


def raw_relation(schemas: PostgresSchemas, name: str) -> sql.Composed:
    return relation(schemas.raw, name)


def op_relation(schemas: PostgresSchemas, name: str) -> sql.Composed:
    return relation(schemas.op, name)


def rpt_relation(schemas: PostgresSchemas, name: str) -> sql.Composed:
    return relation(schemas.rpt, name)


def _clean_schema(value: str | None, label: str) -> str:
    schema = (value or DEFAULT_SCHEMAS[label]).strip()
    if not schema:
        raise SystemExit(f"{label} schema must not be blank.")
    return schema
