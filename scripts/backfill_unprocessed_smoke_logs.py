#!/usr/bin/env python
"""
Backfill unprocessed smoke / multistage Inspect .eval logs one at a time.

Dry-run by default. With --apply, calls:

    scripts/backfill_inspect_logs_to_postgres.py
      --write
      --promote-operational
      --diagnostics
      --eval-log <path>

This does not run model evals or external APIs. It only processes existing .eval logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_INCLUDE_REGEX = r"v5|multistage|multi-stage|smoke"
DEFAULT_CONTENT_REGEX = r"v5_multistage_pressure_pilot_v0|mri-behaviour-v5|multistage_pressure"


@dataclass(frozen=True)
class CandidateLog:
    path: str
    name: str
    match_reason: str
    size_bytes: int
    modified_at: str
    sha256: str
    already_backfilled: bool
    already_backfilled_reason: str | None


@dataclass
class BackfillResult:
    path: str
    name: str
    sha256: str
    status: str
    started_at: str
    completed_at: str
    returncode: int | None
    stdout: str
    stderr: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def psql_query(query: str) -> str:
    container = require_env("PGCONTAINER")
    user = require_env("PGUSER")
    database = require_env("PGDATABASE")

    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        user,
        "-d",
        database,
        "-At",
        "-c",
        query,
    ]
    result = run_command(cmd)
    if result.returncode != 0:
        raise SystemExit(
            "Postgres query failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    return result.stdout


def normalise_pathish(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


def load_existing_backfill_identities() -> tuple[set[str], set[str], set[str]]:
    hash_query = """
select lower(eval_log_sha256)
from public.experiment_pipeline_run
where eval_log_sha256 is not null
union
select lower(source_log_sha256)
from public.inspect_log_sample
where source_log_sha256 is not null;
"""
    path_query = """
select eval_log_path
from public.experiment_pipeline_run
where eval_log_path is not null
union
select original_eval_log_path
from public.experiment_pipeline_run
where original_eval_log_path is not null
union
select source_log_path
from public.inspect_log_sample
where source_log_path is not null;
"""

    existing_hashes = {
        line.strip().lower()
        for line in psql_query(hash_query).splitlines()
        if line.strip()
    }

    existing_paths = {
        normalise_pathish(line)
        for line in psql_query(path_query).splitlines()
        if line.strip()
    }

    existing_names = {
        Path(path).name.lower()
        for path in existing_paths
        if path
    }

    return existing_hashes, existing_paths, existing_names


def iter_eval_logs(scan_dirs: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.eval"):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def file_content_matches(path: Path, content_re: re.Pattern[str] | None) -> bool:
    if content_re is None:
        return False
    if path.suffix.lower() != ".eval":
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(content_re.search(text))


def discover_candidates(
    *,
    scan_dirs: list[Path],
    include_regex: str,
    content_regex: str | None,
    exclude_regex: str | None,
) -> list[tuple[Path, str]]:
    include_re = re.compile(include_regex, re.IGNORECASE)
    content_re = re.compile(content_regex, re.IGNORECASE) if content_regex else None
    exclude_re = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None

    candidates: list[tuple[Path, str]] = []
    for path in iter_eval_logs(scan_dirs):
        path_text = path.as_posix()
        path_match = bool(include_re.search(path.name) or include_re.search(path_text))
        content_match = file_content_matches(path, content_re)
        if not path_match and not content_match:
            continue
        if exclude_re and (exclude_re.search(path.name) or exclude_re.search(path_text)):
            continue
        if path_match and content_match:
            match_reason = "both"
        elif path_match:
            match_reason = "filename/path"
        else:
            match_reason = "content"
        candidates.append((path, match_reason))

    return sorted(candidates, key=lambda item: (item[0].stat().st_mtime, item[0].name))


def classify_candidates(
    paths: list[tuple[Path, str]],
    *,
    repo_root: Path,
    existing_hashes: set[str],
    existing_paths: set[str],
    existing_names: set[str],
) -> list[CandidateLog]:
    records: list[CandidateLog] = []

    for path, match_reason in paths:
        digest = sha256_file(path)

        try:
            rel = path.relative_to(repo_root)
            path_keys = {
                normalise_pathish(str(path)),
                normalise_pathish(str(rel)),
                normalise_pathish(rel.as_posix()),
            }
        except ValueError:
            path_keys = {
                normalise_pathish(str(path)),
            }

        name_key = path.name.lower()

        already = False
        reason = None
        if digest in existing_hashes:
            already = True
            reason = "sha256"
        elif path_keys & existing_paths:
            already = True
            reason = "path"
        elif name_key in existing_names:
            already = True
            reason = "filename"

        stat = path.stat()
        records.append(
            CandidateLog(
                path=str(path),
                name=path.name,
                match_reason=match_reason,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                sha256=digest,
                already_backfilled=already,
                already_backfilled_reason=reason,
            )
        )

    return records


def make_pythonpath(repo_root: Path) -> str:
    existing = os.environ.get("PYTHONPATH")
    parts = [str(repo_root), str(repo_root / "src")]
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def run_backfill_for_log(
    *,
    repo_root: Path,
    log: CandidateLog,
) -> BackfillResult:
    backfill_script = repo_root / "scripts" / "backfill_inspect_logs_to_postgres.py"
    if not backfill_script.exists():
        raise FileNotFoundError(f"Missing backfill script: {backfill_script}")

    env = os.environ.copy()
    env["PYTHONPATH"] = make_pythonpath(repo_root)

    cmd = [
        sys.executable,
        str(backfill_script),
        "--write",
        "--promote-operational",
        "--diagnostics",
        "--eval-log",
        log.path,
    ]

    started = utc_now_iso()
    result = run_command(cmd, cwd=repo_root, env=env)
    completed = utc_now_iso()

    status = "ok" if result.returncode == 0 else "error"

    return BackfillResult(
        path=log.path,
        name=log.name,
        sha256=log.sha256,
        status=status,
        started_at=started,
        completed_at=completed,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def print_candidate_table(records: list[CandidateLog]) -> None:
    if not records:
        print("No matching .eval files found.")
        return

    print()
    print(f"{'DONE':<6} {'BACKFILL':<10} {'MATCH':<14} {'SIZE':>10}  {'NAME'}")
    print("-" * 118)
    for rec in records:
        done = "yes" if rec.already_backfilled else "no"
        reason = rec.already_backfilled_reason or ""
        print(f"{done:<6} {reason:<10} {rec.match_reason:<14} {rec.size_bytes:>10}  {rec.name}")


def write_report(
    *,
    report_dir: Path,
    candidates: list[CandidateLog],
    results: list[BackfillResult],
    args: argparse.Namespace,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"backfill_unprocessed_smoke_logs_{timestamp}.json"

    payload = {
        "created_at": utc_now_iso(),
        "args": json_safe(vars(args)),
        "candidate_count": len(candidates),
        "pending_count": len([c for c in candidates if not c.already_backfilled]),
        "result_count": len(results),
        "candidates": [asdict(c) for c in candidates],
        "results": [asdict(r) for r in results],
    }

    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report_path


def print_current_pipeline_counts() -> None:
    query = """
select
  epr.experiment_pipeline_run_id,
  epr.experiment_slug,
  epr.dataset_version,
  epr.answer_model,
  epr.answer_models,
  count(*) as samples,
  count(ils.response_id) filter (where ils.response_id is not null) as linked_samples
from public.experiment_pipeline_run epr
join public.inspect_log_sample ils
  on ils.experiment_pipeline_run_id = epr.experiment_pipeline_run_id
group by
  epr.experiment_pipeline_run_id,
  epr.experiment_slug,
  epr.dataset_version,
  epr.answer_model,
  epr.answer_models
order by epr.experiment_pipeline_run_id desc;
"""
    container = require_env("PGCONTAINER")
    user = require_env("PGUSER")
    database = require_env("PGDATABASE")

    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        user,
        "-d",
        database,
        "-c",
        query,
    ]
    result = run_command(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill unprocessed smoke / multistage Inspect .eval logs."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root_from_script(),
        help="Repository root. Defaults to parent of this script directory.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs"),
        help="Directory containing .eval logs.",
    )
    parser.add_argument(
        "--include-tmp",
        action="store_true",
        help="Also scan tmp/ for .eval logs.",
    )
    parser.add_argument(
        "--include-regex",
        default=DEFAULT_INCLUDE_REGEX,
        help="Regex used to include candidate .eval paths.",
    )
    parser.add_argument(
        "--content-regex",
        default=DEFAULT_CONTENT_REGEX,
        help="Regex used to include candidate .eval file content.",
    )
    parser.add_argument(
        "--exclude-regex",
        default=None,
        help="Optional regex used to exclude candidate .eval paths.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually backfill pending logs. Default is dry-run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pending logs to backfill.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue after a failed backfill instead of stopping.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("tmp"),
        help="Directory for JSON report output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()

    scan_dirs = [(repo_root / args.logs_dir).resolve()]
    if args.include_tmp:
        scan_dirs.append((repo_root / "tmp").resolve())

    print("Repo root:", repo_root)
    print("Scan dirs:")
    for scan_dir in scan_dirs:
        print(" ", scan_dir)
    print("Include regex:", args.include_regex)
    print("Content regex:", args.content_regex)
    if args.exclude_regex:
        print("Exclude regex:", args.exclude_regex)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")

    existing_hashes, existing_paths, existing_names = load_existing_backfill_identities()
    print(f"Existing SHA identities: {len(existing_hashes)}")
    print(f"Existing path identities: {len(existing_paths)}")

    paths = discover_candidates(
        scan_dirs=scan_dirs,
        include_regex=args.include_regex,
        content_regex=args.content_regex,
        exclude_regex=args.exclude_regex,
    )
    candidates = classify_candidates(
        paths,
        repo_root=repo_root,
        existing_hashes=existing_hashes,
        existing_paths=existing_paths,
        existing_names=existing_names,
    )

    print_candidate_table(candidates)

    pending = [candidate for candidate in candidates if not candidate.already_backfilled]
    if args.limit is not None:
        pending = pending[: args.limit]

    print()
    print(f"Candidates: {len(candidates)}")
    print(f"Pending:    {len(pending)}")

    results: list[BackfillResult] = []

    if not pending:
        report_path = write_report(
            report_dir=(repo_root / args.report_dir).resolve(),
            candidates=candidates,
            results=results,
            args=args,
        )
        print("Nothing to backfill.")
        print("Report:", report_path)
        return 0

    if not args.apply:
        report_path = write_report(
            report_dir=(repo_root / args.report_dir).resolve(),
            candidates=candidates,
            results=results,
            args=args,
        )
        print()
        print("Dry run only. Rerun with --apply to backfill pending logs.")
        print("Report:", report_path)
        return 0

    for log in pending:
        print()
        print("=" * 100)
        print(f"Backfilling: {log.name}")
        print(f"Path:        {log.path}")
        print(f"SHA256:      {log.sha256}")

        result = run_backfill_for_log(repo_root=repo_root, log=log)
        results.append(result)

        if result.stdout:
            print("STDOUT:")
            print(result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr, file=sys.stderr)

        print(f"Status: {result.status}")

        if result.status != "ok" and not args.continue_on_error:
            print("Stopping after first failed backfill.")
            break

    report_path = write_report(
        report_dir=(repo_root / args.report_dir).resolve(),
        candidates=candidates,
        results=results,
        args=args,
    )

    print()
    print("Report:", report_path)
    print()
    print("Current pipeline run counts:")
    print_current_pipeline_counts()

    return 0 if all(r.status == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
