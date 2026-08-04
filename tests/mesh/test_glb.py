"""GLB 작성 (스펙 §6.7)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mri2mesh.mesh import Preprocess
from mri2mesh.mesh.extract import extract
from mri2mesh.mesh.glb import GlbError, write_glb
from mri2mesh.mesh.preprocess import apply_preprocess


def _sphere(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_nodes_are_named_label_id(tmp_path, sphere_mask):
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    size = write_glb({13: (verts, faces), 1: (verts, faces)}, out)

    assert size > 0
    assert out.stat().st_size == size

    scene = trimesh.load(out)
    assert set(scene.geometry.keys()) == {"label_13", "label_1"}


def test_no_material_color_baked(tmp_path, sphere_mask):
    """색은 regions-meta.json이 운반한다(B안). GLB에 색을 굽지 않는다 —
    정점 색을 실지 않았으므로 재로드 시 색 변이가 없다."""
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    write_glb({13: (verts, faces)}, out)

    mesh = trimesh.load(out).geometry["label_13"]
    vc = getattr(mesh.visual, "vertex_colors", None)
    if vc is not None and len(vc):
        assert len(np.unique(np.asarray(vc), axis=0)) == 1  # 균일(구운 색 없음)


def test_coordinates_preserved(tmp_path, sphere_mask):
    """정점 좌표가 그대로 실린다(호출자가 이미 world mm로 만들었다)."""
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    write_glb({13: (verts, faces)}, out)

    mesh = trimesh.load(out).geometry["label_13"]
    assert np.allclose(mesh.bounds[0], verts.min(axis=0), atol=1e-3)
    assert np.allclose(mesh.bounds[1], verts.max(axis=0), atol=1e-3)


def test_empty_meshes_dict_raises(tmp_path):
    with pytest.raises(GlbError):
        write_glb({}, tmp_path / "regions.glb")
