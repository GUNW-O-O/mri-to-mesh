"""regions-meta.json 조립 (스펙 §2.2).

mesh 엔진은 라벨별 regions 배열만 낸다. 여기서 헤더(space·source·labelTable)를
붙여 소비 측(brain-educate) 계약을 완성한다. space.toMNI152는 지금 넣지 않는다
— brain-educate 뇌간 임시해가 있고, SimpleITK MNI152 등록은 별도 작업이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np

from seg_and_mesh.labels import CANONICAL_VERSION


def build_regions_meta(seg_path, regions, variant_id, engine, seg_file) -> dict:
    """스펙 §2.2 regions-meta.json 전체."""
    img = nib.load(Path(seg_path))
    affine = np.asarray(img.affine, dtype=float)
    zooms = [float(z) for z in img.header.get_zooms()[:3]]
    return {
        "version": 2,
        "labelTable": CANONICAL_VERSION,
        "space": {
            "affine": affine.tolist(),
            "shape": [int(s) for s in img.shape[:3]],
            "voxelSize": zooms,
        },
        "source": {
            "engine": engine.get("name"),
            "engineVersion": engine.get("version"),
            "segFile": seg_file,
        },
        "meshConfig": None,  # 탐색 단계 — 정본 확정 전(todo §6.6)
        "meshVariantId": variant_id,
        "regions": regions,
    }


def write_regions_meta(out_path, meta: dict) -> None:
    Path(out_path).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
