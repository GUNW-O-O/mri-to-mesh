"""테스트 공통 fixture."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def real_data_dir() -> Path:
    """실제 MRI 테스트 데이터 폴더. 환경 변수 MRI2MESH_TEST_DATA_DIR로 지정한다."""
    raw = os.environ.get("MRI2MESH_TEST_DATA_DIR")
    if not raw:
        pytest.skip("MRI2MESH_TEST_DATA_DIR 미설정 — 실제 데이터 테스트를 건너뛴다")
    path = Path(raw)
    if not path.is_dir():
        pytest.skip(f"MRI2MESH_TEST_DATA_DIR가 폴더가 아니다: {path}")
    return path


@pytest.fixture
def dcm2niix_bin() -> str:
    """dcm2niix 실행 파일 경로. 없으면 skip."""
    found = os.environ.get("DCM2NIIX_BIN") or shutil.which("dcm2niix")
    if not found:
        pytest.skip("dcm2niix 바이너리를 찾지 못했다 — PATH 또는 DCM2NIIX_BIN 설정 필요")
    return found
