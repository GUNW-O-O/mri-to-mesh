"""파라미터 모델과 variantId (스펙 §7, §7.1)."""

from __future__ import annotations

from mri2mesh.mesh import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)


def test_default_params_are_conservative():
    p = default_params()
    assert p.preprocess.method == "none"
    assert p.extractor.name == "skimage_mc"
    assert p.smoothing.method == "none"
    assert p.decimation.method == "none"
    assert p.min_voxel == 100
    assert p.label_table == "canonical-v1"


def test_params_are_frozen():
    p = default_params()
    import pytest

    with pytest.raises(Exception):
        p.min_voxel = 0


def test_params_dict_uses_spec_camelcase():
    """스펙 §7.1 params.json 키. 소비 측이 이 형태를 기대한다."""
    p = MeshParams(
        preprocess=Preprocess(method="gaussian", sigma_vox=0.6),
        extractor=Extractor(name="vtk_surfacenets"),
        smoothing=Smoothing(method="taubin", iterations=20, pass_band=0.1, feature_angle=60.0),
        decimation=Decimation(method="quadric", target_ratio=0.35),
        min_voxel=100,
        seg_source="aparc.DKTatlas+aseg.deep.withCC.mgz",
    )
    d = p.to_params_dict()
    assert d["preprocess"] == {"method": "gaussian", "sigmaVox": 0.6}
    assert d["extractor"]["name"] == "vtk_surfacenets"
    assert d["smoothing"] == {
        "method": "taubin", "iterations": 20, "passBand": 0.1, "featureAngle": 60.0
    }
    assert d["decimation"] == {"method": "quadric", "targetRatio": 0.35}
    assert d["minVoxel"] == 100
    assert d["labelTable"] == "canonical-v1"
    assert d["segSource"] == "aparc.DKTatlas+aseg.deep.withCC.mgz"


def test_variant_id_format():
    """v<순번 2자리>-<해시 4자리> (스펙 §7)."""
    p = default_params()
    vid = p.variant_id(7)
    assert vid.startswith("v07-")
    assert len(vid) == len("v07-") + 4
    int(vid.split("-")[1], 16)


def test_variant_id_hash_is_deterministic_and_param_sensitive():
    a = default_params()
    b = MeshParams(
        preprocess=Preprocess(method="gaussian"),
        extractor=Extractor(),
        smoothing=Smoothing(),
        decimation=Decimation(),
    )
    assert a.param_hash() == default_params().param_hash()
    assert a.param_hash() != b.param_hash()


def test_index_changes_prefix_not_hash():
    """순번은 접두만 바꾸고 해시는 파라미터에만 달렸다."""
    p = default_params()
    assert p.variant_id(1).split("-")[1] == p.variant_id(2).split("-")[1]
