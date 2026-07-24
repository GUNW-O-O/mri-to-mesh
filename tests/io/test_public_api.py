"""io 패키지 공개 API 계약.

뒤 계획(jobs/, web/)은 이 이름들만 쓴다. 내부 모듈 경로에 묶이지 않게 한다.
"""

import seg_and_mesh.io as sam_io

EXPECTED = {
    "InputKind",
    "detect_format",
    "ExtractLimits",
    "ExtractResult",
    "UnsafeArchiveError",
    "safe_extract",
    "SourceKind",
    "PreparedInput",
    "UnsupportedInputError",
    "prepare_input",
    "SeriesOutput",
    "Dcm2niixError",
    "run_dcm2niix",
    "describe_nifti",
    "find_dcm2niix",
    "SeriesCandidate",
    "rank_series",
    "score_series",
}


def test_all_names_are_exported():
    assert set(sam_io.__all__) == EXPECTED


def test_all_names_are_importable():
    for name in EXPECTED:
        assert hasattr(sam_io, name), f"{name}이 노출되지 않았다"


def test_source_kind_values_match_status_json_contract():
    """스펙 §9.1의 status.json input.kind 값과 일치해야 한다."""
    assert sam_io.SourceKind.DICOM_ZIP.value == "dicom-zip"
    assert sam_io.SourceKind.DICOM_DIR.value == "dicom-dir"
    assert sam_io.SourceKind.NIFTI.value == "nifti"
