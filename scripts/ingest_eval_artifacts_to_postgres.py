#!/usr/bin/env python
'''Ingest eval artefacts into the PostgreSQL provenance layer.

Files remain the source of truth; this script builds a re-runnable query index.

The source plan is deliberately allowlisted. It does not walk the entire repo.
'''

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg import sql
except ImportError as exc:
    raise SystemExit('Install dependency first: python -m pip install "psycopg[binary]"') from exc

from postgres_schema_config import apply_search_path, op_relation, raw_relation


LOG = logging.getLogger("ingest_eval_artifacts")

DATASET_JSONL_PATTERNS = ("data/*.jsonl",)
TRACE_CSV_PATTERNS = (
    "tmp/*export*.csv",
    "docs/failure_audits/*.csv",
    "docs/run_exports/**/*.csv",
    "artefacts/run_exports/**/*.csv",
    "results/**/*.csv",
)
SOURCE_ONLY_PATTERNS = (
    "docs/reports/*.md",
    "docs/releases/*.md",
    "results/**/*.md",
    "results/**/*.sql",
    "results/**/*.eval",
)

TRACE_CASE_KEYS = {"sample_id", "id", "case_id", "source_item_id"}
TRACE_PAYLOAD_KEYS = {
    "output",
    "raw_response",
    "model_raw_response",
    "manual_score_0_to_3",
    "manual_score",
    "score_manual",
    "primary_failure_class",
    "failure_class",
    "deterministic_score",
    "score_value",
    "score",
}

RAW_IMPORT_TABLES = (
    "source_file",
    "dataset",
    "dataset_case",
    "case_intervention",
    "expected_behaviour",
    "model_run",
    "model_response",
    "manual_score",
    "deterministic_score",
    "structured_decision_tuple",
    "response_failure_class",
)
DERIVED_OPERATIONAL_TABLES = ("dataset", "run", "scenario", "eval_case", "case_turn", "response", "score_event")
INGEST_OWNED_LOOKUP_TABLES = ("rubric", "failure_class")
SPLIT_LAYOUT_DERIVED_REBUILD_SQL_FILES = (
    "promote_public_dimensions_from_raw.sql", # "019_promote_dataset_and_run.sql",
    "backfill_public_operational_from_raw.sql", # "016_backfill_public_operational_from_raw.sql",
    "promote_public_expectations_and_decisions_from_raw.sql",
    "create_reporting_views.sql", # "018_create_rpt_reporting_views_from_raw.sql",
    "create_data_dictionary.sql",
)


def clean(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s.strip() else None


def js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def jsonish(v: Any, default: Any) -> Any:
    if v in (None, ""):
        return default
    if isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return default
    try:
        return json.loads(s) if s[0] in "[{" else ([s] if isinstance(default, list) else default)
    except json.JSONDecodeError:
        return default


def connect(args):
    '''Connect without committing credentials to the repository.

    Accepted forms:
    - --dsn "postgresql://..."
    - libpq-style environment variables: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    '''
    dsn = args.dsn or os.getenv("MORAL_EVALS_DATABASE_URL")
    if dsn:
        conn = psycopg.connect(dsn)
        apply_search_path(conn)
        return conn

    params = {
        "host": os.getenv("PGHOST"),
        "port": os.getenv("PGPORT"),
        "dbname": os.getenv("PGDATABASE"),
        "user": os.getenv("PGUSER"),
        "password": os.getenv("PGPASSWORD"),
    }
    params = {k: v for k, v in params.items() if v}
    if params:
        conn = psycopg.connect(**params)
        apply_search_path(conn)
        return conn

    raise SystemExit(
        "No PostgreSQL connection configured. Set MORAL_EVALS_DATABASE_URL, "
        "or set PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD, or pass --dsn."
    )


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def kind(path: Path, override: str | None = None) -> str:
    if override:
        return override
    p = path.as_posix()
    if path.suffix == ".jsonl":
        return "dataset_jsonl"
    if path.suffix == ".csv" and "docs/failure_audits/" in p:
        return "manual_audit_csv"
    if path.suffix == ".csv":
        return "inspect_export_csv" if "export" in path.name.lower() else "csv_artifact"
    if path.suffix == ".md":
        return "markdown_artifact"
    if path.suffix == ".eval":
        return "inspect_eval_artifact"
    if path.suffix == ".sql":
        return "sql_artifact"
    return "other"


def source(cur, root: Path, path: Path, count: int | None, kind_override: str | None = None) -> int:
    rel = path.relative_to(root).as_posix()
    cur.execute(
        sql.SQL('''
        insert into {} as source_file(file_path,file_kind,content_sha256,file_size_bytes,record_count)
        values(%s,%s,%s,%s,%s)
        on conflict(file_path) do update set
          file_kind=excluded.file_kind,content_sha256=excluded.content_sha256,
          file_size_bytes=excluded.file_size_bytes,record_count=coalesce(excluded.record_count,source_file.record_count),
          ingested_at=now()
        returning source_file_id
        ''').format(raw_relation("source_file")),
        (rel, kind(path, kind_override), digest(path), path.stat().st_size, count),
    )
    return int(cur.fetchone()[0])


def version_from_path(path: Path) -> str:
    s = path.stem
    for pre in ("moral_reasoning_integrity_behaviour_", "moral_reasoning_integrity_", "moral_sycophancy_"):
        s = s.removeprefix(pre)
    return s


def family(ver: str) -> str:
    if "release_governance" in ver:
        return "release_governance"
    if "scope_control" in ver:
        return "scope_control"
    if "trap_expansion" in ver:
        return "evidence_strength_trap_expansion"
    if "evidence_strength" in ver:
        return "evidence_strength"
    if "integrity" in ver:
        return "integrity"
    return "behavioural"


def dataset(cur, ver: str, sfid: int | None) -> int:
    cur.execute(
        sql.SQL('''
        insert into {} as dataset(dataset_version,dataset_family,source_file_id)
        values(%s,%s,%s)
        on conflict(dataset_version) do update set
          dataset_family=excluded.dataset_family,source_file_id=coalesce(dataset.source_file_id,excluded.source_file_id),updated_at=now()
        returning dataset_id
        ''').format(raw_relation("dataset")),
        (ver, family(ver), sfid),
    )
    return int(cur.fetchone()[0])


def sample_id(r: dict[str, Any]) -> str | None:
    return clean(r.get("sample_id") or r.get("id") or r.get("case_id") or r.get("source_item_id"))


def upsert_case(cur, did: int, sfid: int | None, line: int | None, r: dict[str, Any]) -> int | None:
    sid = sample_id(r)
    if not sid:
        return None
    cur.execute(
        sql.SQL('''
        insert into {} as dataset_case(dataset_id,sample_id,case_id,source_item_id,variant,prompt_style,moral_domain,risk_track,scenario,initial_judgement,difficulty,difficulty_notes,source_file_id,source_line,raw_record)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        on conflict(dataset_id,sample_id) do update set
          case_id=coalesce(excluded.case_id,dataset_case.case_id),source_item_id=coalesce(excluded.source_item_id,dataset_case.source_item_id),
          variant=coalesce(excluded.variant,dataset_case.variant),prompt_style=coalesce(excluded.prompt_style,dataset_case.prompt_style),
          moral_domain=coalesce(excluded.moral_domain,dataset_case.moral_domain),risk_track=coalesce(excluded.risk_track,dataset_case.risk_track),
          scenario=coalesce(excluded.scenario,dataset_case.scenario),initial_judgement=coalesce(excluded.initial_judgement,dataset_case.initial_judgement),
          difficulty=coalesce(excluded.difficulty,dataset_case.difficulty),difficulty_notes=coalesce(excluded.difficulty_notes,dataset_case.difficulty_notes),
          source_file_id=coalesce(dataset_case.source_file_id,excluded.source_file_id),source_line=coalesce(dataset_case.source_line,excluded.source_line),
          raw_record=dataset_case.raw_record || excluded.raw_record,updated_at=now()
        returning case_pk
        ''').format(raw_relation("dataset_case")),
        (
            did,
            sid,
            clean(r.get("case_id")),
            clean(r.get("source_item_id")),
            clean(r.get("variant")),
            clean(r.get("prompt_style")),
            clean(r.get("moral_domain")),
            clean(r.get("risk_track")),
            clean(r.get("scenario")),
            clean(r.get("initial_judgement")),
            clean(r.get("difficulty")),
            clean(r.get("difficulty_notes")),
            sfid,
            line,
            js(r),
        ),
    )
    return int(cur.fetchone()[0])


def enrich_case(cur, cpk: int, r: dict[str, Any]) -> None:
    cur.execute(
        sql.SQL('''
        insert into {} as case_intervention(case_pk,user_followup,evidence_quality,pressure_type,followup_strength,expected_update,pressure_source,pressure_mechanism,pressure_legitimacy,pressure_escalation_stage,pressure_target,conflict_type,safeguard_relevance_to_original_concern,safeguard_type,safeguard_features,missing_safeguards,residual_risk_features,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)
        on conflict(case_pk) do update set user_followup=coalesce(excluded.user_followup,case_intervention.user_followup),evidence_quality=coalesce(excluded.evidence_quality,case_intervention.evidence_quality),pressure_type=coalesce(excluded.pressure_type,case_intervention.pressure_type),raw_metadata=case_intervention.raw_metadata || excluded.raw_metadata
        ''').format(raw_relation("case_intervention")),
        (
            cpk,
            clean(r.get("user_followup")),
            clean(r.get("evidence_quality")),
            clean(r.get("pressure_type")),
            clean(r.get("followup_strength")),
            clean(r.get("expected_update")),
            clean(r.get("pressure_source")),
            clean(r.get("pressure_mechanism")),
            clean(r.get("pressure_legitimacy")),
            clean(r.get("pressure_escalation_stage")),
            clean(r.get("pressure_target")),
            clean(r.get("conflict_type")),
            clean(r.get("safeguard_relevance_to_original_concern")),
            js(jsonish(r.get("safeguard_type"), [])),
            js(jsonish(r.get("safeguard_features"), [])),
            js(jsonish(r.get("missing_safeguards"), [])),
            js(jsonish(r.get("residual_risk_features"), [])),
            js(r),
        ),
    )
    cur.execute(
        sql.SQL('''
        insert into {} as expected_behaviour(case_pk,expected_behaviour,ideal_behaviour,target_scope,minimum_acceptable_scope,maximum_acceptable_scope,target_release_scope,minimum_acceptable_release_scope,maximum_acceptable_release_scope,target_access,minimum_acceptable_access,maximum_acceptable_access,release_artifact,missing_evaluation_types,failure_modes,too_conservative_failure,too_permissive_failure,pressure_trap,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)
        on conflict(case_pk) do update set expected_behaviour=coalesce(excluded.expected_behaviour,expected_behaviour.expected_behaviour),ideal_behaviour=coalesce(excluded.ideal_behaviour,expected_behaviour.ideal_behaviour),target_scope=coalesce(excluded.target_scope,expected_behaviour.target_scope),maximum_acceptable_scope=coalesce(excluded.maximum_acceptable_scope,expected_behaviour.maximum_acceptable_scope),target_release_scope=coalesce(excluded.target_release_scope,expected_behaviour.target_release_scope),maximum_acceptable_release_scope=coalesce(excluded.maximum_acceptable_release_scope,expected_behaviour.maximum_acceptable_release_scope),raw_metadata=expected_behaviour.raw_metadata || excluded.raw_metadata
        ''').format(raw_relation("expected_behaviour")),
        (
            cpk,
            clean(r.get("expected_behaviour")),
            clean(r.get("ideal_behaviour")),
            clean(r.get("target_scope")),
            clean(r.get("minimum_acceptable_scope")),
            clean(r.get("maximum_acceptable_scope")),
            clean(r.get("target_release_scope")),
            clean(r.get("minimum_acceptable_release_scope")),
            clean(r.get("maximum_acceptable_release_scope")),
            js(jsonish(r.get("target_access"), {})),
            js(jsonish(r.get("minimum_acceptable_access"), {})),
            js(jsonish(r.get("maximum_acceptable_access"), {})),
            clean(r.get("release_artifact")),
            js(jsonish(r.get("missing_evaluation_types"), [])),
            js(jsonish(r.get("failure_modes"), [])),
            clean(r.get("too_conservative_failure")),
            clean(r.get("too_permissive_failure")),
            clean(r.get("pressure_trap")),
            js(r),
        ),
    )


def find_case(cur, did: int, r: dict[str, Any]) -> int | None:
    sid, cid = sample_id(r), clean(r.get("case_id"))
    if sid:
        cur.execute(
            sql.SQL("select case_pk from {} where dataset_id=%s and sample_id=%s").format(
                raw_relation("dataset_case")
            ),
            (did, sid),
        )
        x = cur.fetchone()
        if x:
            return int(x[0])
    if cid:
        cur.execute(
            sql.SQL("select case_pk from {} where dataset_id=%s and case_id=%s limit 1").format(
                raw_relation("dataset_case")
            ),
            (did, cid),
        )
        x = cur.fetchone()
        if x:
            return int(x[0])
    return None


def model_from_name(path: Path) -> str | None:
    m = re.search(
        r"(gpt-[a-z0-9.\-]+|claude[a-z0-9._\-]*|gemini[a-z0-9._\-]*|llama[a-z0-9._\-]*|qwen[a-z0-9._\-]*)",
        path.stem.lower(),
    )
    return m.group(1).replace("_", "-") if m else None


def run(cur, root: Path, path: Path, did: int | None, dataset_version: str | None = None, metadata: dict[str, Any] | None = None) -> int:
    rel = path.relative_to(root).as_posix()
    metadata = metadata or {}
    cur.execute(
        sql.SQL('''
        insert into {} as model_run(run_label,model_name,provider,dataset_id,dataset_version,prompt_style,source_file_id,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,(select source_file_id from {} where file_path=%s),%s::jsonb)
        on conflict(run_label) do update set
          model_name=coalesce(model_run.model_name, excluded.model_name),
          provider=coalesce(model_run.provider, excluded.provider),
          dataset_id=coalesce(model_run.dataset_id, excluded.dataset_id),
          dataset_version=coalesce(model_run.dataset_version, excluded.dataset_version),
          prompt_style=coalesce(model_run.prompt_style, excluded.prompt_style),
          source_file_id=coalesce(model_run.source_file_id, excluded.source_file_id),
          raw_metadata=model_run.raw_metadata || excluded.raw_metadata,
          updated_at=now()
        returning run_id
        ''').format(raw_relation("model_run"), raw_relation("source_file")),
        (
            rel,
            clean(metadata.get("model_name")) or model_from_name(path),
            clean(metadata.get("provider")),
            did,
            dataset_version,
            clean(metadata.get("prompt_style")),
            rel,
            js(metadata),
        ),
    )
    return int(cur.fetchone()[0])

... (truncated)