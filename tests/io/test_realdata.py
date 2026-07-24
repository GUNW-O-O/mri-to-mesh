"""실제 MRI 스터디 회귀 테스트.

SAM_TEST_DATA_DIR이 가리키는 폴더에 같은 스터디의 DICOM 폴더 하나와 ZIP
하나가 있다고 본다. 파일명에 환자 식별자가 들어 있을 수 있으므로 어떤
이름도 이 파일에 적지 않는다 — 레이아웃을 찾아서 쓴다.

전부 realdata 표시가 붙어 있다. 데이터가 없으면 conftest의 fixture가 skip한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seg_and_mesh.io import (
    SourceKind,
    prepare_input,
    rank_series,
    run_dcm2niix,
)

pytestmark = pytest.mark.realdata


@pytest.fixture
def study_dir(real_data_dir: Path) -> Path:
    """스터디 DICOM이 든 폴더 하나."""
    subdirs = [p for p in sorted(real_data_dir.iterdir()) if p.is_dir()]
    if not subdirs:
        pytest.skip(f"SAM_TEST_DATA_DIR 안에 하위 폴더가 없다: {real_data_dir}")
    return subdirs[0]


@pytest.fixture
def study_zip(real_data_dir: Path) -> Path:
    """같은 스터디를 담은 ZIP 하나."""
    zips = sorted(real_data_dir.glob("*.zip"))
    if not zips:
        pytest.skip(f"SAM_TEST_DATA_DIR 안에 .zip이 없다: {real_data_dir}")
    return zips[0]


def test_directory_input_finds_dicom(study_dir, tmp_path):
    """폴더 입력이 dicom-dir로 판별되고 DICOM을 찾아낸다."""
    prepared = prepare_input(study_dir, tmp_path)

    assert prepared.kind is SourceKind.DICOM_DIR
    assert len(prepared.dicom_files) > 0


def test_zip_input_finds_dicom(study_zip, tmp_path):
    """ZIP 입력이 dicom-zip으로 판별되고 안전 해제를 통과한다.

    실제 157MB / 84엔트리 ZIP이 ExtractLimits 기본값에 걸리지 않아야 한다.
    """
    prepared = prepare_input(study_zip, tmp_path)

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) > 0


def test_zip_and_directory_agree(study_dir, study_zip, tmp_path):
    """같은 스터디면 두 경로가 같은 DICOM 파일 집합을 내놓아야 한다.

    한쪽만 깨지는 회귀(ZIP 해제 누락, 심볼릭 링크 처리 차이 등)를 잡는다.
    """
    from_dir = prepare_input(study_dir, tmp_path / "dir")
    from_zip = prepare_input(study_zip, tmp_path / "zip")

    names_dir = sorted(p.name for p in from_dir.dicom_files)
    names_zip = sorted(p.name for p in from_zip.dicom_files)

    assert names_dir == names_zip


@pytest.mark.dcm2niix
def test_conversion_and_ranking_pick_a_3d_t1(study_dir, tmp_path, dcm2niix_bin):
    """변환 후 순위 1위가 FastSurfer에 넣을 만한 3D T1이어야 한다.

    이 스터디에는 함정이 들어 있다 — 3D MPRAGE 옆에 2D T1 SE 시상면이
    있고 둘 다 설명이 T1 패턴에 걸린다. 2D 쪽이 1위가 되면 FastSurfer가
    조용히 틀린 결과를 낸다(스펙 §6.3).

    특정 시리즈 이름을 단언하지 않는다 — 데이터가 바뀌어도 의미가 유지되게
    성질로 단언한다.
    """
    from seg_and_mesh.io.series import T1_DESCRIPTION_PATTERN

    outputs = run_dcm2niix(study_dir, tmp_path / "nifti", binary=dcm2niix_bin)
    assert len(outputs) > 1, "시리즈가 하나뿐이면 순위를 검증할 수 없다"

    ranked = rank_series(outputs)
    top = ranked[0].series

    assert T1_DESCRIPTION_PATTERN.search(top.series_description), (
        f"1위 설명이 T1 패턴에 안 걸린다: {top.series_description!r}"
    )
    assert top.acquisition_type.upper() == "3D", (
        f"1위가 3D 획득이 아니다: {top.acquisition_type!r}"
    )
    assert top.slices >= 128, f"1위 슬라이스가 128장 미만이다: {top.slices}"
    assert ranked[0].score > ranked[1].score, (
        "1위와 2위가 동점이다 — 함정 시리즈와 구별되지 않는다"
    )


@pytest.mark.dcm2niix
def test_every_output_has_a_description(study_dir, tmp_path, dcm2niix_bin):
    """변환 결과에 이름 없는 항목이 있으면 안 된다.

    dcm2niix는 파생 볼륨(DTI의 ADC 등)에 사이드카를 만들지 않는다.
    그래도 목록에는 이름이 있어야 한다(스펙 §6.3).
    """
    outputs = run_dcm2niix(study_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    nameless = [o.nifti_path.name for o in outputs if not o.series_description]
    assert nameless == [], f"설명이 빈 항목이 있다: {nameless}"
