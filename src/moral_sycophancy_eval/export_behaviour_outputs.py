from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from inspect_ai.log import read_eval_log


RAW_FIELDNAMES = [
    "sample_id",
    "case_id",
    "source_item_id",
    "dataset_version",
    "variant",
    "prompt_style",
    "moral_domain",
    "risk_track",
    "evidence_quality",
    "pressure_type",
    "followup_strength",
    "expected_update",
    "target_scope",
    "minimum_acceptable_scope",
    "maximum_acceptable_scope",
    "target_release_scope",
    "minimum_acceptable_release_scope",
    "maximum_acceptable_release_scope",
    # Schema-v2 / schema-v2.1 structured access fields.
    "access_purpose",
    "access_intent",
    "access_population",
    "access_modality",
    "operational_status",
    "real_world_exposure",
    "externalisation_level",
    "reversibility_level",
    "monitorability_level",
    "enforceability_level",
    "misuse_evaluation_state",
    "safeguard_relevance_to_original_concern",
    "pressure_source",
    "pressure_mechanism",
    "pressure_legitimacy",
    "pressure_escalation_stage",
    "pressure_target",
    "conflict_type",
    "situational_awareness_context",
    "target_access",
    "minimum_acceptable_access",
    "maximum_acceptable_access",
    # Existing descriptive and audit-support fields.
    "expected_behaviour",
    "difficulty",
    "difficulty_notes",
    "deployment_scope_options",
    "release_scope_options",
    "release_artifact",
    "reversibility_notes",
    "monitorability_notes",
    "pressure_trap",
    "too_conservative_failure",
    "too_permissive_failure",
    "scope_control_notes",
    "missing_evaluation_types",
    "safeguard_type",
    "failure_modes",
    "relevant_moral_features",
    "safeguard_features",
    "missing_safeguards",
    "residual_risk_features",
    "ideal_behaviour",
    "output",
]

REVIEW_FIELDNAMES = [
    "manual_score_0_to_3",
    "primary_failure_class",
    "confidence",
    "action",
    "notes",
]


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def stringify(value: Any) -> str:
    """Return a stable string representation for CSV/Markdown export.

    Inspect metadata can include lists and objects for fields such as failure
    modes and schema-v2 target access. JSON keeps those values parseable instead
    of flattening them into ambiguous comma-separated strings.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def metadata_value(metadata: dict[str, Any], key: str) -> str:
    return stringify(metadata.get(key, ""))


def latest_eval_log(log_dir: Path) -> Path:
    logs = sorted(log_dir.glob("*.eval"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not logs:
        raise FileNotFoundError(f"No .eval logs found in {log_dir}")
    return logs[0]


def extract_completion(sample: Any) -> str:
    output = get_attr_or_key(sample, "output", None)
    completion = get_attr_or_key(output, "completion", "")
    if completion:
        return str(completion)

    # Fallback for logs where the final assistant message is stored in messages.
    messages = get_attr_or_key(sample, "messages", []) or []
    for message in reversed(messages):
        role = str(get_attr_or_key(message, "role", "")).lower()
        content = get_attr_or_key(message, "content", "")
        if role == "assistant" and content:
            return str(content)

    return ""


def extract_outputs(log_path: Path) -> list[dict[str, str]]:
    log = read_eval_log(log_path)

    if not log.samples:
        raise ValueError(
            "This log has no samples. Re-run the eval with sample logging enabled, "
            "or choose a completed log that contains samples."
        )

    rows: list[dict[str, str]] = []

    for sample in log.samples:
        metadata = get_attr_or_key(sample, "metadata", {}) or {}
        row = {field: metadata_value(metadata, field) for field in RAW_FIELDNAMES}
        row["sample_id"] = str(get_attr_or_key(sample, "id", ""))
        row["ideal_behaviour"] = row["ideal_behaviour"] or stringify(
            get_attr_or_key(sample, "target", "")
        )
        row["output"] = extract_completion(sample)
        rows.append(row)

    return rows


def row_to_dict(row: dict[str, str], include_review_columns: bool) -> dict[str, str]:
    values = {field: row.get(field, "") for field in RAW_FIELDNAMES}

    if include_review_columns:
        values.update({field: row.get(field, "") for field in REVIEW_FIELDNAMES})

    return values


def write_csv(rows: list[dict[str, str]], output_path: Path, include_review_columns: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = RAW_FIELDNAMES + (REVIEW_FIELDNAMES if include_review_columns else [])

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row_to_dict(row, include_review_columns=include_review_columns))


def escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def write_markdown(rows: list[dict[str, str]], output_path: Path, include_review_columns: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if include_review_columns:
        title = "# Behavioural eval outputs for manual review\n\n"
        description = "Use the project-specific manual scoring rubric for the dataset being audited.\n\n"
        header = (
            "| sample_id | case_id | dataset_version | moral_domain | evidence_quality | "
            "pressure_type | expected_update | target_release_scope | access_purpose | "
            "access_intent | access_population | access_modality | operational_status | "
            "real_world_exposure | externalisation_level | ideal_behaviour | output | "
            "score_0_to_3 | failure_class | notes |\n"
        )
        separator = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---:|---|---|\n"
    else:
        title = "# Behavioural eval outputs\n\n"
        description = "Raw exported model outputs. Manual scoring belongs in a separate audit file.\n\n"
        header = (
            "| sample_id | case_id | dataset_version | moral_domain | evidence_quality | "
            "pressure_type | expected_update | target_release_scope | access_purpose | "
            "access_intent | access_population | access_modality | operational_status | "
            "real_world_exposure | externalisation_level | ideal_behaviour | output |\n"
        )
        separator = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"

    base_fields = [
        "sample_id",
        "case_id",
        "dataset_version",
        "moral_domain",
        "evidence_quality",
        "pressure_type",
        "expected_update",
        "target_release_scope",
        "access_purpose",
        "access_intent",
        "access_population",
        "access_modality",
        "operational_status",
        "real_world_exposure",
        "externalisation_level",
        "ideal_behaviour",
        "output",
    ]

    with output_path.open("w", encoding="utf-8") as f:
        f.write(title)
        f.write(description)
        f.write(header)
        f.write(separator)
        for row in rows:
            escaped_cells = [escape_table_cell(row.get(field, "")) for field in base_fields]
            if include_review_columns:
                escaped_cells.extend(["", "", ""])
            f.write("| " + " | ".join(escaped_cells) + " |\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export behavioural Inspect eval outputs.")
    parser.add_argument(
        "log",
        nargs="?",
        type=Path,
        help="Path to a specific behavioural .eval log. If omitted, uses the latest .eval file in --log-dir.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory containing Inspect .eval logs. Defaults to ./logs.",
    )
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV output path.")
    parser.add_argument("--md", type=Path, default=None, help="Optional Markdown output path.")
    parser.add_argument(
        "--include-review-columns",
        action="store_true",
        help="Include empty manual review columns in CSV/Markdown exports.",
    )

    args = parser.parse_args()

    try:
        log_path = args.log if args.log else latest_eval_log(args.log_dir)
        rows = extract_outputs(log_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Log: {log_path}")
    print(f"Samples: {len(rows)}")

    missing_outputs = sum(1 for row in rows if not row.get("output", "").strip())
    if missing_outputs:
        print(f"Missing outputs: {missing_outputs}")

    if args.csv:
        write_csv(rows, args.csv, include_review_columns=args.include_review_columns)
        print(f"Wrote CSV: {args.csv}")

    if args.md:
        write_markdown(rows, args.md, include_review_columns=args.include_review_columns)
        print(f"Wrote Markdown: {args.md}")

    if not args.csv and not args.md:
        for row in rows:
            print(f"\n{row.get('sample_id', '')} | {row.get('case_id', '')} | {row.get('moral_domain', '')}")
            metadata_summary = " | ".join(
                part
                for part in [
                    row.get("evidence_quality", ""),
                    row.get("pressure_type", ""),
                    row.get("expected_update", ""),
                    row.get("target_scope", ""),
                    row.get("target_release_scope", ""),
                    row.get("access_purpose", ""),
                    row.get("access_intent", ""),
                    row.get("access_population", ""),
                    row.get("access_modality", ""),
                    row.get("operational_status", ""),
                    row.get("real_world_exposure", ""),
                    row.get("externalisation_level", ""),
                ]
                if part
            )
            if metadata_summary:
                print(metadata_summary)
            print(row.get("output", ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
