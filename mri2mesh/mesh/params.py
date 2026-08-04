"""메시 변형 파라미터 (스펙 §6.5, §7.1).

3축(전처리·추출·후처리) + min-voxel 노브. frozen dataclass라 변형 하나가
불변 값이고, 파라미터 해시로 variantId를 만든다(스펙 §7).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Preprocess:
    """축1: 마스크 안티에일리어싱 (스펙 §6.5)."""

    method: str = "none"  # none | gaussian | distance
    sigma_vox: float = 0.6


@dataclass(frozen=True)
class Extractor:
    """축2: 표면 추출기 (스펙 §6.5).

    options는 정렬된 튜플이라 frozen에 담긴다(dict는 unhashable).
    """

    name: str = "skimage_mc"
    options: tuple = ()


@dataclass(frozen=True)
class Smoothing:
    """축3-1: 스무딩 (스펙 §6.5)."""

    method: str = "none"  # none | laplacian | taubin | humphrey
    iterations: int = 20
    pass_band: float = 0.1
    feature_angle: float = 60.0
    relaxation: float = 0.1
    alpha: float = 0.1
    beta: float = 0.2


@dataclass(frozen=True)
class Decimation:
    """축3-2: 데시메이션 (스펙 §6.5)."""

    method: str = "none"  # none | quadric
    target_ratio: float = 0.35


@dataclass(frozen=True)
class MeshParams:
    """변형 하나의 전체 파라미터."""

    preprocess: Preprocess
    extractor: Extractor
    smoothing: Smoothing
    decimation: Decimation
    min_voxel: int = 100
    label_table: str = "canonical-v1"
    seg_source: str = ""

    def to_params_dict(self) -> dict:
        """스펙 §7.1 params.json의 파라미터부(camelCase).

        variantId·createdAt는 생성 시점 값이라 여기 없다 — generate가 붙인다.
        """
        pre = {"method": self.preprocess.method}
        if self.preprocess.method == "gaussian":
            pre["sigmaVox"] = self.preprocess.sigma_vox

        smo: dict = {"method": self.smoothing.method}
        if self.smoothing.method in ("laplacian", "taubin", "humphrey"):
            smo["iterations"] = self.smoothing.iterations
        if self.smoothing.method == "taubin":
            smo["passBand"] = self.smoothing.pass_band
            smo["featureAngle"] = self.smoothing.feature_angle
        if self.smoothing.method == "laplacian":
            smo["relaxation"] = self.smoothing.relaxation
        if self.smoothing.method == "humphrey":
            smo["alpha"] = self.smoothing.alpha
            smo["beta"] = self.smoothing.beta

        dec: dict = {"method": self.decimation.method}
        if self.decimation.method == "quadric":
            dec["targetRatio"] = self.decimation.target_ratio

        return {
            "preprocess": pre,
            "extractor": {"name": self.extractor.name, "options": dict(self.extractor.options)},
            "smoothing": smo,
            "decimation": dec,
            "minVoxel": self.min_voxel,
            "labelTable": self.label_table,
            "segSource": self.seg_source,
        }

    def param_hash(self) -> str:
        """파라미터 정준 JSON의 sha1 앞 4자리. segSource는 제외한다 —
        같은 파라미터면 입력 파일명이 달라도 같은 변형이다."""
        payload = self.to_params_dict()
        payload.pop("segSource", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:4]

    def variant_id(self, index: int) -> str:
        """v<순번 2자리>-<해시 4자리> (스펙 §7)."""
        return f"v{index:02d}-{self.param_hash()}"


def default_params() -> MeshParams:
    """전부 기본값 — 가장 보수적인 조합(전처리·스무딩·데시 없음, 기준선 추출기)."""
    return MeshParams(
        preprocess=Preprocess(),
        extractor=Extractor(),
        smoothing=Smoothing(),
        decimation=Decimation(),
    )


def baseline_params() -> MeshParams:
    """현재 프로덕션(brainds nifti_pipeline/mesh_export.py) 기본값.

    이진 마스크에 vtkContourFilter(0.5) → vtkSmoothPolyDataFilter(iter 30,
    relax 0.1). default_params()와 다르다 — 폼·엔드포인트·파이프라인 초기
    변형의 기준선은 이쪽이다.
    """
    return MeshParams(
        preprocess=Preprocess(method="none"),
        extractor=Extractor(name="vtk_contour_perlabel"),
        smoothing=Smoothing(method="laplacian", iterations=30, relaxation=0.1),
        decimation=Decimation(method="none"),
        min_voxel=100,
    )


def _num(payload: dict, key: str, default, lo: float, hi: float):
    """camelCase 키에서 수치를 읽고 [lo, hi]로 검증. 없으면 default."""
    if key not in payload:
        return default
    v = payload[key]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError(f"{key}: 숫자가 아니다")
    if not (lo <= v <= hi):
        raise ValueError(f"{key}: 범위를 벗어났다")
    return v


def _axis(payload: dict, key: str) -> dict:
    """payload[key]를 축 dict로 읽는다.

    없으면 {}(누락 축은 baseline로 채운다), dict면 그대로, dict가 아니면
    ValueError(잘못된 요청 — 입력값은 메시지에 담지 않는다).
    """
    if key not in payload:
        return {}
    v = payload[key]
    if not isinstance(v, dict):
        raise ValueError(f"{key}: 객체가 아니다")
    return v


def parse_mesh_params(payload: dict) -> MeshParams:
    """요청 dict(camelCase) → MeshParams. 누락 축은 baseline, 위반은 ValueError.

    메시지에 입력값을 그대로 넣지 않는다(PHI/로깅 안전).
    """
    from mri2mesh.mesh.extract import EXTRACTOR_NAMES

    if not isinstance(payload, dict):
        raise ValueError("payload: 객체가 아니다")

    base = baseline_params()
    payload = payload or {}

    # preprocess
    pre_in = _axis(payload, "preprocess")
    pre_method = pre_in.get("method", base.preprocess.method)
    if pre_method not in ("none", "gaussian", "distance"):
        raise ValueError("preprocess.method 화이트리스트 위반")
    pre = Preprocess(
        method=pre_method,
        sigma_vox=_num(pre_in, "sigmaVox", base.preprocess.sigma_vox, 0.1, 2.0),
    )

    # extractor
    ext_in = _axis(payload, "extractor")
    ext_name = ext_in.get("name", base.extractor.name)
    if ext_name not in EXTRACTOR_NAMES:
        raise ValueError("extractor.name 화이트리스트 위반")
    ext = Extractor(name=ext_name, options=())

    # smoothing
    smo_in = _axis(payload, "smoothing")
    smo_method = smo_in.get("method", base.smoothing.method)
    if smo_method not in ("none", "laplacian", "taubin", "humphrey"):
        raise ValueError("smoothing.method 화이트리스트 위반")
    smo = Smoothing(
        method=smo_method,
        iterations=int(_num(smo_in, "iterations", base.smoothing.iterations, 0, 100)),
        pass_band=_num(smo_in, "passBand", base.smoothing.pass_band, 0.0, 1.0),
        feature_angle=_num(smo_in, "featureAngle", base.smoothing.feature_angle, 0.0, 180.0),
        relaxation=_num(smo_in, "relaxation", base.smoothing.relaxation, 0.0, 1.0),
        alpha=_num(smo_in, "alpha", base.smoothing.alpha, 0.0, 1.0),
        beta=_num(smo_in, "beta", base.smoothing.beta, 0.0, 1.0),
    )

    # decimation
    dec_in = _axis(payload, "decimation")
    dec_method = dec_in.get("method", base.decimation.method)
    if dec_method not in ("none", "quadric"):
        raise ValueError("decimation.method 화이트리스트 위반")
    dec = Decimation(
        method=dec_method,
        target_ratio=_num(dec_in, "targetRatio", base.decimation.target_ratio, 0.05, 1.0),
    )

    return MeshParams(
        preprocess=pre, extractor=ext, smoothing=smo, decimation=dec,
        min_voxel=int(_num(payload, "minVoxel", base.min_voxel, 0, 5000)),
    )
