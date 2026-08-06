"""축0: 연결요소 정리 (뇌실주변 파편/스파이크 제거)."""

from __future__ import annotations

import numpy as np

from mri2mesh.mesh import Cleanup, drop_small_components


def _mask_with_stray():
    """한 변 10 큐브(1000 voxel) 본체 + 멀리 떨어진 1 voxel 파편."""
    m = np.zeros((30, 30, 30), dtype=bool)
    m[2:12, 2:12, 2:12] = True  # 본체 1000
    m[25, 25, 25] = True  # 떠돌이 파편 1
    return m


def test_none_is_passthrough():
    m = _mask_with_stray()
    out = drop_small_components(m, Cleanup(method="none", min_component_vox=30))
    assert np.array_equal(out, m)  # 아무것도 안 지운다


def test_threshold_le_one_is_passthrough():
    m = _mask_with_stray()
    out = drop_small_components(m, Cleanup(method="drop_small_components", min_component_vox=1))
    assert np.array_equal(out, m)


def test_drops_small_stray_keeps_body():
    m = _mask_with_stray()
    out = drop_small_components(m, Cleanup(method="drop_small_components", min_component_vox=30))
    assert out[25, 25, 25] == False  # 파편 제거
    assert out[5, 5, 5] == True  # 본체 유지
    assert int(out.sum()) == 1000


def test_keeps_multifocal_lesions_above_threshold():
    """WMH처럼 여러 조각이어도 임계 이상이면 다 남는다."""
    m = np.zeros((40, 40, 40), dtype=bool)
    m[2:8, 2:8, 2:8] = True  # 조각 A = 216
    m[30:36, 30:36, 30:36] = True  # 조각 B = 216
    m[20, 20, 20] = True  # 노이즈 1
    out = drop_small_components(m, Cleanup(method="drop_small_components", min_component_vox=50))
    assert int(out.sum()) == 432  # A·B 유지, 노이즈만 제거
    assert out[20, 20, 20] == False


def test_all_below_threshold_becomes_empty():
    m = np.zeros((20, 20, 20), dtype=bool)
    m[1, 1, 1] = True
    m[10, 10, 10] = True
    out = drop_small_components(m, Cleanup(method="drop_small_components", min_component_vox=30))
    assert out.any() == False


def test_empty_mask_is_safe():
    m = np.zeros((10, 10, 10), dtype=bool)
    out = drop_small_components(m, Cleanup(method="drop_small_components", min_component_vox=30))
    assert out.any() == False
