"""지표 계산 (스펙 §7.2).

파라미터를 눈으로만 고르면 근거가 없다. 변형마다 부피 오차·위상 결함·비용을
자동 계산한다. 이 값은 판단 근거일 뿐 자동 게이트가 아니다(스펙 §7.2).
"""

from __future__ import annotations

import numpy as np
import trimesh


def _edge_defects(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """(경계 변 수, 비다양체 변 수).

    경계 변 = 면 하나에만 속한 변(구멍). 비다양체 변 = 셋 이상 면에 속한 변.
    """
    _, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    boundary = int(np.sum(counts == 1))
    nonmanifold = int(np.sum(counts > 2))
    return boundary, nonmanifold


def region_metrics(label_id, verts, faces, voxel_count, voxel_volume_mm3) -> dict:
    """스펙 §7.2 perRegion 항목 하나."""
    mesh = trimesh.Trimesh(
        vertices=np.asarray(verts), faces=np.asarray(faces), process=False
    )
    # 퇴화(부피 0) 메시는 trimesh가 center_mass에서 0으로 나눠 경고를 낸다.
    # 실제 라벨은 부피가 있지만, 슬라이버가 오면 경고 대신 0을 보고한다.
    with np.errstate(all="ignore"):
        mesh_volume = float(abs(mesh.volume))
    if not np.isfinite(mesh_volume):
        mesh_volume = 0.0
    voxel_volume = float(voxel_count) * float(voxel_volume_mm3)
    err = (
        100.0 * (mesh_volume - voxel_volume) / voxel_volume
        if voxel_volume > 0
        else 0.0
    )
    boundary, nonmanifold = _edge_defects(mesh)
    return {
        "labelId": int(label_id),
        "triangles": int(len(faces)),
        "meshVolumeMm3": round(mesh_volume, 2),
        "voxelVolumeMm3": round(voxel_volume, 2),
        "volumeErrorPct": round(err, 2),
        "surfaceAreaMm2": round(float(mesh.area), 2),
        "boundaryEdges": boundary,
        "nonManifoldEdges": nonmanifold,
    }


def summarize(per_region, durations, glb_bytes, labels_skipped) -> dict:
    """스펙 §7.2 metrics.json 전체.

    total.vertices는 0으로 두고 generate가 실제 정점 합으로 덮어쓴다 —
    perRegion만으로는 정점 수를 알 수 없다.
    """
    total_tris = sum(r["triangles"] for r in per_region)
    errs = [r["volumeErrorPct"] for r in per_region] or [0.0]
    return {
        "durationSec": durations,
        "glbBytes": int(glb_bytes),
        "total": {"vertices": 0, "triangles": int(total_tris)},
        "perRegion": per_region,
        "summary": {
            "volumeErrorPctMedian": round(float(np.median(errs)), 2),
            "volumeErrorPctMax": round(float(max(errs, key=abs)), 2),
            "regionsWithBoundaryEdges": int(sum(1 for r in per_region if r["boundaryEdges"] > 0)),
            "regionsWithNonManifold": int(sum(1 for r in per_region if r["nonManifoldEdges"] > 0)),
            "labelsSkippedByMinVoxel": int(labels_skipped),
        },
    }
