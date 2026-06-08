
from pathlib import Path
import sys

root = Path(".").resolve()
patterns = [
    "data/*.jsonl",
    "tmp/*export*.csv",
    "docs/failure_audits/*.csv",
    "docs/run_exports/**/*.csv",
    "artefacts/run_exports/**/*.csv",
    "results/**/*.csv",
    "docs/reports/*.md",
    "docs/releases/*.md",
    "results/**/*.md",
    "results/**/*.sql",
    "results/**/*.eval",
]

for pat in patterns:
    for path in sorted(root.glob(pat)):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        try:
            size = path.stat().st_size
            print(f"{size:>12}  {rel}")
            with path.open("rb") as f:
                while f.read(1024 * 1024):
                    pass
        except Exception as e:
            print(f"FAILED: {rel}: {type(e).__name__}: {e}", file=sys.stderr)
            raise
