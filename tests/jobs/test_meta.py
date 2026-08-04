"""regions-meta.json 조립 (스펙 §2.2)."""

from __future__ import annotations

import numpy as np
import nibabel as nib

from mri2mesh.jobs.meta import build_regions_meta, write_regions_meta


# 비대칭 affine — 전치·축스왑·2중 적용이 나면 값이 어긋나 테스트가 깨진다.
# 등방 항등행렬은 자기 전치와 같아 그 부류 결함을 못 잡는다.
_AFFINE = [
    [-2.0, 0.0, 0.0, 90.0],
    [0.0, 1.5, 0.0, -110.0],
    [0.0, 0.0, 3.0, -72.0],
    [0.0, 0.0, 0.0, 1.0],
]


def _seg(tmp_path):
    vol = np.zeros((4, 4, 4), np.uint8)
    vol[0, 0, 0] = 13
    img = nib.Nifti1Image(vol, np.array(_AFFINE, dtype=float))
    p = tmp_path / "seg.nii.gz"
    nib.save(img, p)
    return p


def _regions():
    return [{
        "labelId": 13, "name": "Left-Hippocampus", "color": [220, 216, 20],
        "nodeName": "label_13", "fsId": 17, "group": "subcortical", "side": "L",
        "volumeMm3": 3808.0, "centroid": [-32.6, 3.9, -12.7], "triangleCount": 6364,
    }]


def test_header_and_regions(tmp_path):
    meta = build_regions_meta(
        _seg(tmp_path), _regions(), "v01-3c81",
        engine={"name": "fastsurfer", "version": "2.5.4"},
        seg_file="aparc.DKTatlas+aseg.deep.withCC.mgz",
    )
    assert meta["version"] == 2
    assert meta["labelTable"] == "canonical-v1"
    assert meta["meshVariantId"] == "v01-3c81"
    assert meta["source"]["engine"] == "fastsurfer"
    assert meta["source"]["engineVersion"] == "2.5.4"
    assert meta["source"]["segFile"] == "aparc.DKTatlas+aseg.deep.withCC.mgz"
    # meshConfig는 정본 확정 전이라 null이지만 키 자체는 계약에 있어야 한다.
    assert "meshConfig" in meta
    assert meta["meshConfig"] is None
    assert meta["regions"] == _regions()


def test_space_has_native_affine_not_mni(tmp_path):
    """space.affine은 native. toMNI152는 지금 안 넣는다(todo)."""
    meta = build_regions_meta(
        _seg(tmp_path), _regions(), "v01-3c81",
        engine={"name": "fastsurfer", "version": "2.5.4"},
        seg_file="seg.mgz",
    )
    # 값을 통째로 어서트한다 — 행 개수만 보면 전치·딴 행렬·2중 적용을 놓친다.
    assert meta["space"]["affine"] == _AFFINE
    assert meta["space"]["shape"] == [4, 4, 4]
    assert meta["space"]["voxelSize"] == [2.0, 1.5, 3.0]
    assert "toMNI152" not in meta["space"]


def test_missing_engine_key_raises(tmp_path):
    """engine 정보가 null로 새 나가면 소비 측이 한참 뒤에 깨진다 — 바로 막는다."""
    import pytest

    with pytest.raises(ValueError):
        build_regions_meta(
            _seg(tmp_path), _regions(), "v01-3c81",
            engine={"name": "fastsurfer"},  # version 없음
            seg_file="seg.mgz",
        )


def test_write_reads_back(tmp_path):
    import json

    meta = build_regions_meta(
        _seg(tmp_path), _regions(), "v01-3c81",
        engine={"name": "fastsurfer", "version": "2.5.4"}, seg_file="seg.mgz",
    )
    out = tmp_path / "regions-meta.json"
    write_regions_meta(out, meta)
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == 2
