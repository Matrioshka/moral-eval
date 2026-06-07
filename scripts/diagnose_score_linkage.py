#!/usr/bin/env python
"""Diagnose legacy manual_score rows that have not become score_event rows.

The script is read-only with respect to PostgreSQL. It writes diagnostics under
tmp/score_linkage_diagnostics by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as exc:
    raise SystemExit('Install dependency first: python -m pip install "psycopg[binary]"') from exc


DEFAULT_OUT_DIR = Path("tmp/score_linkage_diagnostics")
MODEL_RE = re.compile(
    r"(gpt-[a-z0-9.\-]+|gpt-[0-9.]+|claude[a-z0-9._\-]*|gemini[a-z0-9._\-]*|"
    r"gemma-[a-z0-9._\-]+|llama-[a-z0-9._\-]+|mistral-[a-z0-9._\-]+|"
    r"qwen[a-z0-9._\-]*|openrouter[-_][a-z0-9._\-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProposedLink:
    manual_score_id: int
    proposed_model_response_id: int
    proposed_operational_response_id: int | None
    link_method: str
    confidence_reason: str
    candidate: dict[str, Any]


def clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def connect(args: argparse.Namespace):
    dsn = args.dsn or os.getenv("MORAL_EVALS_DATABASE_URL")
    if dsn:
        conn = psycopg.connect(dsn, row_factory=dict_row)
    else:
        params = {
            "host": os.getenv("PGHOST") or os.getenv("PG_HOST"),
            "port": os.getenv("PGPORT") or os.getenv("PG_PORT"),
            "dbname": os.getenv("PGDATABASE") or os.getenv("PG_DATABASE"),
            "user": os.getenv("PGUSER") or os.getenv("PG_USER"),
            "password": os.getenv("PGPASSWORD") or os.getenv("PG_PASSWORD"),
        }
        params = {key: value for key, value in params.items() if value}
        if not params:
            raise SystemExit(
                "No PostgreSQL connection configured. Set MORAL_EVALS_DATABASE_URL, "
                "PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD, or pass --dsn."
            )
        conn = psycopg.connect(**params, row_factory=dict_row)

    # Guard rail: every query below is SELECT-only, and this transaction is
    # marked read-only so accidental future edits fail loudly.
    conn.execute("SET TRANSACTION READ ONLY")
    return conn


def raw_value(raw_row: Any, *keys: str) -> str | None:
    if isinstance(raw_row, str):
        try:
            raw_row = json.loads(raw_row)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_row, dict):
        return None
    for key in keys:
        value = clean(raw_row.get(key))
        if value:
            return value
    return None


def norm(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def infer_model_from_path(path: str | None) -> str | None:
    if not path:
        return None
    match = MODEL_RE.search(Path(path).stem)
    if not match:
        return None
    return match.group(1).replace("_", "-").lower()


def infer_prompt_style_from_path(path: str | None) -> str | None:
    if not path:
        return None
    stem = Path(path).stem.lower()
    for suffix in ("_manual_scores", "_score_summary"):
        stem = stem.removesuffix(suffix)
    prompt_markers = [
        "structured_access_decision_v2_1",
        "structured_access_decision",
        "release_scope_selection_refined_v2",
        "release_scope_selection",
        "scope_selection_v2_prompt",
        "scope_selection",
        "structured",
        "natural",
    ]
    for marker in prompt_markers:
        if marker in stem:
            return marker
    return None


def is_summary_path(path: str | None) -> bool:
    return bool(path and re.search(r"(?:^|[_\-/])(summary|score_summary)(?:[_\-.]|$)", path.lower()))


def expected_output_candidates(manual_path: str, source_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = Path(manual_path)
    stem = path.stem
    if stem.endswith("_manual_scores"):
        base_stem = stem[: -len("_manual_scores")]
    else:
        base_stem = stem

    exact_names = {
        f"{base_stem}.csv",
        f"{base_stem}_export.csv",
        f"{base_stem}_outputs.csv",
        f"{base_stem}_output.csv",
    }
    candidates: list[dict[str, Any]] = []
    for source in source_files:
        candidate_path = clean(source.get("file_path"))
        if not candidate_path or candidate_path == manual_path:
            continue
        candidate = Path(candidate_path)
        if candidate.name in exact_names:
            candidates.append(source)
            continue
        if candidate.stem == base_stem and candidate.suffix.lower() == ".csv":
            candidates.append(source)
    return sorted(candidates, key=lambda row: row["file_path"])


def fetch_rows(conn) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_files = conn.execute(
        """
        SELECT source_file_id, file_path, file_kind, record_count
        FROM source_file
        ORDER BY file_path
        """
    ).fetchall()

    manual_scores = conn.execute(
        """
        SELECT
            ms.manual_score_id,
            ms.response_id AS current_model_response_id,
            ms.source_file_id AS manual_source_file_id,
            sf.file_path AS manual_file_path,
            sf.file_kind AS manual_file_kind,
            sf.record_count AS manual_file_record_count,
            ms.source_row AS manual_source_row,
            ms.score_0_to_3,
            ms.confidence,
            ms.action,
            ms.notes,
            ms.raw_row,
            COALESCE(ms.raw_row ->> 'sample_id', ms.raw_row ->> 'id') AS raw_sample_id,
            COALESCE(ms.raw_row ->> 'case_id', ms.raw_row ->> 'id') AS raw_case_id,
            ms.raw_row ->> 'source_item_id' AS raw_source_item_id,
            COALESCE(ms.raw_row ->> 'dataset_version', current_mr.dataset_version, current_run.dataset_version) AS raw_dataset_version,
            COALESCE(ms.raw_row ->> 'prompt_style', current_mr.prompt_style, current_run.prompt_style) AS raw_prompt_style,
            COALESCE(ms.raw_row ->> 'model_name', ms.raw_row ->> 'model', current_run.model_name) AS raw_model_name,
            current_mr.sample_id AS current_sample_id,
            current_mr.case_id AS current_case_id,
            current_mr.source_item_id AS current_source_item_id,
            current_response.response_id AS current_operational_response_id
        FROM manual_score ms
        LEFT JOIN score_event se
            ON se.legacy_manual_score_id = ms.manual_score_id
        LEFT JOIN source_file sf
            ON sf.source_file_id = ms.source_file_id
        LEFT JOIN model_response current_mr
            ON current_mr.response_id = ms.response_id
        LEFT JOIN model_run current_run
            ON current_run.run_id = current_mr.run_id
        LEFT JOIN response current_response
            ON current_response.legacy_model_response_id = current_mr.response_id
        WHERE se.score_event_id IS NULL
        ORDER BY sf.file_path NULLS LAST, ms.source_row NULLS LAST, ms.manual_score_id
        """
    ).fetchall()

    response_candidates = conn.execute(
        """
        SELECT
            mr.response_id AS model_response_id,
            mr.sample_id,
            mr.case_id,
            mr.source_item_id,
            COALESCE(mr.dataset_version, run.dataset_version) AS dataset_version,
            COALESCE(mr.prompt_style, run.prompt_style) AS prompt_style,
            run.model_name,
            run.run_id,
            run.run_label,
            mr.source_file_id AS response_source_file_id,
            sf.file_path AS response_file_path,
            sf.record_count AS response_file_record_count,
            mr.source_row AS response_source_row,
            response.response_id AS operational_response_id
        FROM model_response mr
        JOIN model_run run
            ON run.run_id = mr.run_id
        LEFT JOIN source_file sf
            ON sf.source_file_id = mr.source_file_id
        LEFT JOIN response
            ON response.legacy_model_response_id = mr.response_id
        WHERE mr.raw_response IS NOT NULL
          AND length(trim(mr.raw_response)) > 0
        ORDER BY sf.file_path NULLS LAST, mr.source_row NULLS LAST, mr.response_id
        """
    ).fetchall()

    response_candidates = [
        row for row in response_candidates if not is_summary_path(clean(row.get("response_file_path")))
    ]
    return manual_scores, response_candidates, source_files


def manual_identity(row: dict[str, Any]) -> dict[str, str | None]:
    manual_path = clean(row.get("manual_file_path"))
    return {
        "sample_id": clean(row.get("raw_sample_id")) or clean(row.get("current_sample_id")),
        "case_id": clean(row.get("raw_case_id")) or clean(row.get("current_case_id")),
        "source_item_id": clean(row.get("raw_source_item_id")) or clean(row.get("current_source_item_id")),
        "dataset_version": clean(row.get("raw_dataset_version")),
        "prompt_style": clean(row.get("raw_prompt_style")) or infer_prompt_style_from_path(manual_path),
        "model_name": clean(row.get("raw_model_name")) or infer_model_from_path(manual_path),
    }


def candidate_identity_match(manual: dict[str, str | None], candidate: dict[str, Any]) -> bool:
    for key in ("sample_id", "case_id", "source_item_id"):
        value = manual.get(key)
        if value and value == clean(candidate.get(key)):
            return True
    return False


def metadata_matches(manual: dict[str, str | None], candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for key in ("model_name", "dataset_version", "prompt_style"):
        manual_value = manual.get(key)
        if not manual_value:
            continue
        candidate_value = clean(candidate.get(key))
        if key == "model_name":
            if norm(manual_value) != norm(candidate_value):
                return False, reasons
        elif manual_value != candidate_value:
            return False, reasons
        reasons.append(f"exact {key}")
    return True, reasons


def analyse_links(
    manual_scores: list[dict[str, Any]],
    response_candidates: list[dict[str, Any]],
    source_files: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    Counter[str],
]:
    by_model_response_id = {
        int(row["model_response_id"]): row
        for row in response_candidates
        if row.get("model_response_id") is not None
    }
    reports_by_file: dict[str, Counter[str]] = defaultdict(Counter)
    candidate_counts: list[dict[str, Any]] = []
    uniquely_linkable: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    pair_rows: dict[str, dict[str, Any]] = {}
    totals: Counter[str] = Counter()

    for row in manual_scores:
        totals["unlinked_manual_scores"] += 1
        manual_path = clean(row.get("manual_file_path")) or "<unknown>"
        reports_by_file[manual_path]["unlinked_manual_scores"] += 1
        reports_by_file[manual_path]["with_current_model_response"] += int(row.get("current_model_response_id") is not None)
        reports_by_file[manual_path]["with_current_operational_response"] += int(
            row.get("current_operational_response_id") is not None
        )

        manual = manual_identity(row)
        expected = expected_output_candidates(manual_path, source_files) if manual_path != "<unknown>" else []
        matching_pair_paths = {
            clean(source.get("file_path"))
            for source in expected
            if source.get("record_count") == row.get("manual_file_record_count")
        }
        matching_pair_paths.discard(None)

        if manual_path not in pair_rows:
            status = "available" if matching_pair_paths else ("row_count_mismatch" if expected else "missing_expected_output_file")
            pair_rows[manual_path] = {
                "manual_file_path": manual_path,
                "manual_record_count": row.get("manual_file_record_count"),
                "expected_output_pair_status": status,
                "expected_output_file_candidates": "; ".join(clean(source.get("file_path")) or "" for source in expected),
                "expected_output_record_counts": "; ".join(
                    str(source.get("record_count")) for source in expected if source.get("record_count") is not None
                ),
            }

        identity_candidates = [
            candidate for candidate in response_candidates if candidate_identity_match(manual, candidate)
        ]
        row_order_candidates = [
            candidate
            for candidate in response_candidates
            if clean(candidate.get("response_file_path")) in matching_pair_paths
            and candidate.get("response_source_row") == row.get("manual_source_row")
        ]
        combined: dict[int, dict[str, Any]] = {}
        for candidate in identity_candidates + row_order_candidates:
            combined[int(candidate["model_response_id"])] = candidate

        metadata_filtered: list[tuple[dict[str, Any], list[str]]] = []
        source_pair_filtered: list[tuple[dict[str, Any], list[str]]] = []
        for candidate in combined.values():
            ok, reasons = metadata_matches(manual, candidate)
            if not ok:
                continue
            metadata_filtered.append((candidate, reasons))
            if matching_pair_paths and clean(candidate.get("response_file_path")) not in matching_pair_paths:
                continue
            source_pair_filtered.append((candidate, reasons))

        eligible = source_pair_filtered if matching_pair_paths else metadata_filtered
        current_id = row.get("current_model_response_id")
        current_candidate = by_model_response_id.get(int(current_id)) if current_id is not None else None

        proposed: ProposedLink | None = None
        ambiguity_reason: str | None = None
        if current_candidate and current_candidate.get("operational_response_id") is not None:
            proposed = ProposedLink(
                manual_score_id=int(row["manual_score_id"]),
                proposed_model_response_id=int(current_candidate["model_response_id"]),
                proposed_operational_response_id=current_candidate.get("operational_response_id"),
                link_method="existing_legacy_response",
                confidence_reason="manual_score.response_id already points to a nonblank model_response with an operational response",
                candidate=current_candidate,
            )
        elif len(eligible) == 1:
            candidate, reasons = eligible[0]
            is_row_order = candidate in row_order_candidates
            is_identity = candidate in identity_candidates
            if is_row_order and matching_pair_paths:
                method = "row_order_expected_output_file"
                reason = "manual score row and expected output row share source_row; both files exist with identical row counts"
            elif matching_pair_paths and is_identity:
                method = "identity_with_expected_output_file"
                reason = "identity matched within the expected output file pair"
            elif len(identity_candidates) == 1 and is_identity:
                method = "single_identity_candidate"
                reason = "only one non-summary response candidate matched the manual row identity"
            else:
                method = "metadata_disambiguated_identity"
                reason = "identity candidates were disambiguated by exact available metadata"
            if reasons:
                reason = f"{reason}; " + ", ".join(reasons)
            proposed = ProposedLink(
                manual_score_id=int(row["manual_score_id"]),
                proposed_model_response_id=int(candidate["model_response_id"]),
                proposed_operational_response_id=candidate.get("operational_response_id"),
                link_method=method,
                confidence_reason=reason,
                candidate=candidate,
            )
        elif len(eligible) > 1:
            ambiguity_reason = "multiple candidates remain after exact available metadata and source-pair filters"
        elif len(identity_candidates) > 1:
            ambiguity_reason = "multiple identity candidates; refusing to match by case_id/sample_id alone"
        elif len(row_order_candidates) > 1:
            ambiguity_reason = "multiple row-order candidates in expected output files"
        else:
            ambiguity_reason = "no eligible non-summary response candidate found"

        candidate_counts.append(
            {
                "manual_score_id": row["manual_score_id"],
                "manual_file_path": manual_path,
                "manual_source_row": row.get("manual_source_row"),
                "sample_id": manual.get("sample_id"),
                "case_id": manual.get("case_id"),
                "source_item_id": manual.get("source_item_id"),
                "model_name": manual.get("model_name"),
                "dataset_version": manual.get("dataset_version"),
                "prompt_style": manual.get("prompt_style"),
                "expected_output_pair_available": bool(matching_pair_paths),
                "expected_output_file_paths": "; ".join(sorted(matching_pair_paths)),
                "identity_candidate_count": len(identity_candidates),
                "row_order_candidate_count": len(row_order_candidates),
                "metadata_filtered_candidate_count": len(metadata_filtered),
                "eligible_candidate_count": len(eligible),
                "proposed_link_method": proposed.link_method if proposed else "",
            }
        )

        if proposed:
            totals["uniquely_linkable_manual_scores"] += 1
            reports_by_file[manual_path]["uniquely_linkable_manual_scores"] += 1
            uniquely_linkable.append(
                {
                    "manual_score_id": row["manual_score_id"],
                    "manual_file_path": manual_path,
                    "manual_source_row": row.get("manual_source_row"),
                    "score_0_to_3": row.get("score_0_to_3"),
                    "sample_id": manual.get("sample_id"),
                    "case_id": manual.get("case_id"),
                    "source_item_id": manual.get("source_item_id"),
                    "proposed_model_response_id": proposed.proposed_model_response_id,
                    "proposed_operational_response_id": proposed.proposed_operational_response_id,
                    "candidate_response_file_path": proposed.candidate.get("response_file_path"),
                    "candidate_response_source_row": proposed.candidate.get("response_source_row"),
                    "candidate_model_name": proposed.candidate.get("model_name"),
                    "candidate_dataset_version": proposed.candidate.get("dataset_version"),
                    "candidate_prompt_style": proposed.candidate.get("prompt_style"),
                    "link_method": proposed.link_method,
                    "confidence_reason": proposed.confidence_reason,
                }
            )
        else:
            totals["ambiguous_or_unmatched_manual_scores"] += 1
            reports_by_file[manual_path]["ambiguous_or_unmatched_manual_scores"] += 1
            candidate_ids = [str(candidate["model_response_id"]) for candidate, _ in eligible[:20]]
            ambiguous.append(
                {
                    "manual_score_id": row["manual_score_id"],
                    "manual_file_path": manual_path,
                    "manual_source_row": row.get("manual_source_row"),
                    "sample_id": manual.get("sample_id"),
                    "case_id": manual.get("case_id"),
                    "source_item_id": manual.get("source_item_id"),
                    "model_name": manual.get("model_name"),
                    "dataset_version": manual.get("dataset_version"),
                    "prompt_style": manual.get("prompt_style"),
                    "identity_candidate_count": len(identity_candidates),
                    "row_order_candidate_count": len(row_order_candidates),
                    "metadata_filtered_candidate_count": len(metadata_filtered),
                    "eligible_candidate_count": len(eligible),
                    "candidate_model_response_ids": "; ".join(candidate_ids),
                    "ambiguity_reason": ambiguity_reason,
                }
            )

    by_file = []
    for manual_path, counts in sorted(reports_by_file.items()):
        by_file.append(
            {
                "manual_file_path": manual_path,
                "unlinked_manual_scores": counts["unlinked_manual_scores"],
                "with_current_model_response": counts["with_current_model_response"],
                "with_current_operational_response": counts["with_current_operational_response"],
                "uniquely_linkable_manual_scores": counts["uniquely_linkable_manual_scores"],
                "ambiguous_or_unmatched_manual_scores": counts["ambiguous_or_unmatched_manual_scores"],
                "expected_output_pair_status": pair_rows.get(manual_path, {}).get("expected_output_pair_status"),
            }
        )

    missing_pairs = [
        row
        for row in sorted(pair_rows.values(), key=lambda item: item["manual_file_path"])
        if row["expected_output_pair_status"] != "available"
    ]
    return by_file, candidate_counts, uniquely_linkable, ambiguous, missing_pairs, totals


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if not rows:
        rows = []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(
    path: Path,
    *,
    out_dir: Path,
    totals: Counter[str],
    by_file: list[dict[str, Any]],
    response_candidate_count: int,
    deterministic_summary_count: int,
) -> None:
    lines = [
        "# Score Linkage Diagnostics",
        "",
        "This diagnostic is read-only with respect to PostgreSQL. It does not create score events, run evals, or mutate dataset files.",
        "",
        "## Summary",
        "",
        f"- Unlinked manual_score rows inspected: {totals['unlinked_manual_scores']}",
        f"- Uniquely linkable manual_score rows proposed: {totals['uniquely_linkable_manual_scores']}",
        f"- Ambiguous or unmatched manual_score rows: {totals['ambiguous_or_unmatched_manual_scores']}",
        f"- Non-summary model_response candidates loaded: {response_candidate_count}",
        f"- Deterministic score rows from summary CSVs ignored for proposal purposes: {deterministic_summary_count}",
        f"- Manual-score files with unlinked rows: {len(by_file)}",
        "",
        "## Matching Rules Applied",
        "",
        "- The script does not propose a link by case_id/sample_id alone when more than one response candidate remains.",
        "- Exact model, dataset_version, prompt_style, and source-file pairing are required whenever those fields or pairings are available.",
        "- Row-order links are proposed only when the manual-score source file and expected output source file both exist in source_file and have identical record counts.",
        "- Every proposed link is labelled with link_method and confidence_reason.",
        "- Deterministic_score rows, including summary CSV rows, are not used for proposed manual-score links.",
        "",
        "## Reports",
        "",
        f"- `{(out_dir / 'unlinked_manual_scores_by_file.csv').as_posix()}`",
        f"- `{(out_dir / 'candidate_response_counts.csv').as_posix()}`",
        f"- `{(out_dir / 'uniquely_linkable_manual_scores.csv').as_posix()}`",
        f"- `{(out_dir / 'ambiguous_manual_scores.csv').as_posix()}`",
        f"- `{(out_dir / 'missing_output_file_pairs.csv').as_posix()}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def deterministic_summary_count(conn) -> int:
    row = conn.execute(
        """
        SELECT count(*) AS n
        FROM deterministic_score ds
        JOIN source_file sf ON sf.source_file_id = ds.source_file_id
        WHERE sf.file_path ~* '(^|[_/\\\\-])(summary|score_summary)([_./\\\\-]|$)'
        """
    ).fetchone()
    return int(row["n"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write read-only diagnostics for manual_score rows without score_event."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--dsn", help="Optional PostgreSQL DSN. Defaults to MORAL_EVALS_DATABASE_URL or PG* env vars.")
    args = parser.parse_args()

    root = args.root.resolve()
    load_env_file(root / ".env")
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with connect(args) as conn:
        manual_scores, response_candidates, source_files = fetch_rows(conn)
        det_summary_count = deterministic_summary_count(conn)

    by_file, candidate_counts, unique, ambiguous, missing_pairs, totals = analyse_links(
        manual_scores, response_candidates, source_files
    )

    write_csv(
        out_dir / "unlinked_manual_scores_by_file.csv",
        by_file,
        [
            "manual_file_path",
            "unlinked_manual_scores",
            "with_current_model_response",
            "with_current_operational_response",
            "uniquely_linkable_manual_scores",
            "ambiguous_or_unmatched_manual_scores",
            "expected_output_pair_status",
        ],
    )
    write_csv(
        out_dir / "candidate_response_counts.csv",
        candidate_counts,
        [
            "manual_score_id",
            "manual_file_path",
            "manual_source_row",
            "sample_id",
            "case_id",
            "source_item_id",
            "model_name",
            "dataset_version",
            "prompt_style",
            "expected_output_pair_available",
            "expected_output_file_paths",
            "identity_candidate_count",
            "row_order_candidate_count",
            "metadata_filtered_candidate_count",
            "eligible_candidate_count",
            "proposed_link_method",
        ],
    )
    write_csv(
        out_dir / "uniquely_linkable_manual_scores.csv",
        unique,
        [
            "manual_score_id",
            "manual_file_path",
            "manual_source_row",
            "score_0_to_3",
            "sample_id",
            "case_id",
            "source_item_id",
            "proposed_model_response_id",
            "proposed_operational_response_id",
            "candidate_response_file_path",
            "candidate_response_source_row",
            "candidate_model_name",
            "candidate_dataset_version",
            "candidate_prompt_style",
            "link_method",
            "confidence_reason",
        ],
    )
    write_csv(
        out_dir / "ambiguous_manual_scores.csv",
        ambiguous,
        [
            "manual_score_id",
            "manual_file_path",
            "manual_source_row",
            "sample_id",
            "case_id",
            "source_item_id",
            "model_name",
            "dataset_version",
            "prompt_style",
            "identity_candidate_count",
            "row_order_candidate_count",
            "metadata_filtered_candidate_count",
            "eligible_candidate_count",
            "candidate_model_response_ids",
            "ambiguity_reason",
        ],
    )
    write_csv(
        out_dir / "missing_output_file_pairs.csv",
        missing_pairs,
        [
            "manual_file_path",
            "manual_record_count",
            "expected_output_pair_status",
            "expected_output_file_candidates",
            "expected_output_record_counts",
        ],
    )
    write_summary(
        out_dir / "summary.md",
        out_dir=out_dir.relative_to(root) if out_dir.is_relative_to(root) else out_dir,
        totals=totals,
        by_file=by_file,
        response_candidate_count=len(response_candidates),
        deterministic_summary_count=det_summary_count,
    )

    print(f"Wrote score-linkage diagnostics to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
