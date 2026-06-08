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

from postgres_schema_config import add_schema_args, apply_search_path, op_relation, raw_relation, schemas_from_args


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
DERIVED_OPERATIONAL_TABLES = ("scenario", "eval_case", "case_turn", "response", "score_event")
INGEST_OWNED_LOOKUP_TABLES = ("rubric", "failure_class")
PUBLIC_LAYOUT_DERIVED_REBUILD_SQL_FILES = (
    "003_create_and_populate_scenario.sql",
    "004_update_scenario_names.sql",
    "005_create_operational_case_turn_model.sql",
    "009_fix_turn_score_normalisation.sql",
    "011_backfill_response_score_coverage_deduped.sql",
    "012_add_rubric_timestamps_for_score_backfill.sql",
    "013_backfill_unresolved_legacy_response_turns.sql",
    "015_create_rpt_reporting_views.sql",
)
SPLIT_LAYOUT_DERIVED_REBUILD_SQL_FILES = (
    "016_backfill_public_operational_from_raw.sql",
    "018_create_rpt_reporting_views_from_raw.sql",
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
    # db_url = f"postgresql://{os.getenv('PGUSER')}:{os.getenv('PGPASSWORD')}@{os.getenv('PGHOST')}:{os.getenv('PGPORT')}/{os.getenv('PGDATABASE')}"
    dsn = args.dsn # or os.getenv("MORAL_EVALS_DATABASE_URL")
    if dsn:
        conn = psycopg.connect(dsn)
        apply_search_path(conn, schemas_from_args(args))
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
        apply_search_path(conn, schemas_from_args(args))
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


def source(cur, schemas, root: Path, path: Path, count: int | None, kind_override: str | None = None) -> int:
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
        ''').format(raw_relation(schemas, "source_file")),
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


def dataset(cur, schemas, ver: str, sfid: int | None) -> int:
    cur.execute(
        sql.SQL('''
        insert into {} as dataset(dataset_version,dataset_family,source_file_id)
        values(%s,%s,%s)
        on conflict(dataset_version) do update set
          dataset_family=excluded.dataset_family,source_file_id=coalesce(dataset.source_file_id,excluded.source_file_id),updated_at=now()
        returning dataset_id
        ''').format(raw_relation(schemas, "dataset")),
        (ver, family(ver), sfid),
    )
    return int(cur.fetchone()[0])


def sample_id(r: dict[str, Any]) -> str | None:
    return clean(r.get("sample_id") or r.get("id") or r.get("case_id") or r.get("source_item_id"))


def upsert_case(cur, schemas, did: int, sfid: int | None, line: int | None, r: dict[str, Any]) -> int | None:
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
        ''').format(raw_relation(schemas, "dataset_case")),
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


def enrich_case(cur, schemas, cpk: int, r: dict[str, Any]) -> None:
    cur.execute(
        sql.SQL('''
        insert into {} as case_intervention(case_pk,user_followup,evidence_quality,pressure_type,followup_strength,expected_update,pressure_source,pressure_mechanism,pressure_legitimacy,pressure_escalation_stage,pressure_target,conflict_type,safeguard_relevance_to_original_concern,safeguard_type,safeguard_features,missing_safeguards,residual_risk_features,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)
        on conflict(case_pk) do update set user_followup=coalesce(excluded.user_followup,case_intervention.user_followup),evidence_quality=coalesce(excluded.evidence_quality,case_intervention.evidence_quality),pressure_type=coalesce(excluded.pressure_type,case_intervention.pressure_type),raw_metadata=case_intervention.raw_metadata || excluded.raw_metadata
        ''').format(raw_relation(schemas, "case_intervention")),
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
        ''').format(raw_relation(schemas, "expected_behaviour")),
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


def find_case(cur, schemas, did: int, r: dict[str, Any]) -> int | None:
    sid, cid = sample_id(r), clean(r.get("case_id"))
    if sid:
        cur.execute(
            sql.SQL("select case_pk from {} where dataset_id=%s and sample_id=%s").format(
                raw_relation(schemas, "dataset_case")
            ),
            (did, sid),
        )
        x = cur.fetchone()
        if x:
            return int(x[0])
    if cid:
        cur.execute(
            sql.SQL("select case_pk from {} where dataset_id=%s and case_id=%s limit 1").format(
                raw_relation(schemas, "dataset_case")
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


def run(cur, schemas, root: Path, path: Path, sfid: int, r: dict[str, Any]) -> int:
    label = path.relative_to(root).with_suffix("").as_posix()
    model = clean(r.get("model_name") or r.get("model")) or model_from_name(path)
    cur.execute(
        sql.SQL('''
        insert into {} as model_run(run_label,model_name,dataset_version,prompt_style,source_file_id,raw_metadata)
        values(%s,%s,%s,%s,%s,%s::jsonb)
        on conflict(run_label) do update set model_name=coalesce(excluded.model_name,model_run.model_name),dataset_version=coalesce(excluded.dataset_version,model_run.dataset_version),prompt_style=coalesce(excluded.prompt_style,model_run.prompt_style),source_file_id=excluded.source_file_id,updated_at=now()
        returning run_id
        ''').format(raw_relation(schemas, "model_run")),
        (label, model, clean(r.get("dataset_version")), clean(r.get("prompt_style")), sfid, js({"source_csv": path.relative_to(root).as_posix()})),
    )
    return int(cur.fetchone()[0])


def response(cur, schemas, rid: int, cpk: int | None, sfid: int, rownum: int, r: dict[str, Any]) -> int:
    sid = sample_id(r) or f"row-{rownum}"
    out = clean(r.get("output") or r.get("raw_response") or r.get("model_raw_response"))
    cur.execute(
        sql.SQL('''
        insert into {} as model_response(run_id,case_pk,sample_id,case_id,source_item_id,dataset_version,prompt_style,raw_response,source_file_id,source_row,raw_row)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        on conflict(run_id,sample_id) do update set case_pk=coalesce(excluded.case_pk,model_response.case_pk),raw_response=coalesce(excluded.raw_response,model_response.raw_response),raw_row=model_response.raw_row || excluded.raw_row,updated_at=now()
        returning response_id
        ''').format(raw_relation(schemas, "model_response")),
        (
            rid,
            cpk,
            sid,
            clean(r.get("case_id")),
            clean(r.get("source_item_id")),
            clean(r.get("dataset_version")),
            clean(r.get("prompt_style")),
            out,
            sfid,
            rownum,
            js(r),
        ),
    )
    return int(cur.fetchone()[0])


def tuple_from_output(cur, schemas, respid: int, sfid: int, r: dict[str, Any]) -> None:
    text = clean(r.get("output") or r.get("raw_response") or r.get("model_raw_response")) or ""
    fields = [
        "access_purpose",
        "access_intent",
        "access_population",
        "access_modality",
        "operational_status",
        "real_world_exposure",
        "externalisation_level",
    ]
    data = {}
    for f in fields:
        pat = f.replace("_", r"[_\s-]")
        m = re.search(rf"\b{pat}\b\s*[:=\-â€“â€”]\s*`?([A-Za-z0-9_./ -]{{1,80}})`?", text, re.I)
        if m:
            data[f] = re.sub(r"[^A-Za-z0-9]+", "_", m.group(1)).strip("_").lower()
    if not data:
        return
    cur.execute(
        sql.SQL('''
        insert into {}(response_id,extracted_from,access_purpose,access_intent,access_population,access_modality,operational_status,real_world_exposure,externalisation_level,raw_tuple,source_file_id)
        values(%s,'model_output_regex',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        on conflict(response_id) do update set raw_tuple=excluded.raw_tuple,source_file_id=excluded.source_file_id,updated_at=now()
        ''').format(raw_relation(schemas, "structured_decision_tuple")),
        (
            respid,
            clean(data.get("access_purpose")),
            clean(data.get("access_intent")),
            clean(data.get("access_population")),
            clean(data.get("access_modality")),
            clean(data.get("operational_status")),
            clean(data.get("real_world_exposure")),
            clean(data.get("externalisation_level")),
            js(data),
            sfid,
        ),
    )


def scores(cur, schemas, respid: int, sfid: int, rownum: int, r: dict[str, Any]) -> None:
    fc = clean(r.get("primary_failure_class") or r.get("failure_class"))
    fid = None
    if fc:
        cur.execute(
            sql.SQL(
                "insert into {}(name) values(%s) on conflict(name) do update set name=excluded.name returning failure_class_id"
            ).format(op_relation(schemas, "failure_class")),
            (fc,),
        )
        fid = int(cur.fetchone()[0])

    manual_score_raw = clean(r.get("manual_score_0_to_3") or r.get("manual_score") or r.get("score_manual"))
    note = clean(r.get("notes") or r.get("grading_rationale") or r.get("audit_notes") or r.get("rationale"))

    if any(clean(x) for x in (manual_score_raw, fc, r.get("confidence"), r.get("action"), note)):
        cur.execute(
            sql.SQL("select rubric_id from {} where rubric_name='manual_score_0_to_3'").format(
                op_relation(schemas, "rubric")
            )
        )
        rub = int(cur.fetchone()[0])
        try:
            score = float(manual_score_raw) if manual_score_raw else None
        except ValueError:
            score = None
        cur.execute(
            sql.SQL('''
            insert into {}(response_id,rubric_id,score_0_to_3,primary_failure_class_id,confidence,action,notes,source_file_id,source_row,raw_row)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            on conflict(response_id,source_file_id) do update set score_0_to_3=excluded.score_0_to_3,primary_failure_class_id=excluded.primary_failure_class_id,notes=excluded.notes,updated_at=now()
            ''').format(raw_relation(schemas, "manual_score")),
            (respid, rub, score, fid, clean(r.get("confidence")), clean(r.get("action")), note, sfid, rownum, js(r)),
        )

    det = clean(r.get("deterministic_score") or r.get("score_value") or r.get("score"))
    if det:
        cur.execute(
            sql.SQL(
                "insert into {}(response_id,score_value,source_file_id,source_row,raw_row) values(%s,%s,%s,%s,%s::jsonb) on conflict(response_id) do update set score_value=excluded.score_value,raw_row=excluded.raw_row,updated_at=now()"
            ).format(raw_relation(schemas, "deterministic_score")),
            (respid, det, sfid, rownum, js(r)),
        )


def ingest_jsonl(cur, schemas, root: Path, path: Path) -> int:
    rows = [(i, json.loads(line)) for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]
    sfid = source(cur, schemas, root, path, len(rows), "dataset_jsonl")
    for line, r in rows:
        did = dataset(cur, schemas, clean(r.get("dataset_version")) or version_from_path(path), sfid)
        cpk = upsert_case(cur, schemas, did, sfid, line, r)
        if cpk:
            enrich_case(cur, schemas, cpk, r)
    return len(rows)


def is_trace_csv(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    cols = {str(c).lower().strip() for c in rows[0].keys()}
    return bool(cols & TRACE_CASE_KEYS) and bool(cols & TRACE_PAYLOAD_KEYS)


def ingest_csv(cur, schemas, root: Path, path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    sfid = source(cur, schemas, root, path, len(rows))
    if not is_trace_csv(rows):
        LOG.info("recorded source-only CSV, not trace rows: %s", path.relative_to(root))
        return 0

    for n, r in enumerate(rows, 2):
        did = dataset(cur, schemas, clean(r.get("dataset_version")) or version_from_path(path), None)
        cpk = find_case(cur, schemas, did, r) or upsert_case(cur, schemas, did, None, None, r)
        if cpk:
            enrich_case(cur, schemas, cpk, r)
        rid = run(cur, schemas, root, path, sfid, r)
        respid = response(cur, schemas, rid, cpk, sfid, n, r)
        tuple_from_output(cur, schemas, respid, sfid, r)
        scores(cur, schemas, respid, sfid, n, r)
    return len(rows)


def collect_sources(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    jsonl = sorted({p for pat in DATASET_JSONL_PATTERNS for p in root.glob(pat) if p.is_file()})
    csvs = sorted({p for pat in TRACE_CSV_PATTERNS for p in root.glob(pat) if p.is_file()})
    source_only = sorted({p for pat in SOURCE_ONLY_PATTERNS for p in root.glob(pat) if p.is_file()})
    return jsonl, csvs, source_only


def list_sources(root: Path) -> None:
    jsonl, csvs, source_only = collect_sources(root)
    for label, paths in (("dataset_jsonl", jsonl), ("csv_candidate", csvs), ("source_only", source_only)):
        print(f"\n[{label}]")
        for path in paths:
            print(path.relative_to(root).as_posix())


def execute_schema(cur, root: Path) -> None:
    cur.execute((root / "sql" / "001_create_eval_provenance_schema.sql").read_text(encoding="utf-8"))


def run_sql_file(cur, path: Path, schemas) -> None:
    apply_search_path(cur, schemas)
    cur.execute(path.read_text(encoding="utf-8"))


def execute_sql_sequence(cur, root: Path, filenames: tuple[str, ...], schemas) -> None:
    for filename in filenames:
        path = root / "sql" / filename
        run_sql_file(cur, path, schemas)
        LOG.info("applied SQL: %s", path.relative_to(root).as_posix())


def derived_rebuild_sql_files(schemas) -> tuple[str, ...]:
    if schemas.raw == "public" and schemas.op == "public":
        return PUBLIC_LAYOUT_DERIVED_REBUILD_SQL_FILES
    if schemas.raw == "raw" and schemas.op == "public" and schemas.rpt == "rpt":
        return SPLIT_LAYOUT_DERIVED_REBUILD_SQL_FILES
    raise SystemExit(
        "--rebuild-derived supports either the legacy public layout "
        "(--raw-schema public --op-schema public) or the split layout "
        "(--raw-schema raw --op-schema public --rpt-schema rpt)."
    )


def validate_reset_scope_layout(schemas, scope: str) -> None:
    if schemas.raw not in ("public", "raw"):
        raise SystemExit("--reset-scope supports only --raw-schema public or --raw-schema raw.")
    if schemas.op != "public":
        raise SystemExit("--reset-scope currently requires --op-schema public.")
    if scope == "all" and schemas.raw == "raw" and schemas.rpt != "rpt":
        raise SystemExit("--reset-scope all in the split layout requires --rpt-schema rpt.")


def seed_rubric(cur, schemas) -> None:
    cur.execute(
        sql.SQL('''
        insert into {} (rubric_name, score_scale, description)
        values (
            'manual_score_0_to_3',
            '0-3',
            'Project manual audit score. Interpret using the dataset-specific manual scoring notes; PostgreSQL stores the recorded score and rationale but does not replace the audit files.'
        )
        on conflict (rubric_name) do nothing
        ''').format(op_relation(schemas, "rubric"))
    )


def truncate_relations(cur, relations: list[sql.Composed]) -> None:
    cur.execute(
        sql.SQL('''
        truncate table
            {}
        restart identity cascade
        ''').format(
            sql.SQL(",\n            ").join(relations)
        )
    )


def reset_raw(cur, schemas) -> None:
    # FK CASCADE may clear dependent operational rows in the current public layout.
    truncate_relations(cur, [raw_relation(schemas, table) for table in RAW_IMPORT_TABLES])


def reset_derived(cur, schemas) -> None:
    truncate_relations(cur, [op_relation(schemas, table) for table in DERIVED_OPERATIONAL_TABLES])


def reset_all(cur, schemas) -> None:
    truncate_relations(
        cur,
        [raw_relation(schemas, table) for table in RAW_IMPORT_TABLES]
        + [op_relation(schemas, table) for table in DERIVED_OPERATIONAL_TABLES]
        + [op_relation(schemas, table) for table in INGEST_OWNED_LOOKUP_TABLES],
    )
    seed_rubric(cur, schemas)


def reset_scope(cur, schemas, scope: str) -> None:
    if scope == "raw":
        reset_raw(cur, schemas)
    elif scope == "derived":
        reset_derived(cur, schemas)
    elif scope == "all":
        reset_all(cur, schemas)
    else:
        raise AssertionError(f"unexpected reset scope: {scope}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--dsn", help="Optional PostgreSQL DSN. Prefer MORAL_EVALS_DATABASE_URL or PG* env vars for local use.")
    add_schema_args(p)
    p.add_argument("--init-schema", action="store_true")
    p.add_argument(
        "--reset-scope",
        choices=("raw", "derived", "all"),
        help=(
            "Explicit reset scope. raw truncates imported/source-shaped tables in --raw-schema "
            "(FK CASCADE may clear dependent operational rows); derived truncates public operational "
            "derived tables; all truncates raw + derived + ingest-owned rubric/failure_class lookups "
            "and reseeds manual_score_0_to_3. Requires --yes."
        ),
    )
    p.add_argument(
        "--reset-data",
        action="store_true",
        help="Deprecated alias for --reset-scope all. Requires --yes.",
    )
    p.add_argument(
        "--rebuild-derived",
        action="store_true",
        help="Apply the post-ingest SQL sequence for derived operational/reporting objects. Uses 016/018 in the raw/public/rpt split layout.",
    )
    p.add_argument("--yes", action="store_true", help="Required with --reset-scope or deprecated --reset-data.")
    p.add_argument("--list-sources", action="store_true", help="Print the allowlisted source files and exit without connecting to PostgreSQL.")
    p.add_argument("--no-ingest", action="store_true", help="Apply schema/reset/rebuild steps only; do not ingest artefacts.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()

    if args.list_sources:
        list_sources(root)
        return 0

    if args.reset_data and args.reset_scope and args.reset_scope != "all":
        raise SystemExit("--reset-data is an alias for --reset-scope all; do not combine it with another reset scope.")
    if args.reset_data:
        args.reset_scope = "all"
        LOG.warning("--reset-data is deprecated; use --reset-scope all --yes instead.")

    if args.reset_scope and not args.yes:
        raise SystemExit(f"--reset-scope {args.reset_scope} is destructive. Re-run with --reset-scope {args.reset_scope} --yes.")

    schemas = schemas_from_args(args)
    rebuild_sql_files = derived_rebuild_sql_files(schemas) if args.rebuild_derived else ()
    if args.init_schema and schemas.search_path != ("public",):
        raise SystemExit("--init-schema remains public-schema only in this transition patch. Apply existing SQL migrations manually for now.")
    if args.reset_scope:
        validate_reset_scope_layout(schemas, args.reset_scope)

    with connect(args) as db, db.cursor() as cur:
        if args.init_schema:
            execute_schema(cur, root)
        if args.reset_scope:
            reset_scope(cur, schemas, args.reset_scope)
            LOG.info("reset provenance-layer data with scope=%s", args.reset_scope)

        total = 0
        if not args.no_ingest:
            jsonl, csvs, source_only = collect_sources(root)

            for path in jsonl:
                total += ingest_jsonl(cur, schemas, root, path)
                LOG.info("ingested dataset JSONL: %s", path.relative_to(root))

            for path in csvs:
                total += ingest_csv(cur, schemas, root, path)
                LOG.info("processed CSV candidate: %s", path.relative_to(root))

            for path in source_only:
                source(cur, schemas, root, path, None)
                LOG.info("recorded source-only artefact: %s", path.relative_to(root))

        if args.rebuild_derived:
            execute_sql_sequence(cur, root, rebuild_sql_files, schemas)

        db.commit()

    LOG.info("done; indexed %s JSONL/CSV trace rows", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

