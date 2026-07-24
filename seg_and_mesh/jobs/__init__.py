"""잡 오케스트레이션 (스펙 §7, §9, §12).

단일 사용자·로컬. 잡은 폴더 + status.json 하나다. 파이프라인이 io·segment·
labels·mesh를 순서대로 호출한다.
"""

from seg_and_mesh.jobs.layout import JobPaths, job_paths, new_job_id
from seg_and_mesh.jobs.status import (
    STATES,
    JobStatus,
    read_status,
    record_error,
    sanitize_stderr,
    write_status,
)

__all__ = [
    "JobPaths",
    "job_paths",
    "new_job_id",
    "STATES",
    "JobStatus",
    "read_status",
    "record_error",
    "sanitize_stderr",
    "write_status",
]
