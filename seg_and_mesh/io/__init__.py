"""입력 판별, ZIP 안전 해제, dcm2niix 래핑, 시리즈 추천 (스펙 §6.1~§6.3).

파이프라인 1~4단계를 담당한다. 도커·FastSurfer·잡 큐에 의존하지 않으므로
로컬에서 전 구간 테스트된다.
"""

from seg_and_mesh.io.archive import (
    ExtractLimits,
    ExtractResult,
    UnsafeArchiveError,
    safe_extract,
)
from seg_and_mesh.io.dcm2niix import (
    Dcm2niixError,
    SeriesOutput,
    describe_nifti,
    find_dcm2niix,
    run_dcm2niix,
)
from seg_and_mesh.io.detect import InputKind, detect_format
from seg_and_mesh.io.ingest import (
    PreparedInput,
    SourceKind,
    UnsupportedInputError,
    prepare_input,
)
from seg_and_mesh.io.series import SeriesCandidate, rank_series, score_series

__all__ = [
    # detect
    "InputKind",
    "detect_format",
    # archive
    "ExtractLimits",
    "ExtractResult",
    "UnsafeArchiveError",
    "safe_extract",
    # ingest
    "SourceKind",
    "PreparedInput",
    "UnsupportedInputError",
    "prepare_input",
    # dcm2niix
    "SeriesOutput",
    "Dcm2niixError",
    "run_dcm2niix",
    "describe_nifti",
    "find_dcm2niix",
    # series
    "SeriesCandidate",
    "rank_series",
    "score_series",
]
