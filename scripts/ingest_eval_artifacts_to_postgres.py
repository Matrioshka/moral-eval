#!/usr/bin/env python
"""Ingest eval artefacts into the PostgreSQL provenance layer.

Files remain the source of truth; this script builds a re-runnable query index.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, logging, os, re
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError as exc:
    raise SystemExit("Install dependency first: python -m pip install \"psycopg[binary]\"") from exc

LOG = logging.getLogger("ingest_eval_artifacts")


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
    dsn = args.dsn or os.getenv("MORAL_EVALS_DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/moral_evals"
    return psycopg.connect(dsn)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def kind(path: Path) -> str:
    p = path.as_posix()
    if path.suffix == ".jsonl":
        return "dataset_jsonl"
    if path.suffix == ".csv" and "docs/failure_audits/" in p:
        return "manual_audit_csv"
    if path.suffix == ".csv":
        return "inspect_export_csv" if "export" in path.name.lower() else "csv_artifact"
    if path.suffix == ".md":
        return "markdown_artifact"
    return "other"


def source(cur, root: Path, path: Path, count: int | None) -> int:
    rel = path.relative_to(root).as_posix()
    cur.execute(
        """
        insert into source_file(file_path,file_kind,content_sha256,file_size_bytes,record_count)
        values(%s,%s,%s,%s,%s)
        on conflict(file_path) do update set
          file_kind=excluded.file_kind,content_sha256=excluded.content_sha256,
          file_size_bytes=excluded.file_size_bytes,record_count=coalesce(excluded.record_count,source_file.record_count),
          ingested_at=now()
        returning source_file_id
        """,
        (rel, kind(path), digest(path), path.stat().st_size, count),
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
    return "behavioural"


def dataset(cur, ver: str, sfid: int | None) -> int:
    cur.execute(
        """
        insert into dataset(dataset_version,dataset_family,source_file_id)
        values(%s,%s,%s)
        on conflict(dataset_version) do update set
          dataset_family=excluded.dataset_family,source_file_id=coalesce(dataset.source_file_id,excluded.source_file_id),updated_at=now()
        returning dataset_id
        """,
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
        """
        insert into dataset_case(dataset_id,sample_id,case_id,source_item_id,variant,prompt_style,moral_domain,risk_track,scenario,initial_judgement,difficulty,difficulty_notes,source_file_id,source_line,raw_record)
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
        """,
        (did, sid, clean(r.get("case_id")), clean(r.get("source_item_id")), clean(r.get("variant")), clean(r.get("prompt_style")), clean(r.get("moral_domain")), clean(r.get("risk_track")), clean(r.get("scenario")), clean(r.get("initial_judgement")), clean(r.get("difficulty")), clean(r.get("difficulty_notes")), sfid, line, js(r)),
    )
    return int(cur.fetchone()[0])


def enrich_case(cur, cpk: int, r: dict[str, Any]) -> None:
    cur.execute(
        """
        insert into case_intervention(case_pk,user_followup,evidence_quality,pressure_type,followup_strength,expected_update,pressure_source,pressure_mechanism,pressure_legitimacy,pressure_escalation_stage,pressure_target,conflict_type,safeguard_relevance_to_original_concern,safeguard_type,safeguard_features,missing_safeguards,residual_risk_features,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)
        on conflict(case_pk) do update set user_followup=coalesce(excluded.user_followup,case_intervention.user_followup),evidence_quality=coalesce(excluded.evidence_quality,case_intervention.evidence_quality),pressure_type=coalesce(excluded.pressure_type,case_intervention.pressure_type),raw_metadata=case_intervention.raw_metadata || excluded.raw_metadata
        """,
        (cpk, clean(r.get("user_followup")), clean(r.get("evidence_quality")), clean(r.get("pressure_type")), clean(r.get("followup_strength")), clean(r.get("expected_update")), clean(r.get("pressure_source")), clean(r.get("pressure_mechanism")), clean(r.get("pressure_legitimacy")), clean(r.get("pressure_escalation_stage")), clean(r.get("pressure_target")), clean(r.get("conflict_type")), clean(r.get("safeguard_relevance_to_original_concern")), js(jsonish(r.get("safeguard_type"), [])), js(jsonish(r.get("safeguard_features"), [])), js(jsonish(r.get("missing_safeguards"), [])), js(jsonish(r.get("residual_risk_features"), [])), js(r)),
    )
    cur.execute(
        """
        insert into expected_behaviour(case_pk,expected_behaviour,ideal_behaviour,target_scope,minimum_acceptable_scope,maximum_acceptable_scope,target_release_scope,minimum_acceptable_release_scope,maximum_acceptable_release_scope,target_access,minimum_acceptable_access,maximum_acceptable_access,release_artifact,missing_evaluation_types,failure_modes,too_conservative_failure,too_permissive_failure,pressure_trap,raw_metadata)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)
        on conflict(case_pk) do update set expected_behaviour=coalesce(excluded.expected_behaviour,expected_behaviour.expected_behaviour),ideal_behaviour=coalesce(excluded.ideal_behaviour,expected_behaviour.ideal_behaviour),target_scope=coalesce(excluded.target_scope,expected_behaviour.target_scope),maximum_acceptable_scope=coalesce(excluded.maximum_acceptable_scope,expected_behaviour.maximum_acceptable_scope),target_release_scope=coalesce(excluded.target_release_scope,expected_behaviour.target_release_scope),maximum_acceptable_release_scope=coalesce(excluded.maximum_acceptable_release_scope,expected_behaviour.maximum_acceptable_release_scope),raw_metadata=expected_behaviour.raw_metadata || excluded.raw_metadata
        """,
        (cpk, clean(r.get("expected_behaviour")), clean(r.get("ideal_behaviour")), clean(r.get("target_scope")), clean(r.get("minimum_acceptable_scope")), clean(r.get("maximum_acceptable_scope")), clean(r.get("target_release_scope")), clean(r.get("minimum_acceptable_release_scope")), clean(r.get("maximum_acceptable_release_scope")), js(jsonish(r.get("target_access"), {})), js(jsonish(r.get("minimum_acceptable_access"), {})), js(jsonish(r.get("maximum_acceptable_access"), {})), clean(r.get("release_artifact")), js(jsonish(r.get("missing_evaluation_types"), [])), js(jsonish(r.get("failure_modes"), [])), clean(r.get("too_conservative_failure")), clean(r.get("too_permissive_failure")), clean(r.get("pressure_trap")), js(r)),
    )


def find_case(cur, did: int, r: dict[str, Any]) -> int | None:
    sid, cid = sample_id(r), clean(r.get("case_id"))
    if sid:
        cur.execute("select case_pk from dataset_case where dataset_id=%s and sample_id=%s", (did, sid)); x = cur.fetchone()
        if x: return int(x[0])
    if cid:
        cur.execute("select case_pk from dataset_case where dataset_id=%s and case_id=%s limit 1", (did, cid)); x = cur.fetchone()
        if x: return int(x[0])
    return None


def model_from_name(path: Path) -> str | None:
    m = re.search(r"(gpt-[a-z0-9.\-]+|claude[a-z0-9._\-]*|gemini[a-z0-9._\-]*|llama[a-z0-9._\-]*|qwen[a-z0-9._\-]*)", path.stem.lower())
    return m.group(1).replace("_", "-") if m else None


def run(cur, root: Path, path: Path, sfid: int, r: dict[str, Any]) -> int:
    label = path.relative_to(root).with_suffix("").as_posix()
    model = clean(r.get("model_name") or r.get("model")) or model_from_name(path)
    cur.execute(
        """
        insert into model_run(run_label,model_name,dataset_version,prompt_style,source_file_id,raw_metadata)
        values(%s,%s,%s,%s,%s,%s::jsonb)
        on conflict(run_label) do update set model_name=coalesce(excluded.model_name,model_run.model_name),dataset_version=coalesce(excluded.dataset_version,model_run.dataset_version),prompt_style=coalesce(excluded.prompt_style,model_run.prompt_style),source_file_id=excluded.source_file_id,updated_at=now()
        returning run_id
        """,
        (label, model, clean(r.get("dataset_version")), clean(r.get("prompt_style")), sfid, js({"source_csv": path.relative_to(root).as_posix()})),
    )
    return int(cur.fetchone()[0])


def response(cur, rid: int, cpk: int | None, sfid: int, rownum: int, r: dict[str, Any]) -> int:
    sid = sample_id(r) or f"row-{rownum}"
    out = clean(r.get("output") or r.get("raw_response") or r.get("model_raw_response"))
    cur.execute(
        """
        insert into model_response(run_id,case_pk,sample_id,case_id,source_item_id,dataset_version,prompt_style,raw_response,source_file_id,source_row,raw_row)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        on conflict(run_id,sample_id) do update set case_pk=coalesce(excluded.case_pk,model_response.case_pk),raw_response=coalesce(excluded.raw_response,model_response.raw_response),raw_row=model_response.raw_row || excluded.raw_row,updated_at=now()
        returning response_id
        """,
        (rid, cpk, sid, clean(r.get("case_id")), clean(r.get("source_item_id")), clean(r.get("dataset_version")), clean(r.get("prompt_style")), out, sfid, rownum, js(r)),
    )
    return int(cur.fetchone()[0])


def tuple_from_output(cur, respid: int, sfid: int, r: dict[str, Any]) -> None:
    text = clean(r.get("output") or r.get("raw_response") or r.get("model_raw_response")) or ""
    fields = ["access_purpose", "access_intent", "access_population", "access_modality", "operational_status", "real_world_exposure", "externalisation_level"]
    data = {}
    for f in fields:
        pat = f.replace("_", r"[_\s-]")
        m = re.search(rf"\b{pat}\b\s*[:=\-–—]\s*`?([A-Za-z0-9_./ -]{{1,80}})`?", text, re.I)
        if m:
            data[f] = re.sub(r"[^A-Za-z0-9]+", "_", m.group(1)).strip("_").lower()
    if not data:
        return
    cur.execute(
        """
        insert into structured_decision_tuple(response_id,extracted_from,access_purpose,access_intent,access_population,access_modality,operational_status,real_world_exposure,externalisation_level,raw_tuple,source_file_id)
        values(%s,'model_output',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        on conflict(response_id) do update set raw_tuple=excluded.raw_tuple,source_file_id=excluded.source_file_id,updated_at=now()
        """,
        (respid, clean(data.get("access_purpose")), clean(data.get("access_intent")), clean(data.get("access_population")), clean(data.get("access_modality")), clean(data.get("operational_status")), clean(data.get("real_world_exposure")), clean(data.get("externalisation_level")), js(data), sfid),
    )


def scores(cur, respid: int, sfid: int, rownum: int, r: dict[str, Any]) -> None:
    fc = clean(r.get("primary_failure_class"))
    fid = None
    if fc:
        cur.execute("insert into failure_class(name) values(%s) on conflict(name) do update set name=excluded.name returning failure_class_id", (fc,)); fid = int(cur.fetchone()[0])
    if any(clean(r.get(k)) for k in ("manual_score_0_to_3", "primary_failure_class", "confidence", "action", "notes")):
        cur.execute("select rubric_id from rubric where rubric_name='manual_score_0_to_3'"); rub = int(cur.fetchone()[0])
        try: score = float(r.get("manual_score_0_to_3")) if clean(r.get("manual_score_0_to_3")) else None
        except ValueError: score = None
        cur.execute(
            """
            insert into manual_score(response_id,rubric_id,score_0_to_3,primary_failure_class_id,confidence,action,notes,source_file_id,source_row,raw_row)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            on conflict(response_id,source_file_id) do update set score_0_to_3=excluded.score_0_to_3,primary_failure_class_id=excluded.primary_failure_class_id,notes=excluded.notes,updated_at=now()
            """,
            (respid, rub, score, fid, clean(r.get("confidence")), clean(r.get("action")), clean(r.get("notes")), sfid, rownum, js(r)),
        )
    det = clean(r.get("deterministic_score") or r.get("score_value") or r.get("score"))
    if det:
        cur.execute("insert into deterministic_score(response_id,score_value,source_file_id,source_row,raw_row) values(%s,%s,%s,%s,%s::jsonb) on conflict(response_id) do update set score_value=excluded.score_value,raw_row=excluded.raw_row,updated_at=now()", (respid, det, sfid, rownum, js(r)))


def ingest_jsonl(cur, root: Path, path: Path) -> int:
    rows = [(i, json.loads(line)) for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]
    sfid = source(cur, root, path, len(rows))
    for line, r in rows:
        did = dataset(cur, clean(r.get("dataset_version")) or version_from_path(path), sfid)
        cpk = upsert_case(cur, did, sfid, line, r)
        if cpk: enrich_case(cur, cpk, r)
    return len(rows)


def ingest_csv(cur, root: Path, path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    sfid = source(cur, root, path, len(rows))
    for n, r in enumerate(rows, 2):
        did = dataset(cur, clean(r.get("dataset_version")) or version_from_path(path), None)
        cpk = find_case(cur, did, r) or upsert_case(cur, did, None, None, r)
        if cpk: enrich_case(cur, cpk, r)
        rid = run(cur, root, path, sfid, r)
        respid = response(cur, rid, cpk, sfid, n, r)
        tuple_from_output(cur, respid, sfid, r)
        scores(cur, respid, sfid, n, r)
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--dsn")
    p.add_argument("--init-schema", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    with connect(args) as db, db.cursor() as cur:
        if args.init_schema:
            cur.execute((root / "sql" / "001_create_eval_provenance_schema.sql").read_text(encoding="utf-8"))
        total = 0
        for path in sorted((root / "data").glob("*.jsonl")):
            total += ingest_jsonl(cur, root, path); LOG.info("ingested %s", path)
        csvs = sorted(set((root / "tmp").glob("*export*.csv")) | set((root / "docs" / "failure_audits").glob("*.csv")))
        for path in csvs:
            total += ingest_csv(cur, root, path); LOG.info("ingested %s", path)
        for path in sorted(set((root / "docs" / "reports").glob("*.md")) | set((root / "docs" / "releases").glob("*.md"))):
            source(cur, root, path, None)
        db.commit()
    LOG.info("done; indexed %s JSONL/CSV rows", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
