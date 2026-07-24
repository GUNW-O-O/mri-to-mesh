"""입력 파일 종류 판별 (스펙 §6.1).

확장자를 신뢰하지 않는다. DICOM 파일명은 IM000001, I10, SOP Instance UID
문자열, 확장자 없음, .ima, .img 등 제각각이다. 매직바이트로 판별한다.

| 대상    | 판별                                                    |
|---------|---------------------------------------------------------|
| ZIP     | 첫 4바이트 PK\\x03\\x04                                   |
| gzip    | \\x1f\\x8b → 전개 후 아래 검사                            |
| NIfTI-1 | 첫 4바이트 int32 == 348 (양쪽 엔디안) + offset 344 magic |
| NIfTI-2 | sizeof_hdr == 540 + offset 4에 'n+2'                    |
| DICOM   | offset 128에 'DICM'                                     |

preamble 없는 DICOM은 'DICM'이 없으므로 pydicom 폴백으로 2단계 판별한다.

계약: 이 함수가 돌려주는 종류는 path에 있는 파일 그 자체를 gzip 압축 해제
없이 열었을 때의 형식이다. gzip은 유일한 예외로, 압축을 풀어 안의 내용을
검사하되 그 안이 NIfTI/DICOM일 때만 그 종류를 그대로 돌려준다. gzip 안에
ZIP이 들어 있으면(이중 압축) UNKNOWN을 돌려준다 — ZIP은 이미 압축 포맷이라
다시 gzip으로 감쌀 실제 이유가 없고, ingest.prepare_input은 이 함수가 돌려준
종류를 보고 압축을 풀지 않은 원본 경로를 그대로 zipfile.ZipFile에 넘기므로,
여기서 ZIP이라 판별해버리면 "판별된 스트림(내부)"과 "실제로 여는 파일(외부,
아직 gzip)"이 어긋나 zipfile.BadZipFile로 이어진다.
"""

from __future__ import annotations

import gzip
import struct
import warnings
import zlib
from enum import Enum
from pathlib import Path

import pydicom
from pydicom.tag import Tag

#: 판별에 읽는 선두 바이트. NIfTI-1 magic이 offset 344에 있으므로 348 이상이어야 한다.
PROBE_BYTES = 1024

_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"
_NIFTI1_MAGICS = (b"n+1\x00", b"ni1\x00")
_NIFTI2_MAGIC = b"n+2\x00"
_DICOM_PREAMBLE_OFFSET = 128
_DICOM_MAGIC = b"DICM"

#: 폴백 판정에 필요한 태그. 이 셋만 읽어 pydicom이 나머지 element의 값을
#: 물화하지 않게 한다. force=True로 쓰레기 바이트를 읽으면 pydicom이 엉뚱한
#: 값을 element 길이로 해석해 거대한 값을 통째로 읽어들이는데, 그게 파일당
#: 0.15~0.5초를 먹던 원인이었다(실측: 26개 파일 6.22s → 0.01s, 판정 동일).
_FALLBACK_TAGS = [
    Tag(0x0008, 0x0016),  # SOPClassUID
    Tag(0x0028, 0x0010),  # Rows
    Tag(0x0028, 0x0011),  # Columns
]


class InputKind(str, Enum):
    """판별된 입력 종류.

    주의: ingest.SourceKind와 값을 공유한다(둘 다 "nifti"). str Enum이라
    InputKind.NIFTI == SourceKind.NIFTI가 True로 나오지만, 두 Enum은 서로
    무관한 타입 도메인이다 — 반드시 `is`로 비교해야 한다.
    """

    ZIP = "zip"
    NIFTI = "nifti"
    DICOM = "dicom"
    UNKNOWN = "unknown"


def _read_int32(head: bytes, endian: str) -> int | None:
    if len(head) < 4:
        return None
    return struct.unpack(endian + "i", head[:4])[0]


def _is_zip(head: bytes) -> bool:
    return head[:4] == _ZIP_MAGIC


def _is_gzip(head: bytes) -> bool:
    return head[:2] == _GZIP_MAGIC


def _is_nifti(head: bytes) -> bool:
    """NIfTI-1(348) / NIfTI-2(540) 헤더인지 확인한다. 양쪽 엔디안을 모두 본다."""
    for endian in ("<", ">"):
        sizeof_hdr = _read_int32(head, endian)
        if sizeof_hdr == 348 and len(head) >= 348 and head[344:348] in _NIFTI1_MAGICS:
            return True
        if sizeof_hdr == 540 and len(head) >= 8 and head[4:8] == _NIFTI2_MAGIC:
            return True
    return False


def _is_dicom_preamble(head: bytes) -> bool:
    end = _DICOM_PREAMBLE_OFFSET + len(_DICOM_MAGIC)
    return len(head) >= end and head[_DICOM_PREAMBLE_OFFSET:end] == _DICOM_MAGIC


def _is_dicom_fallback(path: Path) -> bool:
    """preamble 없는 DICOM을 pydicom으로 판별한다.

    force=True는 임의 바이너리에도 빈 Dataset을 돌려주므로, 실제 DICOM 태그가
    있는지를 반드시 확인해야 한다.

    pydicom이 내는 경고는 여기서 삼킨다. 밖으로 흘리면 -W error 환경에서
    경고가 예외가 되고 아래 except Exception이 그것을 삼켜, 같은 파일이
    테스트에서는 UNKNOWN, 운영에서는 DICOM으로 갈린다.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = pydicom.dcmread(
                path,
                force=True,
                stop_before_pixels=True,
                specific_tags=_FALLBACK_TAGS,
            )
    except Exception:
        return False
    if "SOPClassUID" in ds:
        return True
    return "Rows" in ds and "Columns" in ds


def detect_format_bytes(head: bytes) -> InputKind | None:
    """선두 바이트만으로 판별한다. 확정하지 못하면 None을 돌려준다.

    gzip은 여기서 판정하지 않는다. 전개가 필요하므로 detect_format이 처리한다.
    """
    if _is_zip(head):
        return InputKind.ZIP
    if _is_nifti(head):
        return InputKind.NIFTI
    if _is_dicom_preamble(head):
        return InputKind.DICOM
    return None


def detect_format(path: Path) -> InputKind:
    """파일 하나의 종류를 판별한다.

    gzip 안에 ZIP이 들어 있으면(이중 압축) UNKNOWN을 돌려준다 — 모듈
    docstring의 "계약" 절 참고. 이 조합은 지원 대상이 아니다.

    Raises:
        IsADirectoryError: path가 폴더일 때.
        FileNotFoundError: path가 없을 때.
    """
    path = Path(path)
    if path.is_dir():
        raise IsADirectoryError(f"파일이 아니라 폴더다: {path}")

    with open(path, "rb") as fh:
        head = fh.read(PROBE_BYTES)

    if _is_gzip(head):
        try:
            with gzip.open(path, "rb") as gz:
                inner = gz.read(PROBE_BYTES)
        except (OSError, EOFError, zlib.error):
            return InputKind.UNKNOWN
        inner_kind = detect_format_bytes(inner)
        if inner_kind is InputKind.ZIP:
            # 이중 압축(gzip 안의 ZIP)은 지원하지 않는다 — 모듈 docstring 참고.
            return InputKind.UNKNOWN
        return inner_kind or InputKind.UNKNOWN

    kind = detect_format_bytes(head)
    if kind is not None:
        return kind

    if _is_dicom_fallback(path):
        return InputKind.DICOM
    return InputKind.UNKNOWN
