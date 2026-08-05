"""조건 프리셋 영역명이 canonical-v1 라벨표에 실재하는지 검증."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PRESETS = _ROOT / "mri2mesh" / "web" / "static" / "condition-presets.json"
_TSV = _ROOT / "labels" / "canonical-v1.tsv"


def _canonical_names() -> set[str]:
    with _TSV.open(encoding="utf-8") as f:
        return {row["name"] for row in csv.DictReader(f, delimiter="\t")}


def test_all_preset_regions_exist_in_canonical():
    presets = json.loads(_PRESETS.read_text(encoding="utf-8"))
    names = _canonical_names()
    for condition, regions in presets.items():
        missing = [r for r in regions if r not in names]
        assert not missing, f"{condition}: canonical-v1에 없는 영역 {missing}"


def test_presets_has_three_conditions_nonempty():
    presets = json.loads(_PRESETS.read_text(encoding="utf-8"))
    assert set(presets) == {"노화", "치매", "알콜"}
    assert all(len(v) > 0 for v in presets.values())
