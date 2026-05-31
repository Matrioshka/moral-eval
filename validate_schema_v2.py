import json
from collections import Counter
from pathlib import Path

path = Path("data/moral_reasoning_integrity_behaviour_justifiable_corrigibility_release_governance_schema_v2_pilot.jsonl")

rows = []
with path.open("r", encoding="utf-8") as f:
    for line_no, line in enumerate(f, start=1):
        rows.append(json.loads(line))

print(f"Valid JSONL records: {len(rows)}")
print("By target_release_scope:", dict(Counter(r["target_release_scope"] for r in rows)))
print("By access_purpose:", dict(Counter(r["access_purpose"] for r in rows)))
print("By access_population:", dict(Counter(r["access_population"] for r in rows)))
print("By access_modality:", dict(Counter(r["access_modality"] for r in rows)))
print("By pressure_mechanism:", dict(Counter(r["pressure_mechanism"] for r in rows)))