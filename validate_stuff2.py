import json
from collections import Counter
from pathlib import Path

path = Path("data/moral_reasoning_integrity_behaviour_justifiable_corrigibility_release_governance_pilot_v1.jsonl")

rows = []
with path.open("r", encoding="utf-8") as f:
    for line_no, line in enumerate(f, start=1):
        row = json.loads(line)
        rows.append(row)

print(f"Valid JSONL records: {len(rows)}")
print("By evidence_quality:", dict(Counter(r["evidence_quality"] for r in rows)))
print("By pressure_type:", dict(Counter(r["pressure_type"] for r in rows)))
print("By target_release_scope:", dict(Counter(r["target_release_scope"] for r in rows)))