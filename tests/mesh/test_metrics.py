"""지표 계산 (스펙 §7.2)."""

from __future__ import annotations

import numpy as np
import pytest

from mri2mesh.mesh import Preprocess
from mri2mesh.mesh.extract import extract
from mri2mesh.mesh.metrics import region_metrics, summarize
from mri2mesh.mesh.preprocess import apply_preprocess

ANALYTIC_SPHERE_VOLUME = 4.0 / 3.0 * np.pi * 8.0**3


def _sphere(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_region_metrics_volume_error(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    voxel_count = int(sphere_mask.sum())
    m = region_metrics(13, verts, faces, voxel_count, 1.0)

    assert m["labelId"] == 13
    assert m["triangles"] == len(faces)
    assert m["voxelVolumeMm3"] == pytest.approx(float(voxel_count))
    assert m["meshVolumeMm3"] == pytest.approx(ANALYTIC_SPHERE_VOLUME, rel=0.1)
    assert -10 < m["volumeErrorPct"] < 5
    assert m["surfaceAreaMm2"] > 0


def test_watertight_sphere_has_no_boundary_or_nonmanifold(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    m = region_metrics(13, verts, faces, int(sphere_mask.sum()), 1.0)
    assert m["boundaryEdges"] == 0
    assert m["nonManifoldEdges"] == 0


def test_open_mesh_reports_boundary_edges():
    """면 하나가 빠진 사면체는 그 면의 세 변이 경계가 된다."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    # 사면체 4면 중 [1,2,3]을 뺀다 → 그 세 변만 면 하나에 속해 경계가 된다.
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3]], dtype=int)
    m = region_metrics(1, verts, faces, 1, 1.0)
    assert m["boundaryEdges"] == 3


def test_summarize_shape(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    per = [region_metrics(13, verts, faces, int(sphere_mask.sum()), 1.0)]
    out = summarize(
        per,
        durations={"extract": 0.4, "smooth": 0.0, "decimate": 0.0, "write": 0.2},
        glb_bytes=12345,
        labels_skipped=3,
    )
    assert out["glbBytes"] == 12345
    assert out["total"]["triangles"] == len(faces)
    assert out["perRegion"] == per
    assert out["summary"]["labelsSkippedByMinVoxel"] == 3
    assert "volumeErrorPctMedian" in out["summary"]
    assert out["durationSec"]["extract"] == 0.4
