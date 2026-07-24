"""canonical 라벨 표와 정적 리맵 (스펙 §3).

FastSurfer가 낸 불연속 FreeSurfer 번호를 입력과 무관하게 고정된 조밀 번호로
바꾼다. 도커·FastSurfer에 의존하지 않으므로 로컬에서 전 구간 테스트된다.
"""

from seg_and_mesh.labels.remap import (
    RemapError,
    RemapResult,
    build_lookup,
    remap_segmentation,
)
from seg_and_mesh.labels.table import (
    CANONICAL_VERSION,
    CanonicalTable,
    LabelEntry,
    LabelTableError,
    load_canonical,
)

__all__ = [
    # table
    "CANONICAL_VERSION",
    "CanonicalTable",
    "LabelEntry",
    "LabelTableError",
    "load_canonical",
    # remap
    "RemapError",
    "RemapResult",
    "build_lookup",
    "remap_segmentation",
]
