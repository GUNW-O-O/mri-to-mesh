"""합성 라벨 볼륨 (스펙 §13 — 구·큐브·판으로 전 축 조합 검증)."""

from __future__ import annotations

import numpy as np
import pytest


def _sphere(shape, center, radius):
    zz, yy, xx = np.ogrid[: shape[0], : shape[1], : shape[2]]
    d2 = (zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2
    return d2 <= radius**2


@pytest.fixture
def sphere_mask():
    """반경 8, 부피 해석해 = 4/3 pi r^3 ≈ 2144.66 voxel."""
    m = np.zeros((32, 32, 32), dtype=np.uint8)
    m[_sphere((32, 32, 32), (16, 16, 16), 8)] = 1
    return m


@pytest.fixture
def sphere_radius():
    return 8.0


@pytest.fixture
def cube_mask():
    """10^3 큐브 = 1000 voxel."""
    m = np.zeros((20, 20, 20), dtype=np.uint8)
    m[5:15, 5:15, 5:15] = 1
    return m


@pytest.fixture
def synthetic_seg(tmp_path):
    """구(라벨 13)·큐브(라벨 1)·얇은 판(라벨 40)이 든 40^3 라벨 볼륨을
    canonical id로 채워 NIfTI로 쓴다. affine은 등방 1mm."""
    import nibabel as nib

    vol = np.zeros((40, 40, 40), dtype=np.uint8)
    vol[_sphere((40, 40, 40), (10, 10, 10), 6)] = 13  # 구
    vol[25:35, 25:35, 25:35] = 1  # 큐브
    vol[20:22, 5:35, 5:35] = 40  # 얇은 판 (두께 2)
    affine = np.diag([1.0, 1.0, 1.0, 1.0])
    img = nib.Nifti1Image(vol, affine)
    img.header.set_zooms((1.0, 1.0, 1.0))
    path = tmp_path / "synthetic_seg.nii.gz"
    nib.save(img, path)
    return path
