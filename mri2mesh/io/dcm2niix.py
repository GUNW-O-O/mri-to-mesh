"""dcm2niix 래핑 (스펙 §6.3).

dcm2niix는 입력 디렉터리를 재귀 탐색하고(-d로 깊이 조절) SeriesInstanceUID로
그룹핑한다. study/series1/*, study/series2/* 같은 중첩 구조가 그대로 처리된다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

import nibabel as nib

#: 표준에러가 길 때 예외 메시지에 담을 꼬리 길이
_STDERR_TAIL_CHARS = 2000

#: 출력 파일명 패턴 — %s 시리즈 번호, %d 시리즈 설명
_FILENAME_PATTERN = "%s_%d"

#: 등방성 판정 — 최대/최소 voxel 변 길이 비가 이 값 이하면 관통면 축이 없다고 본다.
#: series._MAX_ISOTROPY_RATIO와 같은 값이지만 의도적으로 따로 둔다. 이쪽은
#: "슬라이스 축을 못 고르는 경우"를 판정하고, 저쪽은 "T1 후보 가점"을 판정한다.
_ISOTROPY_RATIO = 1.2

#: %s_%d로 만든 파일명에서 시리즈 번호와 설명을 되찾는 패턴.
#: dcm2niix가 ADC 같은 파생 볼륨에는 사이드카를 만들지 않으므로,
#: 그때만 파일명에서 복구한다.
_STEM_PATTERN = re.compile(r"^(?P<number>\d+)_(?P<description>.+)$")


class Dcm2niixError(RuntimeError):
    """dcm2niix 실행 또는 결과 해석에 실패했다."""


@dataclass(frozen=True)
class SeriesOutput:
    """변환된 시리즈 하나."""

    nifti_path: Path
    sidecar_path: Path | None
    series_number: int | None
    series_description: str
    slices: int
    voxel_size_mm: tuple[float, float, float]
    acquisition_type: str


def find_dcm2niix() -> str:
    """dcm2niix 실행 파일 경로를 찾는다. DCM2NIIX_BIN이 PATH보다 우선한다."""
    override = os.environ.get("DCM2NIIX_BIN")
    if override:
        return override
    found = shutil.which("dcm2niix")
    if found:
        return found
    raise Dcm2niixError(
        "dcm2niix 실행 파일을 찾지 못했다. PATH에 넣거나 DCM2NIIX_BIN을 설정해야 한다."
    )


def _load_sidecar(sidecar_path: Path | None) -> dict:
    """BIDS 사이드카 JSON을 읽는다. 없거나 깨졌으면 빈 dict."""
    if sidecar_path is None or not sidecar_path.exists():
        return {}
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _slice_axis(zooms: tuple[float, float, float]) -> int:
    """슬라이스 축 인덱스를 고른다 — 표본 간격이 가장 성긴 축.

    dcm2niix는 볼륨을 재정렬해서 쓰므로 슬라이스 축이 항상 인덱스 2에
    오지 않는다. 실제 Philips MPRAGE는 shape=(170, 288, 288),
    zooms=(1.2, 0.89, 0.89)로 나오며 슬라이스 축이 인덱스 0이다.

    등방성이면 관통면 축이라는 개념이 없으므로 인덱스 2로 정한다.
    간격이 0이거나 음수인 축은 헤더가 불완전한 것이므로 후보에서 뺀다.
    """
    valid = [(z, i) for i, z in enumerate(zooms) if z > 0]
    if len(valid) < 3:
        return 2
    largest = max(z for z, _ in valid)
    smallest = min(z for z, _ in valid)
    if largest / smallest <= _ISOTROPY_RATIO:
        return 2
    # 동점이면 인덱스가 작은 쪽 — -i를 2차 키로 둬 결정론적으로 만든다.
    return max(valid, key=lambda zi: (zi[0], -zi[1]))[1]


def describe_nifti(nifti_path: Path, sidecar_path: Path | None) -> SeriesOutput:
    """NIfTI 헤더와 사이드카에서 시리즈 선택에 필요한 정보를 뽑는다.

    slices는 관통면 축(표본 간격이 가장 성긴 축)의 길이다. dcm2niix가 볼륨을
    재정렬하므로 그 축이 항상 인덱스 2는 아니다 — _slice_axis 참고.

    Raises:
        Dcm2niixError: NIfTI 파일이 잘렸거나(disk full, 강제 종료 등) 깨져서
            nibabel이 읽지 못할 때, 헤더의 축이 3개 미만일 때, 또는 사이드카의
            SeriesNumber가 정수로 해석되지 않을 때(스캐너가 만든 값이라
            신뢰할 수 없다).
    """
    meta = _load_sidecar(sidecar_path)
    try:
        img = nib.load(nifti_path)
    except (nib.filebasedimages.ImageFileError, OSError, EOFError, zlib.error) as exc:
        raise Dcm2niixError(
            f"NIfTI 파일을 읽지 못했다(잘렸거나 손상됨): {nifti_path}"
        ) from exc

    raw_zooms = img.header.get_zooms()
    if len(raw_zooms) < 3:
        raise Dcm2niixError(
            f"NIfTI 헤더의 축이 3개 미만이라 voxel_size_mm을 만들 수 없다: {nifti_path}"
        )
    zooms = tuple(float(z) for z in raw_zooms[:3])
    shape = img.shape
    slices = int(shape[_slice_axis(zooms)]) if len(shape) >= 3 else 0

    series_number = meta.get("SeriesNumber")
    if series_number is None:
        parsed_series_number = None
    else:
        try:
            parsed_series_number = int(series_number)
        except (TypeError, ValueError) as exc:
            raise Dcm2niixError(
                f"사이드카의 SeriesNumber를 정수로 해석하지 못했다: {series_number!r} "
                f"({nifti_path})"
            ) from exc

    return SeriesOutput(
        nifti_path=Path(nifti_path),
        sidecar_path=Path(sidecar_path) if sidecar_path else None,
        series_number=parsed_series_number,
        series_description=str(meta.get("SeriesDescription", "")),
        slices=slices,
        voxel_size_mm=zooms,
        acquisition_type=str(meta.get("MRAcquisitionType", "")),
    )


def _nifti_files(out_dir: Path) -> set[Path]:
    """out_dir 안에서 이름이 .nii 또는 .nii.gz로 '끝나는' 파일만 모은다.

    rglob("*.nii*")는 부분 문자열 매칭이라, 출력 파일명(%s_%d)에 그대로
    들어가는 SeriesDescription(자유 텍스트)에 따라 "5_T1.nii_repeat.json" 같은
    사이드카까지 집어삼킬 수 있다. 접미사 검사로 이를 막는다.
    """
    if not out_dir.is_dir():
        return set()
    return {
        path
        for path in out_dir.rglob("*")
        if path.is_file() and (path.name.endswith(".nii.gz") or path.name.endswith(".nii"))
    }


def _deepest_file_depth(root: Path) -> int | None:
    """root 바로 아래 파일을 깊이 0으로 두고, root 아래 파일 중 가장 깊은 상대 깊이를 돌려준다.

    파일이 하나도 없으면 None. dcm2niix의 -d(depth)와 같은 척도로 맞춰서,
    ingest.collect_dicom_files가 깊이 제한 없이 찾아낸 DICOM이 dcm2niix의
    재귀 한도보다 깊은 곳에 있었는지 진단하는 데 쓴다(모듈 docstring의
    깊이 결합 참고).
    """
    root = Path(root)
    deepest: int | None = None
    for dirpath, _dirnames, filenames in os.walk(root):
        if not filenames:
            continue
        depth = len(Path(dirpath).relative_to(root).parts)
        if deepest is None or depth > deepest:
            deepest = depth
    return deepest


def _describe_from_stem(stem: str) -> tuple[int | None, str]:
    """%s_%d 파일명 stem에서 (시리즈 번호, 설명)을 되찾는다.

    선두 숫자와 언더스코어가 없으면 번호는 None이고 stem 전체가 설명이다 —
    없는 번호를 지어내지 않는다.
    """
    match = _STEM_PATTERN.match(stem)
    if match is None:
        return None, stem
    return int(match.group("number")), match.group("description")


def _collect_outputs(nifti_paths: set[Path]) -> list[SeriesOutput]:
    """변환 결과 NIfTI들을 SeriesOutput으로 만든다.

    dcm2niix는 파생 볼륨(예: DTI의 ADC)에 사이드카를 만들지 않는다. 실제
    출력에서 1101_32DIR_3mm_1NSA.nii.gz에는 짝 .json이 있는데
    1101_32DIR_3mm_1NSA_ADC.nii.gz에는 없다. 그대로 두면 설명이 빈 문자열인
    이름 없는 항목이 시리즈 목록에 뜨는데, 스펙 §6.3은 목록에
    SeriesDescription을 표시하라고 요구한다. 사이드카가 없을 때만 파일명에서
    되찾는다 — 우리가 -f %s_%d로 만든 이름이므로 형식을 안다.
    """
    outputs: list[SeriesOutput] = []
    for nifti_path in sorted(nifti_paths):
        if nifti_path.name.endswith(".nii.gz"):
            stem = nifti_path.name[: -len(".nii.gz")]
        else:
            stem = nifti_path.stem
        sidecar = nifti_path.parent / f"{stem}.json"
        out = describe_nifti(nifti_path, sidecar if sidecar.exists() else None)
        if not out.series_description:
            number, description = _describe_from_stem(stem)
            out = replace(
                out,
                series_description=description,
                series_number=(
                    out.series_number if out.series_number is not None else number
                ),
            )
        outputs.append(out)
    return outputs


def _dcm2niix_cmd(exe: str, depth: int, out_dir, dicom_dir) -> list[str]:
    """dcm2niix 명령 줄을 만든다.

    Args:
        exe: dcm2niix 실행 파일 경로
        depth: 재귀 탐색 깊이
        out_dir: 출력 디렉터리
        dicom_dir: DICOM 입력 디렉터리

    Returns:
        dcm2niix에 전달할 명령 줄 목록
    """
    return [
        exe,
        "-d", str(depth),
        "-z", "y",
        "-b", "y",
        "-ba", "y",     # BIDS 사이드카 익명화 — 버전 기본값에 의존하지 않는다
        "-f", _FILENAME_PATTERN,
        "-o", str(out_dir),
        str(dicom_dir),
    ]


def run_dcm2niix(
    dicom_dir: Path,
    out_dir: Path,
    depth: int = 5,
    binary: str | None = None,
) -> list[SeriesOutput]:
    """DICOM 폴더를 시리즈별 .nii.gz + BIDS 사이드카로 변환한다.

    Args:
        dicom_dir: DICOM이 들어 있는 폴더. 하위 폴더도 depth까지 탐색한다.
            ingest.collect_dicom_files는 이 폴더를 채울 때 깊이 제한 없이
            훑으므로, depth보다 깊은 곳에 DICOM이 있으면 ingest 단계는 그
            파일들을 찾아내고도 dcm2niix는 보지 못하는 불일치가 생길 수
            있다 — 결과 NIfTI가 하나도 없을 때 이 함수가 그 가능성을
            진단해 오류 메시지에 덧붙인다.
        out_dir: 변환 결과를 둘 폴더. 없으면 만든다.
        depth: 재귀 탐색 깊이 (dcm2niix -d).
        binary: 실행 파일 경로. None이면 find_dcm2niix()로 찾는다.

    Raises:
        Dcm2niixError: 실행 실패, 종료 코드 비0, 또는 결과 NIfTI가 없을 때.
    """
    dicom_dir = Path(dicom_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # out_dir가 이전 실행 결과로 이미 차 있을 수 있으므로(mkdir은 비파괴적),
    # 실행 전 상태를 찍어 두고 이번 실행이 새로 만든 파일만 골라낸다.
    existing_files = _nifti_files(out_dir)
    exe = binary or find_dcm2niix()

    cmd = _dcm2niix_cmd(exe, depth, out_dir, dicom_dir)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise Dcm2niixError(f"dcm2niix 실행에 실패했다: {exc}") from exc

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-_STDERR_TAIL_CHARS:]
        raise Dcm2niixError(
            f"dcm2niix가 종료 코드 {result.returncode}로 끝났다.\n"
            f"명령: {' '.join(cmd)}\n표준에러 꼬리:\n{tail}"
        )

    new_files = _nifti_files(out_dir) - existing_files
    outputs = _collect_outputs(new_files)
    if not outputs:
        tail = (result.stdout or "")[-_STDERR_TAIL_CHARS:]
        depth_hint = ""
        deepest = _deepest_file_depth(dicom_dir)
        if deepest is not None and deepest > depth:
            depth_hint = (
                f"\n주의: dicom_dir 안 가장 깊은 파일이 depth={deepest}에 있는데 "
                f"-d {depth}로 실행했다. ingest.collect_dicom_files는 깊이 제한 "
                f"없이 DICOM을 찾아내지만 dcm2niix -d는 {depth}까지만 내려가므로, "
                f"이 깊이 초과가 '새 NIfTI 없음'의 실제 원인일 수 있다."
            )
        raise Dcm2niixError(
            f"dcm2niix가 새로운 NIfTI를 하나도 만들지 않았다.\n"
            f"명령: {' '.join(cmd)}\n표준출력 꼬리:\n{tail}{depth_hint}"
        )
    return outputs
