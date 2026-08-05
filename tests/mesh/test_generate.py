"""변형 생성 조합 (스펙 §6.5~§7.2)."""

from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from mri2mesh.mesh import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)
from mri2mesh.mesh.generate import GenerateError, _ras_to_gltf_yup, generate_variant


def test_ras_to_gltf_yup_maps_superior_to_up():
    """RAS(x=right, y=anterior, z=superior) → glTF Y-up(x, z, -y).
    superior(+Z)가 위(+Y)로, anterior(+Y)가 -Z로 간다."""
    v = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 5.0]])
    out = _ras_to_gltf_yup(v)
    assert out.tolist() == [[1.0, 3.0, -2.0], [0.0, 5.0, -0.0]]
    # 위(superior)는 결과의 +Y로 온다
    assert out[1].tolist() == [0.0, 5.0, -0.0]


def test_generate_writes_all_variant_files(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)

    assert (out / "regions.glb").is_file()
    assert (out / "metrics.json").is_file()
    assert (out / "params.json").is_file()
    assert result.variant_id.startswith("v01-")


def test_glb_nodes_match_present_labels(tmp_path, synthetic_seg):
    """합성 seg에 라벨 13·1·40이 있다. min_voxel 아래는 없다."""
    out = tmp_path / "v01"
    generate_variant(synthetic_seg, out, default_params(), index=1)

    scene = trimesh.load(out / "regions.glb")
    assert set(scene.geometry.keys()) == {"label_13", "label_1", "label_40"}


def test_min_voxel_skips_small_labels(tmp_path, synthetic_seg):
    """min_voxel을 크게 잡으면 작은 라벨이 빠지고 metrics에 스킵 수가 남는다."""
    out = tmp_path / "v01"
    params = MeshParams(
        preprocess=Preprocess(), extractor=Extractor(), smoothing=Smoothing(),
        decimation=Decimation(), min_voxel=1500,
    )
    result = generate_variant(synthetic_seg, out, params, index=1)

    node_ids = {r["labelId"] for r in result.regions}
    assert 40 in node_ids  # 얇은 판 1800 >= 1500
    assert 13 not in node_ids  # 구 < 1500
    assert 1 not in node_ids  # 큐브 1000 < 1500
    assert result.metrics["summary"]["labelsSkippedByMinVoxel"] == 2


def test_regions_carry_metadata_for_meta_json(tmp_path, synthetic_seg):
    """regions 항목은 regions-meta.json에 들어갈 재료다(스펙 §2.2)."""
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)

    by_id = {r["labelId"]: r for r in result.regions}
    hip = by_id[13]
    assert hip["name"] == "Left-Hippocampus"
    assert hip["nodeName"] == "label_13"
    assert hip["color"] == [220, 216, 20]
    assert hip["group"] == "subcortical"
    assert hip["side"] == "L"
    assert hip["volumeMm3"] > 0
    assert len(hip["centroid"]) == 3
    assert hip["triangleCount"] > 0


def test_params_json_has_variant_id_and_created_at(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    generate_variant(synthetic_seg, out, default_params(), index=1)

    params = json.loads((out / "params.json").read_text(encoding="utf-8"))
    assert params["variantId"].startswith("v01-")
    assert "createdAt" in params
    assert params["labelTable"] == "canonical-v1"


def test_metrics_total_vertices_filled(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)
    assert result.metrics["total"]["vertices"] > 0


def test_affine_applied_once_world_coordinates(tmp_path):
    """비등방 affine에서 정점이 world mm로 한 번만 변환된다(이중 스케일 금지)."""
    import nibabel as nib

    vol = np.zeros((30, 30, 30), dtype=np.uint8)
    vol[10:20, 10:20, 10:20] = 1  # 큐브 라벨 1
    affine = np.array([
        [2.0, 0, 0, 100.0],
        [0, 1.0, 0, -50.0],
        [0, 0, 0.5, 30.0],
        [0, 0, 0, 1.0],
    ])
    img = nib.Nifti1Image(vol, affine)
    img.header.set_zooms((2.0, 1.0, 0.5))
    seg = tmp_path / "aniso.nii.gz"
    nib.save(img, seg)

    out = tmp_path / "v01"
    generate_variant(seg, out, default_params(), index=1)

    scene = trimesh.load(out / "regions.glb")
    mesh = scene.geometry["label_1"]
    # voxel 10..20 큐브의 world x = 100 + 2*10 .. 100 + 2*20 = 120..140
    assert mesh.bounds[0][0] == pytest.approx(120.0, abs=1.0)
    assert mesh.bounds[1][0] == pytest.approx(140.0, abs=1.0)


def test_unreadable_seg_raises(tmp_path):
    bad = tmp_path / "bad.nii.gz"
    bad.write_bytes(b"not nifti")
    with pytest.raises(GenerateError):
        generate_variant(bad, tmp_path / "v01", default_params(), index=1)
