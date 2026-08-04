"""DICOM メタデータ before/after 管理 (익명화 감사).

before는 로컬 전용이다 — 원본 환자 식별자를 담으므로 git·HTTP status로 절대
내보내지 않는다(전용 dicom-meta 엔드포인트로만). 테스트는 가짜 메타로만.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pydicom


class DicomMetaError(RuntimeError):
    """DICOM 헤더를 읽지 못했다."""


def _json_safe(value):
    """pydicom element 값을 json 직렬화 가능한 형태로. 못 담을 값은 str."""
    from pydicom.multival import MultiValue
    from pydicom.valuerep import PersonName
    if isinstance(value, PersonName):
        return str(value)
    if isinstance(value, MultiValue):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)


def read_dicom_header(path) -> dict:
    """대표 DICOM 헤더를 {keyword: json-safe value}로. PixelData·private 제외.

    Raises:
        DicomMetaError: 파일을 DICOM으로 읽지 못했을 때.
    """
    try:
        ds = pydicom.dcmread(Path(path), stop_before_pixels=True, force=True)
    except Exception as exc:  # noqa: BLE001 — pydicom이 내는 예외 종류가 넓다
        raise DicomMetaError("DICOM 헤더를 읽지 못했다") from exc
    # force=True는 비-DICOM도 빈 Dataset으로 읽을 수 있다 — 식별 element가 하나도
    # 없으면 DICOM이 아니라고 본다.
    out: dict = {}
    for elem in ds:
        if elem.keyword in ("", "PixelData"):
            continue
        out[elem.keyword] = _json_safe(elem.value)
    if not out:
        raise DicomMetaError("DICOM 헤더에서 읽을 element가 없다")
    return out
