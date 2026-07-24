"""세그멘테이션 (스펙 §5.1, §6.4).

FastSurfer를 docker로 돌려 conform된 orig.nii.gz와 withCC 세그를 낸다.
엔진 인터페이스 하나에 FastSurfer를 구현체로 둔다(스펙 §10). remap·mesh는
downstream 별도 모듈이다.
"""

from seg_and_mesh.segment.fastsurfer import (
    SEG_SOURCE_FILE,
    SegmentError,
    SegmentResult,
    build_fastsurfer_command,
    run_fastsurfer,
)

__all__ = [
    "SEG_SOURCE_FILE",
    "SegmentError",
    "SegmentResult",
    "build_fastsurfer_command",
    "run_fastsurfer",
]
