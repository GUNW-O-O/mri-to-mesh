"""정적 리맵 (스펙 §3, §2.2)."""

from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

from mri2mesh.labels import (
    RemapError,
    build_lookup,
    load_canonical,
    remap_segmentation,
)


def _write_seg(path, data, zooms=(1.0, 1.0, 1.0)):
    affine = np.diag([zooms[0], zooms[1], zooms[2], 1.0])
    img = nib.Nifti1Image(np.asarray(data, dtype=np.int32), affine)
    img.header.set_zooms(zooms)
    nib.save(img, path)
    return path


def test_lookup_is_uint8_and_covers_every_fs_id():
    table = load_canonical()
    lut = build_lookup(table)

    assert lut.dtype == np.uint8
    assert len(lut) == max(e.fs_id for e in table.entries) + 1
    for entry in table.entries:
        assert lut[entry.fs_id] == entry.id


def test_lookup_maps_absent_labels_to_background():
    """표에 없는 라벨은 0이 된다 (스펙 §3).

    1004(corpuscallosum)는 DKT에서 빠진 피질 라벨이라 표에 없다. 잘못
    잘라내면 이웃 라벨 번호로 붙어 조용히 틀린 구조가 된다.
    """
    lut = build_lookup(load_canonical())

    assert lut[0] == 0
    assert lut[1004] == 0
    assert lut[1] == 0


def test_remap_writes_uint8_and_keeps_geometry(tmp_path):
    """저장 dtype은 uint8, affine과 voxel 간격은 그대로 옮긴다."""
    by_fs = load_canonical().by_fs_id()
    data = np.zeros((4, 4, 4), dtype=np.int32)
    data[0, 0, 0] = 17
    data[1, 1, 1] = 53
    src = _write_seg(tmp_path / "in.nii.gz", data, zooms=(1.0, 1.0, 1.0))

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    out = nib.load(result.seg_path)
    assert out.get_data_dtype() == np.uint8
    arr = np.asanyarray(out.dataobj)
    assert arr[0, 0, 0] == by_fs[17].id
    assert arr[1, 1, 1] == by_fs[53].id
    assert np.allclose(out.affine, nib.load(src).affine)


def test_remap_drops_labels_not_in_table(tmp_path):
    """표에 없는 라벨은 배경이 된다. 이웃 번호로 붙지 않는다."""
    data = np.zeros((3, 3, 3), dtype=np.int32)
    data[0, 0, 0] = 1004  # DKT에서 빠진 라벨
    data[1, 1, 1] = 9999  # LUT 범위 밖
    src = _write_seg(tmp_path / "in.nii.gz", data)

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    arr = np.asanyarray(nib.load(result.seg_path).dataobj)
    assert arr[0, 0, 0] == 0
    assert arr[1, 1, 1] == 0


def test_voxel_counts_and_volume(tmp_path):
    """volumeMm3 = 리맵 전 voxel count × voxel 부피 (스펙 §2.2).

    stats 파일을 파싱하지 않는다.
    """
    by_fs = load_canonical().by_fs_id()
    data = np.zeros((4, 4, 4), dtype=np.int32)
    data[0, 0, :3] = 17  # 3 voxel
    src = _write_seg(tmp_path / "in.nii.gz", data, zooms=(2.0, 1.0, 0.5))

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    hippocampus = by_fs[17].id
    assert result.voxel_counts[hippocampus] == 3
    assert result.voxel_volume_mm3 == pytest.approx(1.0)  # 2.0 * 1.0 * 0.5
    assert result.volume_mm3(hippocampus) == pytest.approx(3.0)


def test_voxel_counts_exclude_background(tmp_path):
    """배경은 영역이 아니다. regions 배열에 0번 행이 생기면 안 된다."""
    data = np.zeros((3, 3, 3), dtype=np.int32)
    data[0, 0, 0] = 17
    src = _write_seg(tmp_path / "in.nii.gz", data)

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    assert 0 not in result.voxel_counts


def test_absent_label_has_zero_volume(tmp_path):
    """케이스에 없는 구조는 다른 구조의 번호에 영향을 주지 않는다 (스펙 §3)."""
    data = np.zeros((3, 3, 3), dtype=np.int32)
    data[0, 0, 0] = 17
    src = _write_seg(tmp_path / "in.nii.gz", data)

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    amygdala = load_canonical().by_fs_id()[18].id
    assert amygdala not in result.voxel_counts
    assert result.volume_mm3(amygdala) == 0.0


def test_negative_values_are_background(tmp_path):
    """음수 라벨은 있을 수 없지만, 오면 룩업 인덱스가 뒤에서부터 잡혀
    엉뚱한 라벨이 된다. 배경으로 눌러야 한다."""
    data = np.zeros((3, 3, 3), dtype=np.int32)
    data[0, 0, 0] = -5
    src = _write_seg(tmp_path / "in.nii.gz", data)

    result = remap_segmentation(src, tmp_path / "seg.nii.gz")

    arr = np.asanyarray(nib.load(result.seg_path).dataobj)
    assert arr[0, 0, 0] == 0


def test_unreadable_input_raises(tmp_path):
    bad = tmp_path / "broken.nii.gz"
    bad.write_bytes(b"not a nifti")

    with pytest.raises(RemapError):
        remap_segmentation(bad, tmp_path / "seg.nii.gz")
