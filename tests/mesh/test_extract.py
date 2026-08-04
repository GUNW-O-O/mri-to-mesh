"""축2 표면 추출 (스펙 §6.5).

구(반경 8, 부피 4/3 pi r^3 ≈ 2144.66)로 각 추출기의 부피 정확도를 본다.
mesh 부피는 voxel 부피보다 약간 작게 나오는 게 정상(계단을 깎으므로).
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mri2mesh.mesh import EXTRACTOR_NAMES, Preprocess
from mri2mesh.mesh.extract import ExtractError, extract
from mri2mesh.mesh.preprocess import apply_preprocess

ANALYTIC_SPHERE_VOLUME = 4.0 / 3.0 * np.pi * 8.0**3  # ≈ 2144.66


def _volume(verts, faces):
    return abs(trimesh.Trimesh(vertices=verts, faces=faces, process=False).volume)


def test_registry_lists_all_five_axis2_options():
    assert set(EXTRACTOR_NAMES) == {
        "skimage_mc", "pymcubes", "vtk_flyingedges",
        "vtk_surfacenets", "vtk_contour_perlabel",
    }


@pytest.mark.parametrize("name", [
    "skimage_mc", "pymcubes", "vtk_flyingedges",
    "vtk_surfacenets", "vtk_contour_perlabel",
])
def test_each_extractor_reconstructs_sphere_volume(sphere_mask, name):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    verts, faces = extract(field, level, name)

    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3
    assert len(faces) > 0

    vol = _volume(verts, faces)
    assert vol == pytest.approx(ANALYTIC_SPHERE_VOLUME, rel=0.15), f"{name}: {vol}"


@pytest.mark.parametrize("name", ["skimage_mc", "vtk_surfacenets", "vtk_flyingedges"])
def test_watertight_for_compact_label(sphere_mask, name):
    """볼륨 경계에 안 닿는 조밀 구조는 닫힌 표면이어야 한다."""
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    verts, faces = extract(field, level, name)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    assert mesh.is_watertight


def test_unknown_extractor_raises(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    with pytest.raises(ExtractError):
        extract(field, level, "does_not_exist")
