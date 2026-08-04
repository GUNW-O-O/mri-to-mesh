"""regions-meta.json 조립 (스펙 §2.2).

mesh 엔진은 라벨별 regions 배열만 낸다. 여기서 헤더(space·source·labelTable)를
붙여 소비 측(brain-educate) 계약을 완성한다. space.toMNI152는 지금 넣지 않는다
— brain-educate 뇌간 임시해가 있고, SimpleITK MNI152 등록은 별도 작업이다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import nibabel as nib
import numpy as np

from mri2mesh.labels import CANONICAL_VERSION


def build_regions_meta(
    seg_path: Path,
    regions: list[dict],
    variant_id: str,
    engine: dict,
    seg_file: str,
) -> dict:
    """스펙 §2.2 regions-meta.json 전체.

    engine은 {"name", "version"} 키가 반드시 있어야 한다 — 소비 측 계약에
    엔진 정보가 null로 새면(호출자가 키 이름을 잘못 쓴 경우 등) 원인을
    한참 뒤에야 찾게 되므로, 여기서 바로 막는다.
    """
    if "name" not in engine or "version" not in engine:
        raise ValueError(f"engine에 name/version이 필요하다: {engine!r}")
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
            "engine": engine["name"],
            "engineVersion": engine["version"],
            "segFile": seg_file,
        },
        "meshConfig": None,  # 탐색 단계 — 정본 확정 전(todo §6.6)
        "meshVariantId": variant_id,
        "regions": regions,
    }


def write_regions_meta(out_path: Path, meta: dict) -> None:
    """원자적으로 쓴다(임시파일 → rename).

    regions-meta.json은 소비 측(brain-educate)이 그대로 읽는 최종 산출물
    중 하나다 — write_status와 같은 이유로, 쓰다 만 파일을 반쪽 계약인 채로
    남기지 않는다(status.py의 write_status 참고).
    """
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_path)
