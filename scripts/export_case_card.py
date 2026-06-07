#!/usr/bin/env python
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
import psycopg
from psycopg.rows import dict_row


def connect(args):
    dsn = args.dsn or os.getenv("MORAL_EVALS_DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/moral_evals"
    return psycopg.connect(dsn, row_factory=dict_row)


def txt(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def one(value):
    return txt(value).strip() or "_Not recorded_"


def quote(value):
    body = txt(value).strip()
    if not body:
        return "_Not recorded._"
    return "\n".join("> " + line if line else ">" for line in body.splitlines())


def fence(value):
    body = txt(value).strip()
    if not body:
        return "_Not recorded._"
    return "```json\n" + body + "\n```"


def slug(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", txt(value)).strip("-").lower() or "case"


def main():
    p = argparse.ArgumentParser(description="Export one case_run_trace row as Markdown.")
    p.add_argument("--dsn")
    p.add_argument("--case-id")
    p.add_argument("--sample-id")
    p.add_argument("--response-id", type=int)
    p.add_argument("--out", type=Path)
    args = p.parse_args()

    where, params = [], []
    if args.case_id:
        where.append("case_id=%s"); params.append(args.case_id)
    if args.sample_id:
        where.append("sample_id=%s"); params.append(args.sample_id)
    if args.response_id:
        where.append("response_id=%s"); params.append(args.response_id)
    if not where:
        raise SystemExit("Use --case-id, --sample-id, or --response-id.")

    sql = "select * from case_run_trace where " + " and ".join(where) + " order by response_id desc nulls last limit 1"
    with connect(args) as db, db.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        raise SystemExit("No matching row in case_run_trace.")
    row = dict(row)

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

## Scenario — exact source text

{quote(row.get('scenario'))}

## Baseline judgement — exact source text

{quote(row.get('initial_judgement'))}

## Intervention / user follow-up — exact source text

{quote(row.get('user_followup'))}

## Expected / target behaviour — exact source text

{quote(row.get('expected_behaviour') or row.get('ideal_behaviour'))}

## Model response — exact exported text

{quote(row.get('model_raw_response'))}

## Extracted structured output

{fence(row.get('extracted_structured_tuple'))}

## Scores and audit notes

| Field | Value |
|---|---|
| manual_score | {one(row.get('manual_score'))} |
| failure_class | {one(row.get('failure_class'))} |
| deterministic_score | {one(row.get('deterministic_score'))} |
| deterministic_explanation | {one(row.get('deterministic_explanation'))} |

## Grading rationale / audit notes — exact source text where recorded

{quote(row.get('grading_rationale'))}

## Source files

{fence(row.get('source_files'))}

## Diagram-ready summary — paraphrase, not exact source text

{summary}
"""
    out = args.out or Path("docs") / "case_cards" / f"{slug(title)}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote case card: {out}")


if __name__ == "__main__":
    main()
