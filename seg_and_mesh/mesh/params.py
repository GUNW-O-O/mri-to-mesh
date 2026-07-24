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
