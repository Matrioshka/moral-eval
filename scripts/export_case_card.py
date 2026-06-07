#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as psql
from psycopg.rows import dict_row

from postgres_schema_config import add_schema_args, apply_search_path, rpt_relation, schemas_from_args


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs from .env without adding a dependency.

    Existing environment variables win. This intentionally supports only the
    plain .env format used for local development; it is not a shell parser.
    """

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def connect(args):
    load_dotenv()
    dsn = args.dsn or os.getenv("MORAL_EVALS_DATABASE_URL")
    if dsn:
        conn = psycopg.connect(dsn, row_factory=dict_row)
        apply_search_path(conn, schemas_from_args(args))
        return conn

    params = {
        "host": env_first("PG_HOST", "PGHOST"),
        "port": env_first("PG_PORT", "PGPORT"),
        "dbname": env_first("PG_DATABASE", "PGDATABASE"),
        "user": env_first("PG_USER", "PGUSER"),
        "password": env_first("PG_PASSWORD", "PGPASSWORD"),
    }
    params = {k: v for k, v in params.items() if v}
    if params:
        conn = psycopg.connect(**params, row_factory=dict_row)
        apply_search_path(conn, schemas_from_args(args))
        return conn

    raise SystemExit(
        "No PostgreSQL connection configured. Set MORAL_EVALS_DATABASE_URL, "
        "or set PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD in .env, "
        "or pass --dsn."
    )


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def one(value: Any) -> str:
    return txt(value).strip() or "_Not recorded_"


def first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def quote(value: Any) -> str:
    body = txt(value).strip()
    if not body:
        return "_Not recorded._"
    return "\n".join("> " + line if line else ">" for line in body.splitlines())


def fence(value: Any) -> str:
    body = txt(value).strip()
    if not body:
        return "_Not recorded._"
    return "```json\n" + body + "\n```"


def slug(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", txt(value)).strip("-").lower() or "case"


def main():
    p = argparse.ArgumentParser(description="Export one case_run_trace row as Markdown.")
    p.add_argument("--dsn")
    add_schema_args(p)
    p.add_argument("--case-id")
    p.add_argument("--sample-id")
    p.add_argument("--response-id", type=int)
    p.add_argument("--out", type=Path)
    args = p.parse_args()

    where, params = [], []
    if args.case_id:
        where.append("case_id=%s")
        params.append(args.case_id)
    if args.sample_id:
        where.append("sample_id=%s")
        params.append(args.sample_id)
    if args.response_id:
        where.append("response_id=%s")
        params.append(args.response_id)
    if not where:
        raise SystemExit("Use --case-id, --sample-id, or --response-id.")

    schemas = schemas_from_args(args)
    query = psql.SQL("select * from {} where {} order by response_id desc nulls last limit 1").format(
        rpt_relation(schemas, "case_run_trace"),
        psql.SQL(" and ").join(psql.SQL(condition) for condition in where),
    )
    with connect(args) as db, db.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"No matching row in {schemas.rpt}.case_run_trace.")
    row = dict(row)

    source_files = as_mapping(row.get("source_files"))
    structured_tuple = first(row, "structured_tuple", "extracted_structured_tuple", "raw_tuple")
    model_response = first(row, "raw_response", "model_raw_response", "response_text")

    title = one(row.get("case_id") or row.get("sample_id"))
    summary = (
        f"This case tests pressure type {one(row.get('pressure_type'))} with evidence quality "
        f"{one(row.get('evidence_quality'))}. Expected target: "
        f"{one(row.get('target_release_scope') or row.get('target_scope'))}. Manual score: "
        f"{one(row.get('manual_score'))}. Failure class: {one(row.get('failure_class'))}."
    )
    md = f"""# Case card: {title}

This card is exported from `case_run_trace`. Exact-source sections are provenance text from ingested artefacts. The diagram-ready summary is a paraphrase.

## Identifiers

| Field | Value |
|---|---|
| case_id | {one(row.get('case_id'))} |
| sample_id | {one(row.get('sample_id'))} |
| dataset_version | {one(row.get('dataset_version'))} |
| model_name | {one(row.get('model_name'))} |
| run_label | {one(row.get('run_label'))} |
| response_id | {one(row.get('response_id'))} |

## Scenario - exact source text

{quote(row.get('scenario'))}

## Baseline judgement - exact source text

{quote(row.get('initial_judgement'))}

## Intervention / user follow-up - exact source text

{quote(row.get('user_followup'))}

## Expected / target behaviour - exact source text

{quote(row.get('expected_behaviour') or row.get('ideal_behaviour'))}

## Model response - exact source text

{quote(model_response)}

## Structured tuple - extracted artefact

{fence(structured_tuple)}

## Score / audit

| Field | Value |
|---|---|
| manual_score | {one(row.get('manual_score'))} |
| deterministic_score | {one(row.get('deterministic_score'))} |
| failure_class | {one(row.get('failure_class'))} |

### Grading rationale / audit notes - exact source text where present

{quote(row.get('grading_rationale') or row.get('audit_notes'))}

## Source files

| Source | Path |
|---|---|
| dataset | {one(first(row, 'dataset_source_path') or source_files.get('dataset_case'))} |
| response | {one(first(row, 'response_source_path') or source_files.get('model_response'))} |
| manual score | {one(first(row, 'manual_score_source_path') or source_files.get('manual_score'))} |
| deterministic score | {one(first(row, 'deterministic_score_source_path') or source_files.get('deterministic_score'))} |

## Diagram-ready summary - paraphrase, not exact source text

{summary}
"""

    out = args.out or Path("docs/case_cards") / f"{slug(title)}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
