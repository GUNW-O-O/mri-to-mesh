"""변형 생성 조합 (스펙 §6.5~§7.2).

canonical seg.nii.gz를 읽어 라벨별로 축1(전처리)→축2(추출)→축3(후처리)를
적용하고, 정점에 affine을 한 번만 적용해 world mm로 옮긴 뒤 GLB·metrics.json·
params.json을 쓴다. regions(라벨별 메타)는 소비 측 regions-meta.json 재료다.
"""

from __future__ import annotations

import json
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import nibabel as nib
import numpy as np

from mri2mesh.labels import load_canonical
from mri2mesh.mesh.extract import extract
from mri2mesh.mesh.glb import write_glb
from mri2mesh.mesh.metrics import region_metrics, summarize
from mri2mesh.mesh.params import MeshParams
from mri2mesh.mesh.postprocess import decimate, smooth
from mri2mesh.mesh.preprocess import apply_preprocess


class GenerateError(RuntimeError):
    """세그를 읽지 못했거나 변형 생성에 실패했다."""


def _ras_to_gltf_yup(verts: np.ndarray) -> np.ndarray:
    """NIfTI RAS world 좌표를 glTF 표준 Y-up으로 변환.

    RAS는 (x,y,z)=(right, anterior, superior)로 Z가 위(superior)다. 하지만
    glTF/three.js 표준은 Y-up이다 — 그래픽으로 반출하는 경계에서 한 번 변환해야
    소비 측(brain-educate 등)이 회전 보정 없이 똑바로 본다.
    (x,y,z) → (x, z, -y): right는 그대로, superior(+Z)→위(+Y), anterior(+Y)→
    -Z(posterior가 +Z). GLB 정점과 regions-meta centroid가 같은 프레임이 되도록
    centroid 계산 전에 적용한다.
    """
    return np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]])


@dataclass(frozen=True)
class VariantResult:
    variant_id: str
    glb_path: Path
    metrics: dict
    params: dict
    regions: list


def generate_variant(seg_path, out_dir, params: MeshParams, index: int = 1, table=None) -> VariantResult:
    """canonical seg + params -> 변형 하나. GLB·metrics.json·params.json을 쓴다.

    Raises:
        GenerateError: 입력을 읽지 못했거나 만들 메시가 하나도 없을 때.
    """
    table = table or load_canonical()
    by_id = table.by_id()
    seg_path = Path(seg_path)
    out_dir = Path(out_dir)

    try:
        img = nib.load(seg_path)
        data = np.asanyarray(img.dataobj)
    except (nib.filebasedimages.ImageFileError, OSError, EOFError, zlib.error) as exc:
        raise GenerateError(f"세그를 읽지 못했다: {seg_path}") from exc

    affine = img.affine
    R = affine[:3, :3]
    t = affine[:3, 3]
    voxel_volume = float(np.prod([abs(float(z)) for z in img.header.get_zooms()[:3]]))

    labels, counts = np.unique(data, return_counts=True)
    present = {int(l): int(c) for l, c in zip(labels, counts) if int(l) != 0}

    meshes: dict = {}
    regions: list = []
    per_region: list = []
    skipped = 0
    total_vertices = 0
    durations = {"extract": 0.0, "smooth": 0.0, "decimate": 0.0, "write": 0.0}

    for label_id, voxel_count in sorted(present.items()):
        entry = by_id.get(label_id)
        if entry is None:
            continue  # 표에 없는 값(있으면 안 되지만 방어)
        if voxel_count < params.min_voxel:
            skipped += 1
            continue

        mask = data == label_id

        t0 = time.perf_counter()
        field, isolevel = apply_preprocess(mask, params.preprocess)
        verts_vox, faces = extract(field, isolevel, params.extractor.name, dict(params.extractor.options))
        durations["extract"] += time.perf_counter() - t0

        if len(faces) == 0:
            skipped += 1
            continue

        t0 = time.perf_counter()
        verts_vox, faces = smooth(verts_vox, faces, params.smoothing)
        durations["smooth"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        verts_vox, faces = decimate(verts_vox, faces, params.decimation)
        durations["decimate"] += time.perf_counter() - t0

        # affine 한 번만 적용 -> world mm (스펙 §6.7, 이중 스케일 금지)
        verts_world = verts_vox @ R.T + t
        # 그래픽 반출 경계에서 RAS(Z-up) → glTF 표준 Y-up으로 한 번 변환한다.
        verts_world = _ras_to_gltf_yup(verts_world)

        meshes[label_id] = (verts_world, faces)
        total_vertices += len(verts_world)

        rm = region_metrics(label_id, verts_world, faces, voxel_count, voxel_volume)
        per_region.append(rm)

        centroid = verts_world.mean(axis=0)
        regions.append({
            "labelId": label_id,
            "name": entry.name,
            "color": list(entry.color),
            "nodeName": f"label_{label_id}",
            "fsId": entry.fs_id,
            "group": entry.group,
            "side": entry.side,
            "volumeMm3": round(float(voxel_count) * voxel_volume, 2),
            "centroid": [round(float(c), 2) for c in centroid],
            "triangleCount": int(len(faces)),
        })

    if not meshes:
        raise GenerateError(
            f"만들 메시가 없다(모든 라벨이 min_voxel {params.min_voxel} 미만이거나 부재)"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    glb_path = out_dir / "regions.glb"
    glb_bytes = write_glb(meshes, glb_path)
    durations["write"] += time.perf_counter() - t0

    durations = {k: round(v, 3) for k, v in durations.items()}
    metrics = summarize(per_region, durations, glb_bytes, skipped)
    metrics["total"]["vertices"] = int(total_vertices)

    variant_id = params.variant_id(index)
    params_dict = params.to_params_dict()
    params_dict["variantId"] = variant_id
    params_dict["createdAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "params.json").write_text(
        json.dumps(params_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return VariantResult(
        variant_id=variant_id,
        glb_path=glb_path,
        metrics=metrics,
        params=params_dict,
        regions=regions,
    )
