"""Deterministic offline human-review bundles and append-only decisions."""

from __future__ import annotations

import csv
import html
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import (
    CRITERION_KEYS,
    DEFAULT_RESPONSE_SCHEMA_PATH as DEFAULT_SOURCE_RESPONSE_SCHEMA_PATH,
    REPO_ROOT,
    SemanticReviewError,
    canonical_json,
    load_schema,
    read_jsonl,
    sha256_path,
    sha256_text,
    validate_instance,
    write_jsonl,
)
from .final_validation import (
    DEFAULT_FINAL_RECORD_SCHEMA_PATH,
    SEMANTIC_CHECK_KEYS,
)
from .resolution import (
    DEFAULT_COMPARISON_SCHEMA_PATH,
    DEFAULT_ELIGIBILITY_SCHEMA_PATH,
    DEFAULT_RESOLVED_SCHEMA_PATH,
    DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH,
    source_eligibility_rule,
)
from .transformation import (
    DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    render_transformation_conditions,
)


SOURCE_STAGE = "source_review_human_hold"
FINAL_STAGE = "final_transformation_human_review"
SOURCE_QUEUE_VERSION = "airisk_jmcup_source_human_review_queue_v1"
FINAL_QUEUE_VERSION = "airisk_jmcup_final_human_review_queue_v1"
SOURCE_SUBMISSION_VERSION = "airisk_jmcup_source_human_decision_submission_v1"
FINAL_SUBMISSION_VERSION = "airisk_jmcup_final_human_decision_submission_v1"
SOURCE_DECISION_VERSION = "airisk_jmcup_source_human_decision_v1"
FINAL_DECISION_VERSION = "airisk_jmcup_final_human_decision_v1"
MANIFEST_VERSION = "airisk_jmcup_human_review_bundle_manifest_v1"
AUDIT_VERSION = "airisk_jmcup_human_review_audit_v1"


def _schema_path(version: str) -> Path:
    return REPO_ROOT / "schemas" / f"{version}.schema.json"


DEFAULT_SOURCE_QUEUE_SCHEMA_PATH = _schema_path(SOURCE_QUEUE_VERSION)
DEFAULT_FINAL_QUEUE_SCHEMA_PATH = _schema_path(FINAL_QUEUE_VERSION)
DEFAULT_SOURCE_SUBMISSION_SCHEMA_PATH = _schema_path(SOURCE_SUBMISSION_VERSION)
DEFAULT_FINAL_SUBMISSION_SCHEMA_PATH = _schema_path(FINAL_SUBMISSION_VERSION)
DEFAULT_SOURCE_DECISION_SCHEMA_PATH = _schema_path(SOURCE_DECISION_VERSION)
DEFAULT_FINAL_DECISION_SCHEMA_PATH = _schema_path(FINAL_DECISION_VERSION)
DEFAULT_MANIFEST_SCHEMA_PATH = _schema_path(MANIFEST_VERSION)
DEFAULT_AUDIT_SCHEMA_PATH = _schema_path(AUDIT_VERSION)

SOURCE_HASH_FIELDS = (
    "source_group_record_sha256",
    "resolved_source_review_sha256",
    "source_eligibility_record_sha256",
    "review_comparison_record_sha256",
)
FINAL_HASH_FIELDS = (
    "transformation_record_sha256",
    "final_validation_record_sha256",
    "source_group_record_sha256",
)
FINAL_ROLE_FIELDS = (
    "baseline_preferred_action",
    "unsupported_pressure_variant",
    "preferred_action_after_unsupported_pressure",
    "genuine_evidence_variant",
    "preferred_action_after_genuine_evidence",
)


def _record_sha(record: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(record))


def _indexed(
    records: Sequence[Mapping[str, Any]], *, key: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = dict(raw)
        value = record.get(key)
        if not isinstance(value, str) or not value:
            raise SemanticReviewError(f"{label} record lacks {key}")
        if value in result:
            raise SemanticReviewError(f"Duplicate {label} {key} {value!r}")
        result[value] = record
    return result


def _read_validated_index(
    path: Path, *, schema_path: Path, key: str, label: str
) -> dict[str, dict[str, Any]]:
    schema = load_schema(schema_path)
    records = read_jsonl(path)
    for record in records:
        validate_instance(record, schema, label=label)
    return _indexed(records, key=key, label=label)


def _review_target_sha(
    *, stage: str, case_id: str, record_hashes: Mapping[str, str]
) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": "airisk_jmcup_human_review_target_v1",
                "review_stage": stage,
                "case_id": case_id,
                "reviewed_record_hashes": dict(sorted(record_hashes.items())),
            }
        )
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _assert_output_paths(paths: Sequence[Path], *, overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite human-review products: "
            + ", ".join(str(path) for path in existing)
        )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _source_queue_row(record: Mapping[str, Any]) -> dict[str, Any]:
    comparison = record["review_comparison"]
    return {
        "review_stage": SOURCE_STAGE,
        "generation_group_id": record["generation_group_id"],
        "queue_route": record["queue_route"],
        "queue_reason_codes": " | ".join(record["queue_reason_codes"]),
        "unresolved_criteria": " | ".join(record["unresolved_criteria"]),
        "disputed_criteria": " | ".join(record["disputed_criteria"]),
        "reviewer_a_provider": comparison["reviewer_a"]["provider"],
        "reviewer_a_model": comparison["reviewer_a"]["requested_model"],
        "reviewer_b_provider": comparison["reviewer_b"]["provider"],
        "reviewer_b_model": comparison["reviewer_b"]["requested_model"],
        "review_target_sha256": record["provenance"]["review_target_sha256"],
        "queue_item_sha256": _record_sha(record),
    }


def _final_queue_row(record: Mapping[str, Any]) -> dict[str, Any]:
    validation = record["final_validation"]
    return {
        "review_stage": FINAL_STAGE,
        "transformation_id": record["transformation_id"],
        "generation_group_id": record["generation_group_id"],
        "queue_reason_codes": " | ".join(record["queue_reason_codes"]),
        "validation_id": validation["validation_id"],
        "validator_provider": validation["provenance"]["provider"],
        "validator_model": validation["provenance"]["requested_model"],
        "review_target_sha256": record["provenance"]["review_target_sha256"],
        "queue_item_sha256": _record_sha(record),
    }


def _source_template(record: Mapping[str, Any]) -> dict[str, Any]:
    provenance = record["provenance"]
    return {
        "schema_version": SOURCE_SUBMISSION_VERSION,
        "review_stage": SOURCE_STAGE,
        "generation_group_id": record["generation_group_id"],
        "review_target_sha256": provenance["review_target_sha256"],
        "queue_item_sha256": _record_sha(record),
        **{field: provenance[field] for field in SOURCE_HASH_FIELDS},
        **{key: None for key in CRITERION_KEYS},
        "source_fidelity": None,
        "rewrite_level": None,
        "final_disposition": None,
        "human_rationale": None,
        "reviewer_id": None,
        "reviewer_name": None,
        "review_timestamp": None,
        "supersedes_decision_id": None,
        "supersession_reason": None,
    }


def _final_template(record: Mapping[str, Any]) -> dict[str, Any]:
    provenance = record["provenance"]
    return {
        "schema_version": FINAL_SUBMISSION_VERSION,
        "review_stage": FINAL_STAGE,
        "transformation_id": record["transformation_id"],
        "review_target_sha256": provenance["review_target_sha256"],
        "queue_item_sha256": _record_sha(record),
        **{field: provenance[field] for field in FINAL_HASH_FIELDS},
        **{key: None for key in SEMANTIC_CHECK_KEYS},
        **{field: None for field in FINAL_ROLE_FIELDS},
        "final_disposition": None,
        "human_rationale": None,
        "revision_suggestion": None,
        "reviewer_id": None,
        "reviewer_name": None,
        "review_timestamp": None,
        "supersedes_decision_id": None,
        "supersession_reason": None,
    }


def _html_text(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _html_pre(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2, ensure_ascii=False)
    return f'<div class="text">{_html_text(value)}</div>'


def _html_document(*, title: str, stage: str, cards: Sequence[str]) -> str:
    body = "\n".join(cards) or '<p class="empty">No cases require review.</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html_text(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f5f7;color:#18202a;line-height:1.42}}
main{{max-width:1180px;margin:auto;padding:24px}} .stage{{padding:10px 14px;background:#18202a;color:white;border-radius:6px}}
.card{{background:white;border:1px solid #ccd2da;border-radius:8px;margin:22px 0;padding:18px;box-shadow:0 1px 3px #0001}}
.case-id{{font-family:ui-monospace,monospace}} .alert{{border-left:5px solid #b42318;padding:8px 12px;background:#fff1f0}}
.text{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f8f9fb;border:1px solid #e1e5ea;border-radius:5px;padding:10px}}
table{{border-collapse:collapse;width:100%;table-layout:fixed;margin:12px 0}} th,td{{border:1px solid #d6dbe2;padding:7px;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eef1f5;text-align:left}} .unresolved{{background:#fff0c2;font-weight:700}} h2,h3,h4{{margin-bottom:8px}} .muted{{color:#586574}}
</style></head><body><main><h1>{_html_text(title)}</h1><p class="stage">Stage: {_html_text(stage)}</p>
<p>This report is read-only. Enter decisions in the companion CSV or JSONL template; canonical decisions are appended separately.</p>{body}</main></body></html>
"""


def _source_html(records: Sequence[Mapping[str, Any]]) -> str:
    cards: list[str] = []
    for record in records:
        group_id = record["generation_group_id"]
        comparison = record["review_comparison"]
        resolved = record["resolved_source_review"]
        reviewer_a = comparison["reviewer_a"]
        reviewer_b = comparison["reviewer_b"]
        rows = []
        adjudicator = None
        evidence = resolved.get("adjudication_evidence") or resolved.get("qc_evidence")
        if evidence:
            adjudicator = evidence.get("adjudicator_response")
        for key in CRITERION_KEYS:
            a = reviewer_a["response"]["criteria"][key]
            b = reviewer_b["response"]["criteria"][key]
            op = adjudicator.get("criteria", {}).get(key) if adjudicator else None
            state = resolved["criteria"][key]
            css = ' class="unresolved"' if key in record["unresolved_criteria"] else ""
            rows.append(
                f"<tr{css}><td>{_html_text(key)}</td>"
                f"<td><b>{_html_text(a['judgement'])}</b><br>{_html_text(a['rationale'])}</td>"
                f"<td><b>{_html_text(b['judgement'])}</b><br>{_html_text(b['rationale'])}</td>"
                f"<td>{_html_text(op.get('judgement') if op else None)}<br>{_html_text(op.get('rationale') if op else None)}</td>"
                f"<td>{_html_text(state.get('resolved_judgement'))}<br>{_html_text(state.get('rationale'))}</td></tr>"
            )
        occurrences = []
        for item in record["source_group"]["contextualisations"]:
            occurrences.append(
                f"<h4>{_html_text(item['occurrence_id'])}</h4>{_html_pre(item['dilemma'])}"
                f"<p><b>Contexts:</b> {_html_text(' | '.join(item['contexts']))}</p>"
                f"<table><tr><th>Source action 1</th><th>Source action 2</th></tr><tr>"
                f"<td>{_html_text(item['actions']['action_1']['action'])}<br>{_html_text(item['actions']['action_1']['action_body'])}</td>"
                f"<td>{_html_text(item['actions']['action_2']['action'])}<br>{_html_text(item['actions']['action_2']['action_body'])}</td></tr></table>"
            )
        proposals = []
        for role, reviewer in (("Reviewer A", reviewer_a), ("Reviewer B", reviewer_b)):
            response = reviewer["response"]
            proposals.append(
                f"<tr><td><b>{role}</b><br>{_html_text(reviewer['provider'])}<br>{_html_text(reviewer['requested_model'])}<br>{_html_text(reviewer['reviewer_run_id'])}</td>"
                f"<td>{_html_text(response['proposed_bounded_action'])}</td><td>{_html_text(response['proposed_broader_action'])}</td>"
                f"<td>{_html_text(response['proposed_baseline_blocker'])}</td><td>{_html_text(response['proposed_evidence'])}</td>"
                f"<td>{_html_text(response['source_fidelity'])} / {_html_text(response['rewrite_level'])}<br>{_html_text(response['reviewer_verdict'])} / {_html_text(response['reviewer_confidence'])}</td></tr>"
            )
        if adjudicator:
            proposals.append(
                f"<tr><td><b>Opus adjudicator/QC</b><br>{_html_text(evidence.get('adjudicator_run_id'))}</td>"
                f"<td>{_html_text(adjudicator.get('proposed_bounded_action'))}</td><td>{_html_text(adjudicator.get('proposed_broader_action'))}</td>"
                f"<td>{_html_text(adjudicator.get('proposed_baseline_blocker'))}</td><td>{_html_text(adjudicator.get('proposed_evidence'))}</td>"
                f"<td>{_html_text(adjudicator.get('source_fidelity'))} / {_html_text(adjudicator.get('rewrite_level'))}<br>{_html_text(adjudicator.get('reviewer_verdict'))} / {_html_text(adjudicator.get('reviewer_confidence'))}</td></tr>"
            )
        cards.append(
            f'<section class="card"><h2 class="case-id">{_html_text(group_id)}</h2>'
            f'<div class="alert"><b>Route:</b> {_html_text(record["queue_route"])}<br><b>Reasons:</b> {_html_text(" | ".join(record["queue_reason_codes"]))}</div>'
            + "".join(occurrences)
            + f"<h3>H1–H7 comparison</h3><table><tr><th>Criterion</th><th>Reviewer A: {_html_text(reviewer_a['requested_model'])}</th><th>Reviewer B: {_html_text(reviewer_b['requested_model'])}</th><th>Opus</th><th>Resolved state</th></tr>{''.join(rows)}</table>"
            + f"<h3>Transformation guidance and assessments</h3><table><tr><th>Role</th><th>Bounded action</th><th>Broader action</th><th>Blocker</th><th>Evidence</th><th>Fidelity / rewrite / verdict / confidence</th></tr>{''.join(proposals)}</table>"
            + f"<h3>Provenance hashes</h3>{_html_pre(record['provenance'])}</section>"
        )
    return _html_document(
        title="AIRisk → JMCUP source-review human holds",
        stage=SOURCE_STAGE,
        cards=cards,
    )


def _final_html(records: Sequence[Mapping[str, Any]]) -> str:
    cards: list[str] = []
    for record in records:
        transformation = record["transformation"]
        validation = record["final_validation"]
        semantic = validation["independent_semantic_validation"]
        response = semantic["validator_response"]
        checks = []
        for key in SEMANTIC_CHECK_KEYS:
            item = response["semantic_checks"][key]
            css = ' class="unresolved"' if item["judgement"] == "uncertain" else ""
            checks.append(
                f"<tr{css}><td>{_html_text(key)}</td><td>{_html_text(item['judgement'])}</td><td>{_html_text(item['rationale'])}</td></tr>"
            )
        triplet = record["rendered_triplet"]
        variants = []
        for key in ("matched_baseline", "unsupported_pressure", "genuine_evidence"):
            item = triplet[key]
            variants.append(
                f"<h4>{_html_text(key)}</h4>{_html_pre(item['shared_scenario_text'])}"
                f"<p><b>Addition:</b></p>{_html_pre(item['condition_addition_text'])}"
            )
        source_text = "".join(
            f"<h4>{_html_text(item['occurrence_id'])}</h4>{_html_pre(item['dilemma'])}"
            for item in record["source_occurrences"]
        )
        cards.append(
            f'<section class="card"><h2 class="case-id">{_html_text(record["transformation_id"])}</h2>'
            f'<div class="alert"><b>Reasons:</b> {_html_text(" | ".join(record["queue_reason_codes"]))}</div>'
            f"<h3>Actions and blocker</h3><table><tr><th>Bounded action</th><th>Broader action</th><th>Baseline blocker</th></tr><tr><td>{_html_text(transformation['bounded_action'])}</td><td>{_html_text(transformation['broader_action'])}</td><td>{_html_text(transformation['baseline_blocker'])}</td></tr></table>"
            + "".join(variants)
            + f"<h3>Source occurrences</h3>{source_text}"
            + f"<h3>Independent V1–V14 validation</h3><p><b>Validator:</b> {_html_text(validation['provenance']['provider'])} / {_html_text(validation['provenance']['requested_model'])}</p><table><tr><th>Criterion</th><th>Judgement</th><th>Rationale</th></tr>{''.join(checks)}</table>"
            + f"<h3>Role/action comparison</h3>{_html_pre(semantic['role_mapping_comparison'])}"
            + f"<h3>Nuisance and unresolved concerns</h3>{_html_pre({'nuisance_cue_assessment': response['nuisance_cue_assessment'], 'unresolved_semantic_concerns': response['unresolved_semantic_concerns']})}"
            + f"<h3>Warning-only surface and lexical diagnostics</h3>{_html_pre(validation['deterministic_diagnostics'])}"
            + f"<h3>Authoring provenance</h3>{_html_pre(transformation['provenance'])}<h3>Validation provenance</h3>{_html_pre(validation['provenance'])}"
            + f"<h3>Review-target hashes</h3>{_html_pre(record['provenance'])}</section>"
        )
    return _html_document(
        title="AIRisk → JMCUP final-transformation human review",
        stage=FINAL_STAGE,
        cards=cards,
    )


def _artifact(role: str, path: Path) -> dict[str, str]:
    return {"role": role, "sha256": sha256_path(path)}


def _write_bundle(
    *,
    stage: str,
    selection_rule: str,
    records: Sequence[Mapping[str, Any]],
    output_dir: Path,
    input_artifacts: Sequence[tuple[str, Path]],
    queue_schema_path: Path,
    submission_schema_path: Path,
    decision_schema_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    prefix = "source" if stage == SOURCE_STAGE else "final"
    paths = {
        "queue_jsonl": output_dir / f"{prefix}_human_review_queue.jsonl",
        "queue_csv": output_dir / f"{prefix}_human_review_queue.csv",
        "html_report": output_dir / f"{prefix}_human_review_report.html",
        "decision_template_jsonl": output_dir / f"{prefix}_human_decision_template.jsonl",
        "decision_template_csv": output_dir / f"{prefix}_human_decision_template.csv",
        "manifest": output_dir / f"{prefix}_human_review_manifest.json",
    }
    _assert_output_paths(list(paths.values()), overwrite=overwrite)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(paths["queue_jsonl"], records)
    queue_rows = [
        _source_queue_row(record) if stage == SOURCE_STAGE else _final_queue_row(record)
        for record in records
    ]
    queue_fields = list(queue_rows[0]) if queue_rows else (
        [
            "review_stage", "generation_group_id", "queue_route",
            "queue_reason_codes", "unresolved_criteria", "disputed_criteria",
            "reviewer_a_provider", "reviewer_a_model", "reviewer_b_provider",
            "reviewer_b_model", "review_target_sha256", "queue_item_sha256",
        ]
        if stage == SOURCE_STAGE
        else [
            "review_stage", "transformation_id", "generation_group_id",
            "queue_reason_codes", "validation_id", "validator_provider",
            "validator_model", "review_target_sha256", "queue_item_sha256",
        ]
    )
    _write_csv(paths["queue_csv"], queue_rows, queue_fields)
    templates = [
        _source_template(record) if stage == SOURCE_STAGE else _final_template(record)
        for record in records
    ]
    submission_schema = load_schema(submission_schema_path)
    for item in templates:
        validate_instance(item, submission_schema, label="human decision template")
    write_jsonl(paths["decision_template_jsonl"], templates)
    template_fields = list(templates[0]) if templates else (
        list(_source_template(records[0])) if records else []
    )
    if not template_fields:
        if stage == SOURCE_STAGE:
            template_fields = [
                "schema_version", "review_stage", "generation_group_id",
                "review_target_sha256", "queue_item_sha256", *SOURCE_HASH_FIELDS,
                *CRITERION_KEYS, "source_fidelity", "rewrite_level",
                "final_disposition", "human_rationale", "reviewer_id",
                "reviewer_name", "review_timestamp", "supersedes_decision_id",
                "supersession_reason",
            ]
        else:
            template_fields = [
                "schema_version", "review_stage", "transformation_id",
                "review_target_sha256", "queue_item_sha256", *FINAL_HASH_FIELDS,
                *SEMANTIC_CHECK_KEYS, *FINAL_ROLE_FIELDS, "final_disposition",
                "human_rationale", "revision_suggestion", "reviewer_id",
                "reviewer_name", "review_timestamp", "supersedes_decision_id",
                "supersession_reason",
            ]
    _write_csv(paths["decision_template_csv"], templates, template_fields)
    paths["html_report"].write_text(
        _source_html(records) if stage == SOURCE_STAGE else _final_html(records),
        encoding="utf-8",
        newline="\n",
    )
    case_key = "generation_group_id" if stage == SOURCE_STAGE else "transformation_id"
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "review_stage": stage,
        "selection_rule": selection_rule,
        "record_count": len(records),
        "ordered_case_ids": [record[case_key] for record in records],
        "input_artifacts": [_artifact(role, path) for role, path in input_artifacts],
        "schema_artifacts": [
            _artifact("queue_schema", queue_schema_path),
            _artifact("submission_schema", submission_schema_path),
            _artifact("decision_schema", decision_schema_path),
            _artifact("bundle_manifest_schema", DEFAULT_MANIFEST_SCHEMA_PATH),
            _artifact("audit_schema", DEFAULT_AUDIT_SCHEMA_PATH),
        ],
        "output_artifacts": [
            _artifact(role, path)
            for role, path in paths.items()
            if role != "manifest"
        ],
        "canonical_authoritative_format": "jsonl",
        "external_api_calls": 0,
        "runtime_artifact_discovery_used": False,
    }
    validate_instance(
        manifest, load_schema(DEFAULT_MANIFEST_SCHEMA_PATH),
        label="human-review bundle manifest",
    )
    _write_json(paths["manifest"], manifest)
    return {
        **manifest,
        "output_paths": {key: str(path.resolve()) for key, path in paths.items()},
        "manifest_sha256": sha256_path(paths["manifest"]),
    }


def prepare_source_human_review_bundle(
    *,
    source_payloads_path: Path,
    resolved_reviews_path: Path,
    eligibility_path: Path,
    comparisons_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    sources = _read_validated_index(
        source_payloads_path, schema_path=DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH,
        key="generation_group_id", label="source group",
    )
    resolved = _read_validated_index(
        resolved_reviews_path, schema_path=DEFAULT_RESOLVED_SCHEMA_PATH,
        key="generation_group_id", label="resolved source review",
    )
    eligibility = _read_validated_index(
        eligibility_path, schema_path=DEFAULT_ELIGIBILITY_SCHEMA_PATH,
        key="generation_group_id", label="source eligibility",
    )
    comparisons = _read_validated_index(
        comparisons_path, schema_path=DEFAULT_COMPARISON_SCHEMA_PATH,
        key="generation_group_id", label="review comparison",
    )
    source_response_schema = load_schema(DEFAULT_SOURCE_RESPONSE_SCHEMA_PATH)
    pending_resolved = {
        key for key, value in resolved.items()
        if value["resolution_status"] == "pending_human_review"
    }
    pending_eligibility = {
        key for key, value in eligibility.items()
        if value["eligibility"] == "pending_human_review"
    }
    if pending_resolved != pending_eligibility:
        raise SemanticReviewError(
            "Resolved-review and eligibility human-hold sets differ; "
            f"resolved_only={sorted(pending_resolved - pending_eligibility)}, "
            f"eligibility_only={sorted(pending_eligibility - pending_resolved)}"
        )
    records: list[dict[str, Any]] = []
    for group_id in sorted(pending_resolved):
        if group_id not in sources or group_id not in comparisons:
            raise SemanticReviewError(f"Human source hold lacks source/comparison for {group_id}")
        source = sources[group_id]
        review = resolved[group_id]
        eligibility_record = eligibility[group_id]
        comparison = comparisons[group_id]
        for reviewer_label in ("reviewer_a", "reviewer_b"):
            validate_instance(
                comparison[reviewer_label]["response"],
                source_response_schema,
                label=f"{reviewer_label} canonical source review",
            )
            if comparison[reviewer_label]["response"]["generation_group_id"] != group_id:
                raise SemanticReviewError(
                    f"{reviewer_label} response group differs for {group_id}"
                )
        source_sha = _record_sha(source)
        resolved_sha = _record_sha(review)
        eligibility_sha = _record_sha(eligibility_record)
        comparison_sha = _record_sha(comparison)
        if review["provenance"]["source_group_input_payload_sha256"] != source_sha:
            raise SemanticReviewError(f"Resolved source payload hash differs for {group_id}")
        if eligibility_record["resolved_source_review_sha256"] != resolved_sha:
            raise SemanticReviewError(f"Eligibility resolved-review hash differs for {group_id}")
        if review["provenance"]["comparison_record_sha256"] != comparison_sha:
            raise SemanticReviewError(f"Resolved comparison hash differs for {group_id}")
        if review["provenance"]["reviewer_a_record_sha256"] != _record_sha(comparison["reviewer_a"]):
            raise SemanticReviewError(f"Reviewer A hash differs for {group_id}")
        if review["provenance"]["reviewer_b_record_sha256"] != _record_sha(comparison["reviewer_b"]):
            raise SemanticReviewError(f"Reviewer B hash differs for {group_id}")
        unresolved = sorted(
            key for key, item in review["criteria"].items()
            if item["resolution_status"] == "unresolved"
            or item["resolved_judgement"] in {None, "uncertain"}
        )
        disputed = sorted(
            key for key, item in review["criteria"].items()
            if item["reviewer_a"] != item["reviewer_b"]
        )
        reasons = ["resolved_source_pending_human_review"]
        reasons.extend(f"unresolved_criterion:{key}" for key in unresolved)
        reasons.extend(f"reviewer_disagreement:{key}" for key in disputed)
        if review["source_fidelity"]["resolution_status"] == "unresolved":
            reasons.append("source_fidelity_unresolved")
        if review["rewrite_level"]["resolution_status"] == "unresolved":
            reasons.append("rewrite_level_unresolved")
        if review.get("qc_evidence") is not None:
            reasons.append(f"qc_outcome:{review['qc_evidence']['qc_outcome']}")
            route = "consensus_qc_disagreement"
        elif review.get("adjudication_evidence") is not None:
            reasons.append("opus_adjudication_unresolved")
            route = "adjudication_ambiguity"
        else:
            route = "unresolved_model_resolution"
        hashes = {
            "source_group_record_sha256": source_sha,
            "resolved_source_review_sha256": resolved_sha,
            "source_eligibility_record_sha256": eligibility_sha,
            "review_comparison_record_sha256": comparison_sha,
        }
        record = {
            "schema_version": SOURCE_QUEUE_VERSION,
            "review_stage": SOURCE_STAGE,
            "generation_group_id": group_id,
            "queue_route": route,
            "queue_reason_codes": sorted(set(reasons)),
            "unresolved_criteria": unresolved,
            "disputed_criteria": disputed,
            "source_group": source,
            "resolved_source_review": review,
            "source_eligibility": eligibility_record,
            "review_comparison": comparison,
            "provenance": {
                "review_target_sha256": _review_target_sha(
                    stage=SOURCE_STAGE, case_id=group_id, record_hashes=hashes
                ),
                **hashes,
                "source_group_schema_sha256": sha256_path(DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH),
                "resolved_source_schema_sha256": sha256_path(DEFAULT_RESOLVED_SCHEMA_PATH),
                "source_eligibility_schema_sha256": sha256_path(DEFAULT_ELIGIBILITY_SCHEMA_PATH),
                "review_comparison_schema_sha256": sha256_path(DEFAULT_COMPARISON_SCHEMA_PATH),
            },
            "semantic_truth_deterministically_established": False,
        }
        validate_instance(
            record, load_schema(DEFAULT_SOURCE_QUEUE_SCHEMA_PATH),
            label="source human-review queue item",
        )
        records.append(record)
    return _write_bundle(
        stage=SOURCE_STAGE,
        selection_rule=(
            "resolved_source_review.resolution_status=pending_human_review and "
            "source_eligibility.eligibility=pending_human_review"
        ),
        records=records,
        output_dir=output_dir.resolve(),
        input_artifacts=(
            ("source_payloads", source_payloads_path),
            ("resolved_source_reviews", resolved_reviews_path),
            ("source_eligibility", eligibility_path),
            ("review_comparisons", comparisons_path),
        ),
        queue_schema_path=DEFAULT_SOURCE_QUEUE_SCHEMA_PATH,
        submission_schema_path=DEFAULT_SOURCE_SUBMISSION_SCHEMA_PATH,
        decision_schema_path=DEFAULT_SOURCE_DECISION_SCHEMA_PATH,
        overwrite=overwrite,
    )


def prepare_final_human_review_bundle(
    *,
    transformations_path: Path,
    final_validations_path: Path,
    source_payloads_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    transformations = _read_validated_index(
        transformations_path, schema_path=DEFAULT_TRANSFORMATION_SCHEMA_PATH,
        key="transformation_id", label="transformation",
    )
    validations = _read_validated_index(
        final_validations_path, schema_path=DEFAULT_FINAL_RECORD_SCHEMA_PATH,
        key="transformation_id", label="final validation",
    )
    sources = _read_validated_index(
        source_payloads_path, schema_path=DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH,
        key="generation_group_id", label="source group",
    )
    records: list[dict[str, Any]] = []
    for transformation_id in sorted(validations):
        validation = validations[transformation_id]
        if validation["final_disposition"] != "human_review":
            continue
        if validation["validation_route"] != "independent_semantic_validation":
            raise SemanticReviewError(
                f"Human-review final validation uses non-semantic route: {transformation_id}"
            )
        if transformation_id not in transformations:
            raise SemanticReviewError(f"Final human review lacks transformation {transformation_id}")
        transformation = transformations[transformation_id]
        group_id = transformation["generation_group_id"]
        if group_id not in sources:
            raise SemanticReviewError(f"Final human review lacks source group {group_id}")
        source = sources[group_id]
        transformation_sha = _record_sha(transformation)
        validation_sha = _record_sha(validation)
        source_sha = _record_sha(source)
        if validation["transformation_record_sha256"] != transformation_sha:
            raise SemanticReviewError(f"Final validation transformation hash differs for {transformation_id}")
        if transformation["source_group_input_payload_sha256"] != source_sha:
            raise SemanticReviewError(f"Transformation source hash differs for {transformation_id}")
        if validation["provenance"]["source_group_input_payload_sha256"] != source_sha:
            raise SemanticReviewError(f"Final validation source hash differs for {transformation_id}")
        occurrence_by_id = {
            item["occurrence_id"]: item for item in source["contextualisations"]
        }
        missing = sorted(
            set(transformation["source_occurrence_ids_used"]) - set(occurrence_by_id)
        )
        if missing:
            raise SemanticReviewError(
                f"Transformation source occurrences are missing for {transformation_id}: {missing}"
            )
        selected_occurrences = [
            occurrence_by_id[key]
            for key in sorted(transformation["source_occurrence_ids_used"])
        ]
        hashes = {
            "transformation_record_sha256": transformation_sha,
            "final_validation_record_sha256": validation_sha,
            "source_group_record_sha256": source_sha,
        }
        record = {
            "schema_version": FINAL_QUEUE_VERSION,
            "review_stage": FINAL_STAGE,
            "transformation_id": transformation_id,
            "generation_group_id": group_id,
            "queue_reason_codes": sorted(set(validation["disposition_reasons"])),
            "transformation": transformation,
            "rendered_triplet": render_transformation_conditions(transformation),
            "source_occurrences": selected_occurrences,
            "final_validation": validation,
            "provenance": {
                "review_target_sha256": _review_target_sha(
                    stage=FINAL_STAGE, case_id=transformation_id,
                    record_hashes=hashes,
                ),
                **hashes,
                "transformation_schema_sha256": sha256_path(DEFAULT_TRANSFORMATION_SCHEMA_PATH),
                "final_validation_schema_sha256": sha256_path(DEFAULT_FINAL_RECORD_SCHEMA_PATH),
                "source_group_schema_sha256": sha256_path(DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH),
            },
            "semantic_truth_deterministically_established": False,
        }
        validate_instance(
            record, load_schema(DEFAULT_FINAL_QUEUE_SCHEMA_PATH),
            label="final human-review queue item",
        )
        records.append(record)
    return _write_bundle(
        stage=FINAL_STAGE,
        selection_rule="final_validation.final_disposition=human_review",
        records=records,
        output_dir=output_dir.resolve(),
        input_artifacts=(
            ("transformations", transformations_path),
            ("final_validations", final_validations_path),
            ("source_payloads", source_payloads_path),
        ),
        queue_schema_path=DEFAULT_FINAL_QUEUE_SCHEMA_PATH,
        submission_schema_path=DEFAULT_FINAL_SUBMISSION_SCHEMA_PATH,
        decision_schema_path=DEFAULT_FINAL_DECISION_SCHEMA_PATH,
        overwrite=overwrite,
    )


def _normalise_csv_value(value: Any) -> Any:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def read_decision_submissions(path: Path, *, stage: str) -> list[dict[str, Any]]:
    if stage not in {SOURCE_STAGE, FINAL_STAGE}:
        raise SemanticReviewError(f"Unknown human-review stage {stage!r}")
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [
                {key: _normalise_csv_value(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]
    else:
        rows = read_jsonl(path)
    schema_path = (
        DEFAULT_SOURCE_SUBMISSION_SCHEMA_PATH
        if stage == SOURCE_STAGE else DEFAULT_FINAL_SUBMISSION_SCHEMA_PATH
    )
    schema = load_schema(schema_path)
    for row in rows:
        validate_instance(row, schema, label="human decision submission")
    return rows


def _require_text(record: Mapping[str, Any], field: str, *, optional: bool = False) -> str | None:
    value = record.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SemanticReviewError(f"Human decision requires non-empty {field}")
    return value.strip()


def _validate_timestamp(value: Any) -> str:
    text = _require_text({"review_timestamp": value}, "review_timestamp")
    assert isinstance(text, str)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SemanticReviewError("review_timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SemanticReviewError("review_timestamp must include a UTC offset")
    return text


def _common_submission_checks(
    submission: Mapping[str, Any], queue: Mapping[str, Any], *, hash_fields: Sequence[str]
) -> None:
    if submission["review_target_sha256"] != queue["provenance"]["review_target_sha256"]:
        raise SemanticReviewError("Stale human decision: review-target hash changed")
    if submission["queue_item_sha256"] != _record_sha(queue):
        raise SemanticReviewError("Stale human decision: queue item hash changed")
    for field in hash_fields:
        if submission[field] != queue["provenance"][field]:
            raise SemanticReviewError(f"Stale human decision: {field} changed")
    _require_text(submission, "human_rationale")
    _require_text(submission, "reviewer_id")
    _validate_timestamp(submission.get("review_timestamp"))
    reviewer_name = submission.get("reviewer_name")
    if reviewer_name is not None and (
        not isinstance(reviewer_name, str) or not reviewer_name.strip()
    ):
        raise SemanticReviewError("reviewer_name must be null or non-empty")
    supersedes = submission.get("supersedes_decision_id")
    reason = submission.get("supersession_reason")
    if supersedes is None and reason is not None:
        raise SemanticReviewError("supersession_reason requires supersedes_decision_id")
    if supersedes is not None:
        _require_text(submission, "supersedes_decision_id")
        _require_text(submission, "supersession_reason")


def _source_decision(
    submission: Mapping[str, Any], queue: Mapping[str, Any]
) -> dict[str, Any]:
    _common_submission_checks(submission, queue, hash_fields=SOURCE_HASH_FIELDS)
    criteria = {key: submission.get(key) for key in CRITERION_KEYS}
    if any(value not in {"yes", "no", "uncertain"} for value in criteria.values()):
        raise SemanticReviewError("Completed source decision requires every H1-H7 judgement")
    fidelity = submission.get("source_fidelity")
    rewrite = submission.get("rewrite_level")
    rule = source_eligibility_rule(
        criterion_judgements=criteria,
        source_fidelity=fidelity,
        rewrite_level=rewrite,
    )
    disposition = submission.get("final_disposition")
    expected = "eligible" if rule["eligible"] else "reject"
    if disposition != expected:
        raise SemanticReviewError(
            f"Source disposition {disposition!r} cannot bypass frozen eligibility; expected {expected!r}"
        )
    evaluation = {
        "basis": "resolved_h1_h7_source_fidelity_and_rewrite_feasibility",
        **rule,
    }
    base = {
        "schema_version": SOURCE_DECISION_VERSION,
        "review_stage": SOURCE_STAGE,
        "generation_group_id": submission["generation_group_id"],
        "review_target_sha256": submission["review_target_sha256"],
        "queue_item_sha256": submission["queue_item_sha256"],
        "reviewed_record_hashes": {
            field: submission[field] for field in SOURCE_HASH_FIELDS
        },
        "criteria": criteria,
        "source_fidelity": fidelity,
        "rewrite_level": rewrite,
        "final_disposition": disposition,
        "eligibility_rule_evaluation": evaluation,
        "human_rationale": _require_text(submission, "human_rationale"),
        "reviewer_id": _require_text(submission, "reviewer_id"),
        "reviewer_name": _require_text(submission, "reviewer_name", optional=True),
        "review_timestamp": _validate_timestamp(submission["review_timestamp"]),
        "supersedes_decision_id": submission.get("supersedes_decision_id"),
        "supersession_reason": submission.get("supersession_reason"),
        "input_records_mutated": False,
    }
    digest = sha256_text(canonical_json(base))
    record = {
        **base,
        "decision_id": f"airisk_jmcup_source_human_decision_{digest[:24]}",
    }
    validate_instance(
        record, load_schema(DEFAULT_SOURCE_DECISION_SCHEMA_PATH),
        label="source human decision",
    )
    return record


def _final_decision(
    submission: Mapping[str, Any], queue: Mapping[str, Any]
) -> dict[str, Any]:
    _common_submission_checks(submission, queue, hash_fields=FINAL_HASH_FIELDS)
    criteria = {key: submission.get(key) for key in SEMANTIC_CHECK_KEYS}
    if any(value not in {"pass", "uncertain", "fail"} for value in criteria.values()):
        raise SemanticReviewError("Completed final decision requires every V1-V14 judgement")
    role_checks = {field: submission.get(field) for field in FINAL_ROLE_FIELDS}
    expected_roles = {
        "baseline_preferred_action": "bounded",
        "unsupported_pressure_variant": "pressure",
        "preferred_action_after_unsupported_pressure": "bounded",
        "genuine_evidence_variant": "evidence",
        "preferred_action_after_genuine_evidence": "broader",
    }
    allowed_roles = {
        "baseline_preferred_action": {"bounded", "broader", "uncertain"},
        "unsupported_pressure_variant": {"pressure", "evidence", "uncertain"},
        "preferred_action_after_unsupported_pressure": {"bounded", "broader", "uncertain"},
        "genuine_evidence_variant": {"pressure", "evidence", "uncertain"},
        "preferred_action_after_genuine_evidence": {"bounded", "broader", "uncertain"},
    }
    invalid = [
        key for key, value in role_checks.items() if value not in allowed_roles[key]
    ]
    if invalid:
        raise SemanticReviewError(f"Completed final decision lacks role re-checks: {invalid}")
    all_pass = all(value == "pass" for value in criteria.values())
    all_roles = all(role_checks[key] == value for key, value in expected_roles.items())
    acceptable = all_pass and all_roles
    reasons: list[str] = []
    if all_pass:
        reasons.append("all_v1_v14_pass")
    else:
        reasons.extend(
            f"semantic_check_not_pass:{key}"
            for key, value in criteria.items() if value != "pass"
        )
    if all_roles:
        reasons.append("all_role_action_rechecks_match")
    else:
        reasons.extend(
            f"role_action_recheck_mismatch:{key}"
            for key, value in expected_roles.items() if role_checks[key] != value
        )
    disposition = submission.get("final_disposition")
    expected_disposition = "accept" if acceptable else "reject"
    if disposition != expected_disposition:
        raise SemanticReviewError(
            f"Final disposition {disposition!r} cannot bypass frozen acceptance; expected {expected_disposition!r}"
        )
    base = {
        "schema_version": FINAL_DECISION_VERSION,
        "review_stage": FINAL_STAGE,
        "transformation_id": submission["transformation_id"],
        "review_target_sha256": submission["review_target_sha256"],
        "queue_item_sha256": submission["queue_item_sha256"],
        "reviewed_record_hashes": {
            field: submission[field] for field in FINAL_HASH_FIELDS
        },
        "criteria": criteria,
        "role_action_rechecks": role_checks,
        "final_disposition": disposition,
        "acceptance_rule_evaluation": {
            "all_v1_v14_pass": all_pass,
            "all_role_action_rechecks_match": all_roles,
            "acceptable": acceptable,
            "reason_codes": sorted(set(reasons)),
        },
        "human_rationale": _require_text(submission, "human_rationale"),
        "revision_suggestion": _require_text(
            submission, "revision_suggestion", optional=True
        ),
        "revision_suggestion_is_non_binding": True,
        "reviewer_id": _require_text(submission, "reviewer_id"),
        "reviewer_name": _require_text(submission, "reviewer_name", optional=True),
        "review_timestamp": _validate_timestamp(submission["review_timestamp"]),
        "supersedes_decision_id": submission.get("supersedes_decision_id"),
        "supersession_reason": submission.get("supersession_reason"),
        "input_records_mutated": False,
    }
    digest = sha256_text(canonical_json(base))
    record = {
        **base,
        "decision_id": f"airisk_jmcup_final_human_decision_{digest[:24]}",
    }
    validate_instance(
        record, load_schema(DEFAULT_FINAL_DECISION_SCHEMA_PATH),
        label="final human decision",
    )
    return record


def _queue_index(queue_path: Path, *, stage: str) -> dict[str, dict[str, Any]]:
    schema_path = (
        DEFAULT_SOURCE_QUEUE_SCHEMA_PATH
        if stage == SOURCE_STAGE else DEFAULT_FINAL_QUEUE_SCHEMA_PATH
    )
    key = "generation_group_id" if stage == SOURCE_STAGE else "transformation_id"
    return _read_validated_index(
        queue_path, schema_path=schema_path, key=key, label="human-review queue item"
    )


def _ledger_records(ledger_path: Path, *, stage: str) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    records = read_jsonl(ledger_path)
    schema_path = (
        DEFAULT_SOURCE_DECISION_SCHEMA_PATH
        if stage == SOURCE_STAGE else DEFAULT_FINAL_DECISION_SCHEMA_PATH
    )
    schema = load_schema(schema_path)
    seen: dict[str, dict[str, Any]] = {}
    active_by_target: dict[str, str] = {}
    for record in records:
        validate_instance(record, schema, label="stored human decision")
        decision_id = record["decision_id"]
        if decision_id in seen:
            raise SemanticReviewError(f"Duplicate decision ID in ledger: {decision_id}")
        target = record["review_target_sha256"]
        supersedes = record["supersedes_decision_id"]
        active = active_by_target.get(target)
        if supersedes is None:
            if active is not None:
                raise SemanticReviewError(
                    f"Duplicate unsuperseded human decision for target {target}"
                )
        elif supersedes != active:
            raise SemanticReviewError(
                f"Invalid supersession chain for {decision_id}: expected {active!r}"
            )
        seen[decision_id] = record
        active_by_target[target] = decision_id
    return records


def _append_durable(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(canonical_json(record) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def append_human_decisions(
    *,
    stage: str,
    queue_path: Path,
    submissions_path: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    queue = _queue_index(queue_path, stage=stage)
    submissions = read_decision_submissions(submissions_path, stage=stage)
    existing = _ledger_records(ledger_path, stage=stage)
    case_key = "generation_group_id" if stage == SOURCE_STAGE else "transformation_id"
    existing_ids = {record["decision_id"] for record in existing}
    active_by_target: dict[str, str] = {}
    for record in existing:
        active_by_target[record["review_target_sha256"]] = record["decision_id"]
    new_records: list[dict[str, Any]] = []
    submitted_cases: set[str] = set()
    for submission in submissions:
        case_id = submission[case_key]
        if case_id in submitted_cases:
            raise SemanticReviewError(f"Duplicate submission row for {case_id}")
        submitted_cases.add(case_id)
        if case_id not in queue:
            raise SemanticReviewError(f"Human decision targets absent/stale queue case {case_id}")
        record = (
            _source_decision(submission, queue[case_id])
            if stage == SOURCE_STAGE else _final_decision(submission, queue[case_id])
        )
        if record["decision_id"] in existing_ids:
            raise SemanticReviewError(f"Duplicate decision ID {record['decision_id']}")
        target = record["review_target_sha256"]
        active = active_by_target.get(target)
        supersedes = record["supersedes_decision_id"]
        if active is None and supersedes is not None:
            raise SemanticReviewError(
                f"Cannot supersede absent decision {supersedes!r} for {case_id}"
            )
        if active is not None and supersedes != active:
            raise SemanticReviewError(
                f"Exact artefact already has final decision {active}; explicit supersession is required"
            )
        active_by_target[target] = record["decision_id"]
        existing_ids.add(record["decision_id"])
        new_records.append(record)
    _append_durable(ledger_path, new_records)
    return {
        "review_stage": stage,
        "appended_count": len(new_records),
        "decision_ids": [record["decision_id"] for record in new_records],
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": sha256_path(ledger_path) if ledger_path.exists() else None,
        "external_api_calls": 0,
    }


def audit_human_decisions(
    *,
    stage: str,
    queue_path: Path,
    ledger_path: Path,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    queue = _queue_index(queue_path, stage=stage)
    decisions = _ledger_records(ledger_path, stage=stage)
    case_key = "generation_group_id" if stage == SOURCE_STAGE else "transformation_id"
    superseded_ids = {
        record["supersedes_decision_id"]
        for record in decisions if record["supersedes_decision_id"] is not None
    }
    rows: list[dict[str, Any]] = []
    for record in decisions:
        case_id = record[case_key]
        current = queue.get(case_id)
        stale_reason = None
        if current is None:
            stale_reason = "review_target_absent_from_current_queue"
        elif current["provenance"]["review_target_sha256"] != record["review_target_sha256"]:
            stale_reason = "upstream_review_target_hash_changed"
        elif _record_sha(current) != record["queue_item_sha256"]:
            stale_reason = "queue_item_hash_changed"
        superseded = record["decision_id"] in superseded_ids
        status = (
            "superseded_stale" if superseded and stale_reason
            else "superseded_current" if superseded
            else "active_stale" if stale_reason
            else "active_current"
        )
        rows.append({
            "decision_id": record["decision_id"],
            "case_id": case_id,
            "review_target_sha256": record["review_target_sha256"],
            "status": status,
            "stale_reason": stale_reason,
            "supersedes_decision_id": record["supersedes_decision_id"],
        })
    report = {
        "schema_version": AUDIT_VERSION,
        "review_stage": stage,
        "queue_jsonl_sha256": sha256_path(queue_path),
        "decision_ledger_sha256": sha256_path(ledger_path) if ledger_path.exists() else None,
        "queue_count": len(queue),
        "decision_count": len(decisions),
        "active_count": sum(row["status"].startswith("active_") for row in rows),
        "superseded_count": sum(row["status"].startswith("superseded_") for row in rows),
        "stale_count": sum(row["status"].endswith("_stale") for row in rows),
        "decisions": rows,
    }
    validate_instance(
        report, load_schema(DEFAULT_AUDIT_SCHEMA_PATH),
        label="human-review audit report",
    )
    if output_path is not None:
        if output_path.exists() and not overwrite:
            raise SemanticReviewError(f"Refusing to overwrite audit report {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output_path, report)
    return report
