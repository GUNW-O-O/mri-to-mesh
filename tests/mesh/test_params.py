"""파라미터 모델과 variantId (스펙 §7, §7.1)."""

from __future__ import annotations

import pytest

from mri2mesh.mesh import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    baseline_params,
    default_params,
)
from mri2mesh.mesh.params import parse_mesh_params


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


def test_baseline_params_matches_production():
    p = baseline_params()
    assert p.preprocess.method == "none"
    assert p.extractor.name == "vtk_contour_perlabel"
    assert p.smoothing.method == "laplacian"
    assert p.smoothing.iterations == 30
    assert p.smoothing.relaxation == 0.1
    assert p.decimation.method == "none"
    assert p.min_voxel == 100
    # 파라미터 해시가 안정적이어야 변형 중복 판정이 일관된다
    assert p.variant_id(1) == f"v01-{p.param_hash()}"


def test_parse_fills_missing_axes_from_baseline():
    p = parse_mesh_params({"smoothing": {"method": "taubin", "iterations": 10,
                                         "passBand": 0.2, "featureAngle": 45}})
    # 준 축은 반영
    assert p.smoothing.method == "taubin"
    assert p.smoothing.iterations == 10
    # 안 준 축은 baseline
    assert p.extractor.name == baseline_params().extractor.name
    assert p.min_voxel == 100


def test_parse_rejects_unknown_method():
    with pytest.raises(ValueError):
        parse_mesh_params({"smoothing": {"method": "wobble"}})


def test_parse_rejects_unknown_extractor():
    with pytest.raises(ValueError):
        parse_mesh_params({"extractor": {"name": "not_a_real_extractor"}})


def test_parse_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_mesh_params({"minVoxel": 99999})
    with pytest.raises(ValueError):
        parse_mesh_params({"decimation": {"method": "quadric", "targetRatio": 5.0}})


def test_parse_rejects_non_dict_axis():
    """축이 존재하지만 dict가 아니면(예: 문자열) ValueError — AttributeError로
    새 나가면 라우트에서 500이 된다(리뷰 발견 #1)."""
    with pytest.raises(ValueError):
        parse_mesh_params({"preprocess": "x"})
    with pytest.raises(ValueError):
        parse_mesh_params({"extractor": 5})
    with pytest.raises(ValueError):
        parse_mesh_params({"smoothing": [1, 2]})
    with pytest.raises(ValueError):
        parse_mesh_params({"decimation": "nope"})


def test_parse_rejects_non_dict_payload():
    """최상위 payload 자체가 dict가 아니어도 ValueError(500이 아니라)."""
    with pytest.raises(ValueError):
        parse_mesh_params("nope")
