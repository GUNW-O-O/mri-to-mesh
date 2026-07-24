"""메시 엔진 (스펙 §6.5~§6.7, §7.1, §7.2).

canonical seg.nii.gz + 파라미터 조합 -> 변형 하나(GLB + 지표). 도커·웹 없이
로컬에서 전 구간 테스트된다.
"""

from seg_and_mesh.mesh.params import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)
from seg_and_mesh.mesh.extract import EXTRACTOR_NAMES, ExtractError, extract
from seg_and_mesh.mesh.generate import GenerateError, VariantResult, generate_variant
from seg_and_mesh.mesh.glb import GlbError, write_glb
from seg_and_mesh.mesh.metrics import region_metrics, summarize
from seg_and_mesh.mesh.postprocess import PostprocessError, decimate, smooth
from seg_and_mesh.mesh.preprocess import apply_preprocess

__all__ = [
    "Decimation",
    "Extractor",
    "MeshParams",
    "Preprocess",
    "Smoothing",
    "default_params",
    "apply_preprocess",
    "EXTRACTOR_NAMES",
    "ExtractError",
    "extract",
    "PostprocessError",
    "decimate",
    "smooth",
    "GlbError",
    "write_glb",
    "region_metrics",
    "summarize",
    "GenerateError",
    "VariantResult",
    "generate_variant",
]
