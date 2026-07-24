"""업로드 입력 하나를 파이프라인이 쓸 형태로 정규화한다 (스펙 §6.1).

ZIP 내부 파일은 전부 매직바이트 검사를 거쳐 DICOM만 추린다. 파일명은 보지 않는다.
"""

from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from seg_and_mesh.io.archive import ExtractLimits, safe_extract
from seg_and_mesh.io.detect import InputKind, detect_format

#: ZIP 전개 결과를 두는 workdir 하위 폴더명
_EXTRACT_DIRNAME = "extracted"


class SourceKind(str, Enum):
    """정규화된 입력 종류. status.json의 input.kind 값과 같다 (스펙 §9.1).

    주의: detect.InputKind와 값을 공유한다(둘 다 "nifti"). str Enum이라
    SourceKind.NIFTI == InputKind.NIFTI가 True로 나오지만, 두 Enum은 서로
    무관한 타입 도메인이다 — 반드시 `is`로 비교해야 한다. status.json 계약
    때문에 이 값들은 바꿀 수 없다.
    """

    DICOM_ZIP = "dicom-zip"
    DICOM_DIR = "dicom-dir"
    NIFTI = "nifti"


class UnsupportedInputError(Exception):
    """파이프라인이 처리할 수 없는 입력이다."""


@dataclass(frozen=True)
class PreparedInput:
    """정규화 결과.

    DICOM 입력이면 dicom_dir/dicom_files가 채워지고 nifti_file은 None이다.
    NIfTI 입력이면 반대다.
    """

    kind: SourceKind
    dicom_dir: Path | None
    dicom_files: list[Path]
    nifti_file: Path | None


def collect_dicom_files(root: Path) -> list[Path]:
    """root 아래 모든 파일을 매직바이트로 검사해 DICOM만 정렬해 돌려준다.

    심볼릭 링크로 연결된 디렉터리는 내려가지 않는다 — 자기 자신을 가리키는
    링크가 있어도 순회가 무한히 반복되지 않게 하기 위해서다.

    깊이 제한 없이 재귀한다. dcm2niix.run_dcm2niix는 -d(기본 5)로 재귀
    깊이를 제한하므로, 여기서 depth 5보다 깊은 곳의 DICOM을 찾아 반환해도
    dcm2niix는 그 파일들을 보지 못할 수 있다 — 이 두 함수의 깊이는 서로
    독립적으로 정해진다는 뜻이다. 이 불일치로 결과 NIfTI가 하나도 나오지
    않으면 run_dcm2niix가 dicom_dir의 실제 최대 깊이를 오류 메시지에
    덧붙여 진단을 돕는다(dcm2niix.py의 _deepest_file_depth 참고).
    """
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if detect_format(path) is InputKind.DICOM:
                found.append(path)
    return sorted(found)


def _nifti_target_name(src: Path) -> str:
    """gzip이면 input.nii.gz, 아니면 input.nii."""
    with open(src, "rb") as fh:
        is_gzip = fh.read(2) == b"\x1f\x8b"
    return "input.nii.gz" if is_gzip else "input.nii"


def _prepare_nifti(src: Path, workdir: Path) -> PreparedInput:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / _nifti_target_name(src)
    shutil.copyfile(src, target)
    return PreparedInput(
        kind=SourceKind.NIFTI, dicom_dir=None, dicom_files=[], nifti_file=target
    )


def _prepare_zip(src: Path, workdir: Path, limits: ExtractLimits) -> PreparedInput:
    extract_dir = workdir / _EXTRACT_DIRNAME
    safe_extract(src, extract_dir, limits)
    dicom_files = collect_dicom_files(extract_dir)
    if not dicom_files:
        # archive.safe_extract이 실패 시 자신이 소유한 dest_root를 지우는 것과
        # 같은 규율이다 — 거부된 입력의 전개물을 workdir에 남기지 않아야
        # 같은 workdir로 재시도할 수 있다.
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UnsupportedInputError(f"ZIP 안에 DICOM 파일이 하나도 없다: {src}")
    return PreparedInput(
        kind=SourceKind.DICOM_ZIP,
        dicom_dir=extract_dir,
        dicom_files=dicom_files,
        nifti_file=None,
    )


def _prepare_dir(src: Path) -> PreparedInput:
    dicom_files = collect_dicom_files(src)
    if not dicom_files:
        raise UnsupportedInputError(f"폴더 안에 DICOM 파일이 하나도 없다: {src}")
    return PreparedInput(
        kind=SourceKind.DICOM_DIR,
        dicom_dir=src,
        dicom_files=dicom_files,
        nifti_file=None,
    )


def prepare_input(
    src: Path,
    workdir: Path,
    limits: ExtractLimits = ExtractLimits(),
) -> PreparedInput:
    """업로드 파일 또는 폴더 하나를 정규화한다.

    detect_format이 gzip 안에 ZIP이 든 이중 압축을 UNKNOWN으로 판별하므로
    (detect.py 참고), 그런 입력은 여기서 "판별 실패"로 거부된다.

    Raises:
        UnsupportedInputError: 판별 실패(이중 압축 gzip+ZIP 포함), DICOM 없음,
            단일 DICOM 파일일 때.
        UnsafeArchiveError: ZIP이 안전 검사에 걸렸을 때, 또는 ZIP이 손상되었거나
            올바른 ZIP 형식이 아닐 때.
    """
    src = Path(src)
    workdir = Path(workdir)

    if src.is_dir():
        return _prepare_dir(src)

    kind = detect_format(src)
    if kind is InputKind.ZIP:
        return _prepare_zip(src, workdir, limits)
    if kind is InputKind.NIFTI:
        return _prepare_nifti(src, workdir)
    if kind is InputKind.DICOM:
        raise UnsupportedInputError(
            f"DICOM 한 장으로는 볼륨을 만들 수 없다. 시리즈 폴더나 ZIP을 넣어야 한다: {src}"
        )
    raise UnsupportedInputError(f"입력 종류를 판별하지 못했다: {src}")
