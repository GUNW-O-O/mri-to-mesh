"""FastSurfer docker 실행 (스펙 §5.1, §6.4, §13 — FastSurfer는 목).

FastSurfer는 GPU·수 분이 들어 CI에 안 넣는다. runner를 목으로 바꿔 명령
구성·출력 추출·에러 처리를 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np
import pytest

from seg_and_mesh.segment import (
    SEG_SOURCE_FILE,
    SegmentError,
    build_fastsurfer_command,
    run_fastsurfer,
)


def _save_mgz(path: Path, data: np.ndarray) -> None:
    affine = np.eye(4)
    nib.save(nib.MGHImage(data, affine), path)


def _write_t1(tmp_path: Path, name: str = "input.nii.gz") -> Path:
    t1 = tmp_path / "nifti" / name
    t1.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.zeros((8, 8, 8), np.float32), np.eye(4)), t1)
    return t1


def _ok_runner(subject_dir_root: Path, sid: str):
    """FastSurfer 성공을 흉내낸다 — mri/에 orig.mgz와 withCC 세그를 쓴다."""

    def run(cmd, **kwargs):
        mri = subject_dir_root / sid / "mri"
        mri.mkdir(parents=True, exist_ok=True)
        _save_mgz(mri / "orig.mgz", np.full((8, 8, 8), 100, np.uint8))
        seg = np.zeros((8, 8, 8), np.int16)
        seg[0, 0, 0] = 17    # 해마
        seg[1, 1, 1] = 251   # 뇌량 (withCC)
        _save_mgz(mri / SEG_SOURCE_FILE, seg)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    return run


# --- 명령 구성 ---

def test_command_has_both_root_flags_and_seg_only(tmp_path):
    """--user root(docker)와 --allow_root(FastSurfer) 둘 다, --seg_only."""
    t1 = _write_t1(tmp_path)
    cmd = build_fastsurfer_command(t1, tmp_path / "out", "case", "fs:tag", threads=8)

    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == "root"
    assert "--allow_root" in cmd
    assert "--seg_only" in cmd
    assert "--vox_size" in cmd and cmd[cmd.index("--vox_size") + 1] == "1.0"


def test_command_never_passes_no_cc(tmp_path):
    """--no_cc는 뇌량 5개를 조용히 없앤다(스펙 §14). 절대 안 준다."""
    t1 = _write_t1(tmp_path)
    cmd = build_fastsurfer_command(t1, tmp_path / "out", "case", "fs:tag")
    assert "--no_cc" not in cmd


def test_command_mounts_input_and_output(tmp_path):
    t1 = _write_t1(tmp_path)
    cmd = build_fastsurfer_command(t1, tmp_path / "out", "case", "fs:tag")
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
    assert any(m.endswith(":/data:ro") for m in mounts)
    assert any(m.endswith(":/output") for m in mounts)
    # 입력은 /data/<파일명>으로 참조
    assert cmd[cmd.index("--t1") + 1] == "/data/input.nii.gz"


# --- 실행·출력 추출 ---

def test_run_writes_uint8_orig_and_locates_withcc_seg(tmp_path):
    t1 = _write_t1(tmp_path)
    root = tmp_path / "out"
    result = run_fastsurfer(
        t1, root, "fs:tag", sid="case", runner=_ok_runner(root, "case")
    )

    assert result.orig_path.name == "orig.nii.gz"
    assert result.orig_path.is_file()
    orig = nib.load(result.orig_path)
    assert orig.get_data_dtype() == np.uint8

    assert result.seg_source_path.name == SEG_SOURCE_FILE
    assert result.seg_source_path.is_file()
    assert result.subject_dir == root / "case"


def test_seg_source_feeds_remap_to_canonical(tmp_path):
    """세그 소스가 labels.remap의 입력이 된다 — end-to-end 연결 확인."""
    from seg_and_mesh.labels import load_canonical, remap_segmentation

    t1 = _write_t1(tmp_path)
    root = tmp_path / "out"
    result = run_fastsurfer(
        t1, root, "fs:tag", sid="case", runner=_ok_runner(root, "case")
    )

    canon = tmp_path / "seg.nii.gz"
    remap = remap_segmentation(result.seg_source_path, canon)
    by_fs = load_canonical().by_fs_id()
    # fs 17(해마)·251(뇌량)이 canonical id로 리맵됐다
    assert by_fs[17].id in remap.voxel_counts
    assert by_fs[251].id in remap.voxel_counts


# --- 에러 처리 (PHI 안 새게) ---

def test_failure_raises_without_leaking_filename(tmp_path):
    """실패 메시지에 입력 파일명(환자 식별자 가능)이 없어야 한다(스펙 §12)."""
    t1 = _write_t1(tmp_path, name="Hong_Gil_Dong_MPRAGE.nii.gz")

    def failing(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="CUDA out of memory")

    with pytest.raises(SegmentError) as exc:
        run_fastsurfer(t1, tmp_path / "out", "fs:tag", runner=failing)

    msg = str(exc.value)
    assert "Hong_Gil_Dong" not in msg
    assert "종료 1" in msg
    assert "CUDA out of memory" in msg  # stderr 마지막 줄은 진단에 필요


def test_missing_output_raises(tmp_path):
    """0으로 끝났어도 기대 출력이 없으면 실패."""
    t1 = _write_t1(tmp_path)

    def empty(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(SegmentError):
        run_fastsurfer(t1, tmp_path / "out", "fs:tag", runner=empty)
