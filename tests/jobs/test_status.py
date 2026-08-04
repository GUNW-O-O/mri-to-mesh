"""status.json 상태 모델 + PHI-안전 에러 (스펙 §9.1, §12)."""

from __future__ import annotations

import json

import pytest

from mri2mesh.jobs.layout import job_paths
from mri2mesh.jobs.status import (
    JobStatus,
    read_status,
    record_error,
    sanitize_stderr,
    write_status,
)


def _new_status():
    return JobStatus(
        job_id="job1", case_name="case1",
        created_at="2026-07-22T14:30:11Z", updated_at="2026-07-22T14:30:11Z",
        state="awaiting_series", step="io",
        input={"filename": "study.zip", "kind": "dicom-zip", "bytes": 100},
        series=[], selected_series=None, engine=None, variants=[], error=None,
    )


def test_write_then_read_round_trips(tmp_path):
    p = job_paths(tmp_path, "job1").create()
    write_status(p, _new_status())
    s = read_status(p)
    assert s.job_id == "job1"
    assert s.state == "awaiting_series"


def test_write_updates_timestamp(tmp_path):
    p = job_paths(tmp_path, "job1").create()
    s = _new_status()
    write_status(p, s)
    raw = json.loads(p.status_file.read_text(encoding="utf-8"))
    assert "updatedAt" in raw
    assert raw["jobId"] == "job1"
    # camelCase 계약
    assert "caseName" in raw and "selectedSeries" in raw


def test_record_error_sets_state_and_keeps_no_filename(tmp_path):
    """에러에 입력 경로/파일명이 들어가면 PHI 유출(스펙 §12)."""
    p = job_paths(tmp_path, "job1").create()
    write_status(p, _new_status())
    record_error(
        p, step="segment", returncode=1,
        stderr_tail="/work/jobs/job1/nifti/Hong_Gil_Dong_MPRAGE.nii.gz not found",
    )
    s = read_status(p)
    assert s.state == "error"
    assert s.error["step"] == "segment"
    assert s.error["returncode"] == 1
    assert "Hong_Gil_Dong" not in json.dumps(s.error)


def test_sanitize_masks_paths_and_filenames():
    out = sanitize_stderr("failed at /work/jobs/x/nifti/Patient_Name_T1.nii.gz line 3")
    assert "Patient_Name" not in out
    assert "/work/jobs" not in out
    assert "line 3" in out  # 진단 정보는 남긴다


def test_sanitize_masks_windows_path_with_space():
    """폴더명에 공백이 있어도(예: "Program Files") 잡되, 확장자에서 멈춰
    뒤따르는 진단 문구는 남긴다."""
    out = sanitize_stderr(r"C:\Users\Hong Gil Dong\Desktop\scan.dcm line 3")
    assert "Hong" not in out and "Gil" not in out and "Dong" not in out
    assert r"C:\Users" not in out
    assert "line 3" in out

    out2 = sanitize_stderr(r"C:\Program Files\FreeSurfer\bin\mri_convert.exe failed")
    assert r"C:\Program Files" not in out2
    assert "failed" in out2


def test_sanitize_masks_unc_path():
    out = sanitize_stderr(r"\\SERVER\share\Hong_Gil_Dong\scan.dcm not found")
    assert "Hong_Gil_Dong" not in out
    assert r"\\SERVER" not in out
    assert "not found" in out


def test_sanitize_masks_relative_path_including_leading_segment():
    """상대경로는 첫 세그먼트까지 통째로 마스킹한다 — 그 세그먼트가 환자
    식별자일 수 있다."""
    out = sanitize_stderr("cannot read Hong_Gil_Dong/orig.mgz")
    assert "Hong_Gil_Dong" not in out
    assert "cannot read" in out


def test_sanitize_masks_bare_filename_with_any_extension():
    """확장자 화이트리스트가 없다 — dcm2niix는 SeriesDescription으로
    사이드카 이름을 짓는다(io/dcm2niix.py 참고)."""
    out = sanitize_stderr("cannot open Hong_Gil_Dong_report.txt")
    assert "Hong_Gil_Dong" not in out
    assert "cannot open" in out

    out2 = sanitize_stderr("sidecar 5_T1.nii_repeat.json missing")
    assert "5_T1" not in out2
    assert "sidecar" in out2 and "missing" in out2


def test_sanitize_keeps_diagnostic_text_without_paths_untouched():
    """경로가 없는 진단 문구는 그대로 남는다 — 과도한 마스킹도 PHI 마스킹
    만큼이나 이 필드의 존재 이유(진단)를 해친다."""
    assert sanitize_stderr("CUDA out of memory") == "CUDA out of memory"
    assert sanitize_stderr("exit status 137") == "exit status 137"


def test_sanitize_collapses_extensionless_windows_path_with_space():
    """확장자 없는 파일명(예: 확장자 없는 필립스 DICOM 'IM0001')은 윈도우
    경로 정규식이 멈출 지점이 없어 공백마다 토큰이 쪼개진다 — 마스킹 사이에
    낀 가운데 토막까지 접어야 이름이 안 샌다."""
    out = sanitize_stderr(r"C:\Users\Hong Gil Dong\Desktop\IM0001 not found")
    assert "Hong" not in out and "Gil" not in out and "Dong" not in out
    assert "not found" in out


def test_sanitize_collapses_extensionless_unc_path_with_space():
    out = sanitize_stderr(r"\\SERVER\Share Name\Hong Gil Dong\IM0001 not found")
    assert "Hong" not in out and "Gil" not in out and "Dong" not in out
    assert "not found" in out


def test_sanitize_collapses_three_word_name_fully():
    """가운데 토큰이 인접 구분자가 없는 3어절 이름도 끝까지 접힌다(고정점
    반복이 없으면 첫 결합만 되고 멈춘다)."""
    out = sanitize_stderr(r"C:\Users\Hong Gil Dong\Desktop\IM0001 failed")
    assert "Hong" not in out
    assert "Gil" not in out
    assert "Dong" not in out


def test_sanitize_keeps_engine_version_string_intact():
    """dcm2niix 버전 문자열은 순수 숫자 확장자라 파일로 오인하면 안 된다 —
    엔진 실패 진단의 핵심 정보다."""
    out = sanitize_stderr("dcm2niix v1.0.20211006 failed")
    assert out == "dcm2niix v1.0.20211006 failed"


def test_write_status_rejects_unknown_state(tmp_path):
    p = job_paths(tmp_path, "job1").create()
    s = _new_status()
    s.state = "not_a_real_state"
    with pytest.raises(ValueError):
        write_status(p, s)


def test_record_error_moves_step_to_failing_step(tmp_path):
    """실패하면 status.step은 마지막으로 '시작한' 단계가 아니라 '실패한'
    단계를 가리켜야 한다(스펙 §9.1)."""
    p = job_paths(tmp_path, "job1").create()
    s = _new_status()
    s.step = "io"
    write_status(p, s)
    record_error(p, step="segment", returncode=1, stderr_tail="boom")
    s2 = read_status(p)
    assert s2.step == "segment"
