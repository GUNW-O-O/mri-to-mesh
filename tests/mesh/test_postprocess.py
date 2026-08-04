"""축3 후처리 (스펙 §6.5)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mri2mesh.mesh import Decimation, Preprocess, Smoothing
from mri2mesh.mesh.extract import extract
from mri2mesh.mesh.postprocess import PostprocessError, decimate, smooth
from mri2mesh.mesh.preprocess import apply_preprocess


def _sphere_mesh(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_smooth_none_is_identity(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = smooth(verts, faces, Smoothing(method="none"))
    assert np.allclose(v2, verts)
    assert np.array_equal(f2, faces)


@pytest.mark.parametrize("method", ["laplacian", "taubin", "humphrey"])
def test_smoothing_reduces_surface_roughness(sphere_mask, method):
    """스무딩 후 표면적이 줄어든다(계단이 깎이므로)."""
    verts, faces = _sphere_mesh(sphere_mask)
    area0 = trimesh.Trimesh(verts, faces, process=False).area
    v2, f2 = smooth(verts, faces, Smoothing(method=method, iterations=15))
    area1 = trimesh.Trimesh(v2, f2, process=False).area
    assert area1 < area0
    assert len(v2) == len(verts)  # 스무딩은 위상을 안 바꾼다


def test_taubin_shrinks_less_than_laplacian(sphere_mask):
    """taubin은 수축 억제 필터다(스펙 §6.5). 부피가 laplacian보다 덜 준다."""
    verts, faces = _sphere_mesh(sphere_mask)
    v0 = abs(trimesh.Trimesh(verts, faces, process=False).volume)

    vl, fl = smooth(verts, faces, Smoothing(method="laplacian", iterations=30, relaxation=0.1))
    vt, ft = smooth(verts, faces, Smoothing(method="taubin", iterations=30))
    vol_l = abs(trimesh.Trimesh(vl, fl, process=False).volume)
    vol_t = abs(trimesh.Trimesh(vt, ft, process=False).volume)

    assert (v0 - vol_t) < (v0 - vol_l)


def test_decimate_none_is_identity(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = decimate(verts, faces, Decimation(method="none"))
    assert np.array_equal(f2, faces)


def test_quadric_reduces_triangle_count(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = decimate(verts, faces, Decimation(method="quadric", target_ratio=0.3))
    assert len(f2) < len(faces) * 0.5


def test_unknown_smoothing_raises(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    with pytest.raises(PostprocessError):
        smooth(verts, faces, Smoothing(method="nope"))
