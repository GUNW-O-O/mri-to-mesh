"""uvicorn 진입점 — 환경변수에서 앱을 만든다 (스펙 §11)."""

from __future__ import annotations

import os
from pathlib import Path

from seg_and_mesh.web.app import AppConfig, create_app


def _config() -> AppConfig:
    image = os.environ.get("FASTSURFER_IMAGE")
    if not image:
        raise RuntimeError("FASTSURFER_IMAGE가 필요하다(.env, latest 금지)")
    return AppConfig(
        jobs_root=Path(os.environ.get("SAM_JOBS_ROOT", "/work/jobs")),
        fastsurfer_image=image,
        threads=int(os.environ.get("FASTSURFER_THREADS", "8")),
        # 형제 FastSurfer 컨테이너의 -v 인자를 호스트 경로로 바꾸는 데 쓴다
        # (Task 1 to_host_path). 없으면(호스트에서 uvicorn 직접 실행) None —
        # jobs_root 자체가 이미 호스트 경로이므로 변환하지 않는다.
        host_jobs_root=(
            Path(os.environ["SAM_HOST_JOBS_ROOT"])
            if os.environ.get("SAM_HOST_JOBS_ROOT")
            else None
        ),
    )


app = create_app(_config())
