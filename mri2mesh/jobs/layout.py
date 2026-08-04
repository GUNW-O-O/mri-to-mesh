"""잡 디렉터리 레이아웃 (스펙 §7).

단일 사용자·로컬이라 잡은 폴더 하나다. 큐·DB 없음.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def new_job_id(now: datetime | None = None) -> str:
    """YYYY-MM-DD-HHMMSS-<hex4> (스펙 §9.1)."""
    now = now or datetime.now()
    return f"{now:%Y-%m-%d-%H%M%S}-{secrets.token_hex(2)}"


@dataclass(frozen=True)
class JobPaths:
    """잡 하나의 경로들 (스펙 §7)."""

    root: Path

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def nifti_dir(self) -> Path:
        return self.root / "nifti"

    @property
    def fs_dir(self) -> Path:
        return self.root / "fs"

    @property
    def seg_dir(self) -> Path:
        return self.root / "seg"

    @property
    def mesh_dir(self) -> Path:
        return self.root / "mesh"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def status_file(self) -> Path:
        return self.root / "status.json"

    def variant_dir(self, variant_id: str) -> Path:
        return self.mesh_dir / variant_id

    def create(self) -> "JobPaths":
        for d in (self.input_dir, self.nifti_dir, self.fs_dir,
                  self.seg_dir, self.mesh_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def job_paths(jobs_root: Path, job_id: str) -> JobPaths:
    return JobPaths(root=Path(jobs_root) / job_id)


def to_host_path(path: Path, *, jobs_root: Path, host_jobs_root: Path | None) -> Path:
    """컨테이너 경로 -> 호스트 경로 (형제 컨테이너 -v 인자용).

    도커 데몬은 호스트에 있어 api 컨테이너 내부 경로를 모른다. compose가
    host_jobs_root를 jobs_root에 바인드했다는 전제로 접두사를 바꾼다.
    host_jobs_root가 None이면(호스트 직접 실행) 그대로 둔다.
    """
    path = Path(path)
    if host_jobs_root is None:
        return path
    rel = path.relative_to(jobs_root)   # 밖이면 ValueError
    return Path(host_jobs_root) / rel
