"""CLI skeleton for generating Phase 3 dataset candidates.

Examples:
    # Dry-run style: print selected matrix cells only
    python scripts/generate_phase3_dataset_candidates.py --list-cells --limit-cells 10

    # Real generation using OpenAI structured parsing
    python scripts/generate_phase3_dataset_candidates.py \
        --provider openai-parse \
        --generator-model gpt-4o-2024-08-06 \
        --judge-model gpt-4o-2024-08-06 \
        --limit-cells 8 \
        --n-per-cell 1 \
        --out data/generated/phase3_candidates_v1

    # Bounded quota generation from a targeted cell file
    python scripts/generate_phase3_dataset_candidates.py \
        --provider openai-parse \
        --cells-json data/generation_cells/phase3_core_overapproval_cells.jsonl \
        --quota-mode \
        --target-kept 12 \
        --max-batches 4 \
        --out data/generated/phase3_core_overapproval_v1

    # OpenAI-compatible endpoint such as OpenRouter
    python scripts/generate_phase3_dataset_candidates.py \
        --provider openai-compatible-json \
        --base-url https://openrouter.ai/api/v1 \
        --generator-model <model-id> \
        --judge-model <model-id> \
        --limit-cells 8
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_sycophancy_eval.dataset_generation.generate_candidates import build_matrix_cells
from moral_sycophancy_eval.dataset_generation.llm_clients import OpenAICompatibleJSONClient, OpenAIParseClient
from moral_sycophancy_eval.dataset_generation.pipeline import generate_score_filter_export
from moral_sycophancy_eval.dataset_generation.quota_generation import generate_until_quota
from moral_sycophancy_eval.dataset_generation.schemas import MatrixCell


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Phase 3 JMCU dataset candidates.")
    parser.add_argument("--provider", choices=["openai-parse", "openai-compatible-json"], default="openai-parse")
    parser.add_argument("--base-url", default=None, help="Base URL for OpenAI-compatible endpoints.")
    parser.add_argument("--generator-model", default="gpt-4o-2024-08-06")
    parser.add_argument("--judge-model", default="gpt-4o-2024-08-06")
    parser.add_argument("--out", default="data/generated/phase3_candidates_v1")
    parser.add_argument("--n-per-cell", type=int, default=1)
    parser.add_argument(
        "--limit-cells",
        type=int,
        default=None,
        help=(
            "Optional safety limit. If omitted with --cells-json, all listed cells are used. "
            "If omitted without --cells-json, defaults to 8 to avoid accidental full-matrix generation."
        ),
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--list-cells", action="store_true", help="Only print selected cells; do not call models.")
    parser.add_argument(
        "--cells-json",
        default=None,
        help="Optional path to a JSON/JSONL list of MatrixCell objects. Explicit order is preserved unless --limit-cells is used.",
    )
    parser.add_argument("--min-mean-quality", type=float, default=8.0)
    parser.add_argument("--max-duplicate-risk", type=int, default=4)
    parser.add_argument("--near-duplicate-threshold", type=float, default=0.86)
    parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        help="Allow candidates with deterministic calibration/scope validation errors to pass filtering. Use only for debugging.",
    )
    parser.add_argument("--quota-mode", action="store_true", help="Generate bounded batches until --target-kept candidates survive filtering.")
    parser.add_argument("--target-kept", type=int, default=12, help="Quota-mode target retained candidate count.")
    parser.add_argument("--max-batches", type=int, default=4, help="Quota-mode hard cap on generate/score/filter batches.")
    return parser.parse_args()


def _load_cell_payload(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON list in {path}")
        return data
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_cells(path: str | None, limit: int | None, seed: int) -> list[MatrixCell]:
    """Load candidate-generation matrix cells.

    Explicit cells-json order is preserved. The default full matrix is shuffled before
    applying the limit so small smoke tests sample across domains/evidence/pressure
    rather than taking the first block of the Cartesian product.
    """
    if path:
        data = _load_cell_payload(Path(path))
        cells = [MatrixCell.model_validate(obj) for obj in data]
    else:
        cells = build_matrix_cells()
        rng = random.Random(seed)
        rng.shuffle(cells)
        if limit is None:
            limit = 8

    if limit is not None:
        return cells[:limit]
    return cells


def main() -> None:
    args = parse_args()
    cells = load_cells(args.cells_json, args.limit_cells, args.seed)

    if args.list_cells:
        for cell in cells:
            print(cell.model_dump_json())
        return

    if args.provider == "openai-parse":
        generator_llm = OpenAIParseClient()
        judge_llm = OpenAIParseClient()
    else:
        generator_llm = OpenAICompatibleJSONClient(base_url=args.base_url)
        judge_llm = OpenAICompatibleJSONClient(base_url=args.base_url)

    if args.quota_mode:
        result = generate_until_quota(
            generator_llm=generator_llm,
            judge_llm=judge_llm,
            generator_model=args.generator_model,
            judge_model=args.judge_model,
            cells=cells,
            output_dir=args.out,
            target_kept=args.target_kept,
            max_batches=args.max_batches,
            batch_n_per_cell=args.n_per_cell,
            generation_workers=args.max_workers,
            judge_workers=args.max_workers,
            seed=args.seed,
            min_mean_quality=args.min_mean_quality,
            max_duplicate_risk=args.max_duplicate_risk,
            near_duplicate_threshold=args.near_duplicate_threshold,
            allow_validation_errors=args.allow_validation_errors,
        )
        print(json.dumps(result.summary, indent=2, ensure_ascii=False))
        return

    summary = generate_score_filter_export(
        generator_llm=generator_llm,
        judge_llm=judge_llm,
        generator_model=args.generator_model,
        judge_model=args.judge_model,
        cells=cells,
        output_dir=args.out,
        n_per_cell=args.n_per_cell,
        generation_workers=args.max_workers,
        judge_workers=args.max_workers,
        seed=args.seed,
        min_mean_quality=args.min_mean_quality,
        max_duplicate_risk=args.max_duplicate_risk,
        near_duplicate_threshold=args.near_duplicate_threshold,
        allow_validation_errors=args.allow_validation_errors,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
