"""Build a DuckDB analysis database from the JSONL eval datasets.

The JSONL files remain the canonical dataset source of truth. This script creates a
local, derived database for coverage checks, ad hoc analysis, and later run/result
storage.

Default output:
    results/duckdb/moral_evals.duckdb

Usage:
    python scripts/export_dataset_to_duckdb.py
    python scripts/export_dataset_to_duckdb.py --output results/duckdb/moral_evals.duckdb
    python scripts/export_dataset_to_duckdb.py --data-dir data --overwrite
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:  # pragma: no cover - helpful runtime message
    raise SystemExit(
        "Missing dependency: duckdb. Install it with `pip install duckdb`."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "duckdb" / "moral_evals.duckdb"

SCOPE_OPTIONS: list[tuple[str, int]] = [
    ("no_deployment", 0),
    ("lab_only_evaluation", 1),
    ("read_only_pilot", 2),
    ("narrow_constrained_pilot", 3),
    ("staged_limited_deployment", 4),
    ("broad_deployment", 5),
    ("unrestricted_deployment", 6),
]

LIST_FIELDS = [
    "deployment_scope_options",
    "failure_modes",
    "relevant_moral_features",
    "safeguard_features",
    "missing_safeguards",
    "residual_risk_features",
]


def text_or_none(value: Any) -> str | None:
    """Return a string value, preserving missing/None as NULL."""
    if value is None:
        return None
    return str(value)


def list_of_text(value: Any) -> list[str]:
    """Normalise JSON scalar/list/missing values into a list[str]."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def infer_dataset_family(dataset_version: str) -> str:
    """Infer a coarse dataset family from the current naming conventions."""
    if "scope_control" in dataset_version:
        return "scope_control"
    if "justifiable_corrigibility_pressure" in dataset_version:
        return "justifiable_corrigibility_pressure"
    if "trap_expansion" in dataset_version:
        return "evidence_strength_trap_expansion"
    if "evidence_strength" in dataset_version:
        return "evidence_strength"
    if dataset_version in {"v0", "v1", "v2"}:
        return "seed_behaviour"
    return "unknown"


def infer_phase(dataset_version: str) -> str:
    """Infer the project phase from the dataset version name."""
    if dataset_version.startswith("v4_"):
        return "phase_3"
    if dataset_version.startswith("v3_"):
        return "phase_2"
    if dataset_version in {"v0", "v1", "v2"}:
        return "phase_1"
    return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl_files(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load all JSONL dataset files from data_dir.

    Returns:
        item_rows: one dict per JSONL record, enriched with source metadata.
        source_rows: one dict per source file.
    """
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    jsonl_paths = sorted(data_dir.glob("*.jsonl"))
    if not jsonl_paths:
        raise FileNotFoundError(f"No JSONL files found in: {data_dir}")

    item_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    seen_item_uids: set[str] = set()

    for path in jsonl_paths:
        records_in_file = 0
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        file_hash = sha256_file(path)

        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue

                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON in {relative_path}:{line_number}: {exc}"
                    ) from exc

                item_id = text_or_none(record.get("id"))
                if not item_id:
                    raise ValueError(f"Missing required `id` in {relative_path}:{line_number}")

                dataset_version = text_or_none(record.get("dataset_version")) or path.stem
                item_uid = f"{dataset_version}::{item_id}"
                if item_uid in seen_item_uids:
                    raise ValueError(f"Duplicate item_uid found: {item_uid}")
                seen_item_uids.add(item_uid)

                item_rows.append(
                    {
                        "item_uid": item_uid,
                        "item_id": item_id,
                        "source_path": relative_path,
                        "source_line_number": line_number,
                        "dataset_version": dataset_version,
                        "raw_record": record,
                    }
                )
                records_in_file += 1

        source_rows.append(
            {
                "source_path": relative_path,
                "sha256": file_hash,
                "record_count": records_in_file,
            }
        )

    return item_rows, source_rows


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables and views from scratch."""
    for view_name in [
        "item_failure_mode",
        "item_relevant_moral_feature",
        "item_safeguard_feature",
        "item_missing_safeguard",
        "item_residual_risk_feature",
        "coverage_by_dataset_version",
        "coverage_by_domain_evidence_pressure",
        "scope_decision_with_rank",
    ]:
        con.execute(f"DROP VIEW IF EXISTS {view_name}")

    for table_name in [
        "score",
        "model_output",
        "eval_run",
        "task_config",
        "model",
        "scope_option",
        "dataset_item_wide",
        "dataset_version",
        "source_file",
    ]:
        con.execute(f"DROP TABLE IF EXISTS {table_name}")

    con.execute(
        """
        CREATE TABLE source_file (
            source_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            exported_at TIMESTAMP NOT NULL
        )
        """
    )

    con.execute(
        """
        CREATE TABLE dataset_version (
            dataset_version TEXT PRIMARY KEY,
            dataset_family TEXT,
            phase TEXT,
            source_path TEXT,
            record_count INTEGER NOT NULL,
            exported_at TIMESTAMP NOT NULL
        )
        """
    )

    con.execute(
        """
        CREATE TABLE dataset_item_wide (
            item_uid TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_line_number INTEGER NOT NULL,
            dataset_version TEXT NOT NULL,
            source_item_id TEXT,
            case_id TEXT,
            base_case_id TEXT,
            variant TEXT,

            moral_domain TEXT,
            risk_track TEXT,
            evidence_quality TEXT,
            pressure_type TEXT,
            followup_strength TEXT,
            expected_update TEXT,
            difficulty TEXT,
            difficulty_notes TEXT,

            scenario TEXT,
            initial_judgement TEXT,
            user_followup TEXT,
            expected_behaviour TEXT,
            ideal_behaviour TEXT,

            target_scope TEXT,
            minimum_acceptable_scope TEXT,
            maximum_acceptable_scope TEXT,
            too_conservative_failure TEXT,
            too_permissive_failure TEXT,
            pressure_trap TEXT,
            scope_control_notes TEXT,

            deployment_scope_options TEXT[],
            failure_modes TEXT[],
            relevant_moral_features TEXT[],
            safeguard_features TEXT[],
            missing_safeguards TEXT[],
            residual_risk_features TEXT[],

            raw_json JSON
        )
        """
    )

    con.execute(
        """
        CREATE TABLE scope_option (
            scope_name TEXT PRIMARY KEY,
            scope_rank INTEGER NOT NULL
        )
        """
    )

    con.execute(
        """
        CREATE TABLE model (
            model_id TEXT PRIMARY KEY,
            provider TEXT,
            model_name TEXT NOT NULL,
            model_version TEXT,
            notes TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE task_config (
            task_config_id TEXT PRIMARY KEY,
            task_name TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            prompt_style TEXT,
            prompt_version TEXT,
            scorer_name TEXT,
            temperature DOUBLE,
            top_p DOUBLE,
            max_tokens INTEGER,
            notes TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE eval_run (
            eval_run_id TEXT PRIMARY KEY,
            task_config_id TEXT NOT NULL,
            model_id TEXT,
            run_timestamp TIMESTAMP,
            inspect_log_path TEXT,
            repo_commit_sha TEXT,
            dataset_file_sha TEXT,
            run_label TEXT,
            notes TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE model_output (
            model_output_id TEXT PRIMARY KEY,
            eval_run_id TEXT NOT NULL,
            item_uid TEXT NOT NULL,
            raw_output TEXT,
            extracted_answer TEXT,
            selected_scope TEXT,
            parsed_json JSON,
            invalid_output BOOLEAN DEFAULT FALSE
        )
        """
    )

    con.execute(
        """
        CREATE TABLE score (
            score_id TEXT PRIMARY KEY,
            model_output_id TEXT NOT NULL,
            score DOUBLE,
            score_label TEXT,
            over_approval_delta INTEGER,
            under_update_delta INTEGER,
            matched_expected_behaviour BOOLEAN,
            manual_review_flag BOOLEAN DEFAULT FALSE,
            manual_notes TEXT
        )
        """
    )

    con.executemany("INSERT INTO scope_option VALUES (?, ?)", SCOPE_OPTIONS)

    con.execute(
        """
        CREATE VIEW item_failure_mode AS
        SELECT item_uid, item_id, dataset_version, unnest(failure_modes) AS failure_mode
        FROM dataset_item_wide
        """
    )

    con.execute(
        """
        CREATE VIEW item_relevant_moral_feature AS
        SELECT item_uid, item_id, dataset_version,
               unnest(relevant_moral_features) AS relevant_moral_feature
        FROM dataset_item_wide
        """
    )

    con.execute(
        """
        CREATE VIEW item_safeguard_feature AS
        SELECT item_uid, item_id, dataset_version,
               unnest(safeguard_features) AS safeguard_feature
        FROM dataset_item_wide
        """
    )

    con.execute(
        """
        CREATE VIEW item_missing_safeguard AS
        SELECT item_uid, item_id, dataset_version,
               unnest(missing_safeguards) AS missing_safeguard
        FROM dataset_item_wide
        """
    )

    con.execute(
        """
        CREATE VIEW item_residual_risk_feature AS
        SELECT item_uid, item_id, dataset_version,
               unnest(residual_risk_features) AS residual_risk_feature
        FROM dataset_item_wide
        """
    )

    con.execute(
        """
        CREATE VIEW coverage_by_dataset_version AS
        SELECT
            dataset_version,
            count(*) AS item_count,
            count(DISTINCT moral_domain) AS moral_domain_count,
            count(DISTINCT evidence_quality) AS evidence_quality_count,
            count(DISTINCT pressure_type) AS pressure_type_count
        FROM dataset_item_wide
        GROUP BY dataset_version
        ORDER BY dataset_version
        """
    )

    con.execute(
        """
        CREATE VIEW coverage_by_domain_evidence_pressure AS
        SELECT
            dataset_version,
            moral_domain,
            evidence_quality,
            pressure_type,
            count(*) AS item_count
        FROM dataset_item_wide
        GROUP BY dataset_version, moral_domain, evidence_quality, pressure_type
        ORDER BY dataset_version, moral_domain, evidence_quality, pressure_type
        """
    )

    con.execute(
        """
        CREATE VIEW scope_decision_with_rank AS
        SELECT
            i.item_uid,
            i.item_id,
            i.dataset_version,
            i.moral_domain,
            i.evidence_quality,
            i.pressure_type,
            i.target_scope,
            target.scope_rank AS target_scope_rank,
            i.minimum_acceptable_scope,
            min_scope.scope_rank AS minimum_acceptable_scope_rank,
            i.maximum_acceptable_scope,
            max_scope.scope_rank AS maximum_acceptable_scope_rank,
            i.too_conservative_failure,
            i.too_permissive_failure,
            i.pressure_trap,
            i.scope_control_notes
        FROM dataset_item_wide AS i
        LEFT JOIN scope_option AS target
            ON i.target_scope = target.scope_name
        LEFT JOIN scope_option AS min_scope
            ON i.minimum_acceptable_scope = min_scope.scope_name
        LEFT JOIN scope_option AS max_scope
            ON i.maximum_acceptable_scope = max_scope.scope_name
        WHERE i.target_scope IS NOT NULL
        """
    )


def insert_dataset(
    con: duckdb.DuckDBPyConnection,
    item_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
) -> None:
    exported_at = datetime.now(timezone.utc).isoformat()

    con.executemany(
        "INSERT INTO source_file VALUES (?, ?, ?, ?)",
        [
            (
                row["source_path"],
                row["sha256"],
                row["record_count"],
                exported_at,
            )
            for row in source_rows
        ],
    )

    dataset_counts: Counter[str] = Counter(row["dataset_version"] for row in item_rows)
    dataset_sources: dict[str, str] = {}
    for row in item_rows:
        dataset_sources.setdefault(row["dataset_version"], row["source_path"])

    con.executemany(
        "INSERT INTO dataset_version VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                dataset_version,
                infer_dataset_family(dataset_version),
                infer_phase(dataset_version),
                dataset_sources.get(dataset_version),
                record_count,
                exported_at,
            )
            for dataset_version, record_count in sorted(dataset_counts.items())
        ],
    )

    item_insert_sql = """
        INSERT INTO dataset_item_wide VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?
        )
    """

    item_values = []
    for row in item_rows:
        record = row["raw_record"]
        item_values.append(
            (
                row["item_uid"],
                row["item_id"],
                row["source_path"],
                row["source_line_number"],
                row["dataset_version"],
                text_or_none(record.get("source_item_id")),
                text_or_none(record.get("case_id")),
                text_or_none(record.get("base_case_id")),
                text_or_none(record.get("variant")),
                text_or_none(record.get("moral_domain")),
                text_or_none(record.get("risk_track")),
                text_or_none(record.get("evidence_quality")),
                text_or_none(record.get("pressure_type")),
                text_or_none(record.get("followup_strength")),
                text_or_none(record.get("expected_update")),
                text_or_none(record.get("difficulty")),
                text_or_none(record.get("difficulty_notes")),
                text_or_none(record.get("scenario")),
                text_or_none(record.get("initial_judgement")),
                text_or_none(record.get("user_followup")),
                text_or_none(record.get("expected_behaviour")),
                text_or_none(record.get("ideal_behaviour")),
                text_or_none(record.get("target_scope")),
                text_or_none(record.get("minimum_acceptable_scope")),
                text_or_none(record.get("maximum_acceptable_scope")),
                text_or_none(record.get("too_conservative_failure")),
                text_or_none(record.get("too_permissive_failure")),
                text_or_none(record.get("pressure_trap")),
                text_or_none(record.get("scope_control_notes")),
                list_of_text(record.get("deployment_scope_options")),
                list_of_text(record.get("failure_modes")),
                list_of_text(record.get("relevant_moral_features")),
                list_of_text(record.get("safeguard_features")),
                list_of_text(record.get("missing_safeguards")),
                list_of_text(record.get("residual_risk_features")),
                json.dumps(record, ensure_ascii=False),
            )
        )

    con.executemany(item_insert_sql, item_values)


def build_database(data_dir: Path, output_path: Path, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output database already exists: {output_path}. "
            "Pass --overwrite to rebuild it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    item_rows, source_rows = load_jsonl_files(data_dir)

    with duckdb.connect(str(output_path)) as con:
        create_schema(con)
        insert_dataset(con, item_rows, source_rows)

        item_count = con.execute("SELECT count(*) FROM dataset_item_wide").fetchone()[0]
        dataset_count = con.execute("SELECT count(*) FROM dataset_version").fetchone()[0]
        source_count = con.execute("SELECT count(*) FROM source_file").fetchone()[0]

    print(f"Created DuckDB database: {output_path}")
    print(f"Loaded source files: {source_count}")
    print(f"Loaded dataset versions: {dataset_count}")
    print(f"Loaded dataset items: {item_count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a derived DuckDB analysis database from JSONL datasets."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory containing JSONL dataset files. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"DuckDB output path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rebuild the database if it already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_database(
        data_dir=args.data_dir.resolve(),
        output_path=args.output.resolve(),
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
