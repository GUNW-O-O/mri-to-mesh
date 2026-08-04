"""축1 전처리 (스펙 §6.5)."""

from __future__ import annotations

import numpy as np

from mri2mesh.mesh import Preprocess
from mri2mesh.mesh.preprocess import apply_preprocess


def test_none_returns_binary_field_at_half_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    assert field.dtype == np.float32
    assert level == 0.5
    assert set(np.unique(field)) <= {0.0, 1.0}


def test_gaussian_blurs_but_keeps_half_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="gaussian", sigma_vox=0.8))
    assert level == 0.5
    assert np.any((field > 0.01) & (field < 0.99))
    assert field[16, 16, 16] > 0.9


def test_distance_is_signed_with_zero_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="distance"))
    assert level == 0.0
    assert field[16, 16, 16] > 0
    assert field[0, 0, 0] < 0


def test_unknown_method_raises(sphere_mask):
    import pytest

    with pytest.raises(ValueError):
        apply_preprocess(sphere_mask, Preprocess(method="nope"))
