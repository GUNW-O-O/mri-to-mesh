"""regions-meta.json 조립 (스펙 §2.2)."""

from __future__ import annotations

import numpy as np
import nibabel as nib

from seg_and_mesh.jobs.meta import build_regions_meta, write_regions_meta


def _seg(tmp_path):
    vol = np.zeros((4, 4, 4), np.uint8)
    vol[0, 0, 0] = 13
    affine = np.diag([1.0, 1.0, 1.0, 1.0])
    img = nib.Nifti1Image(vol, affine)
    img.header.set_zooms((1.0, 1.0, 1.0))
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
    assert meta["source"]["segFile"] == "aparc.DKTatlas+aseg.deep.withCC.mgz"
    assert meta["regions"] == _regions()


def test_space_has_native_affine_not_mni(tmp_path):
    """space.affine은 native. toMNI152는 지금 안 넣는다(todo)."""
    meta = build_regions_meta(
        _seg(tmp_path), _regions(), "v01-3c81",
        engine={"name": "fastsurfer", "version": "2.5.4"},
        seg_file="seg.mgz",
    )
    assert len(meta["space"]["affine"]) == 4
    assert meta["space"]["shape"] == [4, 4, 4]
    assert meta["space"]["voxelSize"] == [1.0, 1.0, 1.0]
    assert "toMNI152" not in meta["space"]


def test_write_reads_back(tmp_path):
    import json

    meta = build_regions_meta(
        _seg(tmp_path), _regions(), "v01-3c81",
        engine={"name": "fastsurfer", "version": "2.5.4"}, seg_file="seg.mgz",
    )
    out = tmp_path / "regions-meta.json"
    write_regions_meta(out, meta)
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == 2
