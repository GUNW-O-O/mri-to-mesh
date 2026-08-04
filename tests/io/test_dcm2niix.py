"""dcm2niix 래핑 테스트 (스펙 §6.3).

바이너리 실행이 필요 없는 부분(사이드카 파싱, 오류 처리)은 항상 실행하고,
실제 변환은 dcm2niix 마커로 분리한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from mri2mesh.io.dcm2niix import (
    Dcm2niixError,
    SeriesOutput,
    describe_nifti,
    find_dcm2niix,
    run_dcm2niix,
)


def _write_nifti(path: Path, shape=(256, 256, 176), zooms=(1.0, 1.0, 1.0)) -> Path:
    data = np.zeros(shape, dtype=np.uint8)
    img = nib.Nifti1Image(data, affine=np.eye(4))
    img.header.set_zooms(zooms)
    nib.save(img, path)
    return path


def test_describe_nifti_reads_shape_and_zooms(tmp_path):
    nifti = _write_nifti(tmp_path / "5_MPRAGE.nii.gz", (256, 256, 176), (1.0, 1.0, 1.0))
    sidecar = tmp_path / "5_MPRAGE.json"
    sidecar.write_text(
        json.dumps({
            "SeriesNumber": 5,
            "SeriesDescription": "MPRAGE",
            "MRAcquisitionType": "3D",
        }),
        encoding="utf-8",
    )

    out = describe_nifti(nifti, sidecar)

    assert out.series_number == 5
    assert out.series_description == "MPRAGE"
    assert out.slices == 176
    assert out.voxel_size_mm == pytest.approx((1.0, 1.0, 1.0))
    assert out.acquisition_type == "3D"


def test_describe_nifti_without_sidecar(tmp_path):
    """사이드카가 없어도 헤더만으로 기술한다."""
    nifti = _write_nifti(tmp_path / "vol.nii.gz", (256, 256, 30), (0.5, 0.5, 5.0))

    out = describe_nifti(nifti, None)

    assert out.series_number is None
    assert out.series_description == ""
    assert out.slices == 30
    assert out.voxel_size_mm == pytest.approx((0.5, 0.5, 5.0))
    assert out.acquisition_type == ""


def test_describe_nifti_handles_4d(tmp_path):
    """4D(예: DWI)면 slices는 3번째 축 길이를 쓴다."""
    data = np.zeros((64, 64, 20, 8), dtype=np.uint8)
    nifti = tmp_path / "dwi.nii.gz"
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), nifti)

    out = describe_nifti(nifti, None)

    assert out.slices == 20


def test_describe_nifti_tolerates_broken_sidecar(tmp_path):
    nifti = _write_nifti(tmp_path / "vol.nii.gz")
    sidecar = tmp_path / "vol.json"
    sidecar.write_text("{ not valid json", encoding="utf-8")

    out = describe_nifti(nifti, sidecar)

    assert out.series_description == ""


def test_describe_nifti_wraps_corrupt_nifti_load_error(tmp_path):
    """gzip 스트림이 잘렸거나 깨지면 nib.load 예외를 Dcm2niixError로 감싼다."""
    bad = tmp_path / "bad.nii.gz"
    # 정상적인 gzip 매직바이트만 두고 나머지를 잘라, nib.load가 파일 종류를
    # 판별하지 못해 ImageFileError를 던지게 만든다 (디스크 풀/강제 종료 상황 재현).
    bad.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00")

    with pytest.raises(Dcm2niixError, match="bad.nii.gz"):
        describe_nifti(bad, None)


def test_describe_nifti_raises_when_fewer_than_3_zooms(tmp_path):
    """2D 등 축이 3개 미만이면 voxel_size_mm 타입 계약을 깨는 대신 예외를 던진다."""
    data = np.zeros((64, 64), dtype=np.uint8)
    nifti = tmp_path / "flat.nii.gz"
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), nifti)

    with pytest.raises(Dcm2niixError, match="flat.nii.gz"):
        describe_nifti(nifti, None)


def test_describe_nifti_raises_on_non_numeric_series_number(tmp_path):
    """사이드카의 SeriesNumber는 스캐너가 만든 신뢰할 수 없는 값이다.

    정수로 해석되지 않으면(예: 'N/A') int()가 던지는 ValueError가 그대로
    새어나가면 안 되고 Dcm2niixError로 감싸져야 한다(스펙 리뷰 M6).
    """
    nifti = _write_nifti(tmp_path / "vol.nii.gz")
    sidecar = tmp_path / "vol.json"
    sidecar.write_text(json.dumps({"SeriesNumber": "N/A"}), encoding="utf-8")

    with pytest.raises(Dcm2niixError, match="SeriesNumber"):
        describe_nifti(nifti, sidecar)


def test_find_dcm2niix_uses_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "dcm2niix.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("DCM2NIIX_BIN", str(fake))

    assert find_dcm2niix() == str(fake)


def test_find_dcm2niix_raises_when_missing(monkeypatch):
    monkeypatch.delenv("DCM2NIIX_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(Dcm2niixError, match="dcm2niix"):
        find_dcm2niix()


def test_run_dcm2niix_raises_on_nonzero_exit(tmp_path, monkeypatch):
    """종료 코드가 0이 아니면 표준에러 꼬리를 담아 예외를 던진다."""
    import subprocess

    class _Result:
        returncode = 2
        stdout = "out"
        stderr = "Error: unable to read directory\n" * 3

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    with pytest.raises(Dcm2niixError, match="unable to read directory"):
        run_dcm2niix(dicom_dir, tmp_path / "out")


def test_run_dcm2niix_raises_when_no_output(tmp_path, monkeypatch):
    import subprocess

    class _Result:
        returncode = 0
        stdout = "Conversion required 0 files"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    with pytest.raises(Dcm2niixError, match="NIfTI"):
        run_dcm2niix(dicom_dir, tmp_path / "out")


def test_run_dcm2niix_ignores_stale_files_from_earlier_run(tmp_path, monkeypatch):
    """out_dir에 이전 실행 결과가 남아 있어도 이번에 새로 만든 파일만 돌려준다."""
    import subprocess

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = _write_nifti(out_dir / "1_OLD.nii.gz")

    def fake_run(cmd, **kwargs):
        _write_nifti(out_dir / "2_NEW.nii.gz")

        class _Result:
            returncode = 0
            stdout = "Conversion required 1 files"
            stderr = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    outputs = run_dcm2niix(dicom_dir, out_dir)

    names = {o.nifti_path.name for o in outputs}
    assert names == {"2_NEW.nii.gz"}
    assert stale.exists()  # 기존 파일은 건드리지 않는다


def test_run_dcm2niix_raises_when_no_new_output_despite_stale_files(tmp_path, monkeypatch):
    """out_dir가 이전 실행 결과로 이미 차 있어도, 새 파일이 없으면 실패로 본다."""
    import subprocess

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_nifti(out_dir / "1_OLD.nii.gz")

    class _Result:
        returncode = 0
        stdout = "Conversion required 0 files"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    with pytest.raises(Dcm2niixError, match="NIfTI"):
        run_dcm2niix(dicom_dir, out_dir)


def test_run_dcm2niix_invokes_expected_command_line(tmp_path, monkeypatch):
    """스펙 §6.3의 -d, -z y -b y -f %s_%d -o 조합이 실제로 전달되는지 확인한다.

    기존 mock들은 전부 cmd를 무시했으므로(lambda *a, **kw 등), 잘못된 플래그
    조합이 있어도 어떤 테스트도 걸러내지 못했다(스펙 리뷰 M7).
    """
    import subprocess

    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        _write_nifti(tmp_path / "out" / "5_T1.nii.gz")

        class _Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()
    out_dir = tmp_path / "out"

    run_dcm2niix(dicom_dir, out_dir, depth=7)

    cmd = captured_cmd["cmd"]
    assert cmd[0] == "fake-binary"
    assert cmd[cmd.index("-d") + 1] == "7"
    assert cmd[cmd.index("-z") + 1] == "y"
    assert cmd[cmd.index("-b") + 1] == "y"
    assert cmd[cmd.index("-f") + 1] == "%s_%d"
    assert cmd[cmd.index("-o") + 1] == str(out_dir)
    assert cmd[-1] == str(dicom_dir)


def test_run_dcm2niix_hints_depth_mismatch_when_no_output_from_deep_dicom(tmp_path, monkeypatch):
    """depth보다 깊은 곳에만 DICOM이 있어 dcm2niix가 아무것도 못 만들면,
    그 깊이 초과 사실이 오류 메시지에 나타나야 한다(스펙 리뷰 M3) — 그래야
    '정말 DICOM이 없음'과 '깊이 제한에 걸림'을 오류만 보고 구분할 수 있다.
    """
    import subprocess

    dicom_dir = tmp_path / "dcm"
    deep = dicom_dir
    for i in range(6):  # depth=5(기본값)보다 깊은 6단계 중첩
        deep = deep / f"level{i}"
    deep.mkdir(parents=True)
    (deep / "IM0001").write_bytes(b"not a real dicom, presence is what matters")

    class _Result:
        returncode = 0
        stdout = "Conversion required 0 files"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    with pytest.raises(Dcm2niixError, match="깊이") as excinfo:
        run_dcm2niix(dicom_dir, tmp_path / "out")

    assert "depth=6" in str(excinfo.value)


def test_run_dcm2niix_ignores_non_nifti_name_containing_nii_substring(tmp_path, monkeypatch):
    """파일명에 '.nii'가 부분 문자열로 들어간 사이드카는 결과로 모으지 않는다."""
    import subprocess

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def fake_run(cmd, **kwargs):
        _write_nifti(out_dir / "5_T1.nii.gz")
        (out_dir / "5_T1.nii_repeat.json").write_text("{}", encoding="utf-8")

        class _Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    outputs = run_dcm2niix(dicom_dir, out_dir)

    assert len(outputs) == 1
    assert outputs[0].nifti_path.name == "5_T1.nii.gz"


@pytest.mark.dcm2niix
@pytest.mark.realdata
def test_run_dcm2niix_on_real_data(real_data_dir, dcm2niix_bin, tmp_path):
    """실제 DICOM 폴더/ZIP을 변환해 시리즈 목록이 나오는지 본다."""
    from mri2mesh.io.ingest import prepare_input

    candidates = sorted(real_data_dir.rglob("*.zip")) or [real_data_dir]
    prepared = prepare_input(candidates[0], tmp_path / "work")
    assert prepared.dicom_dir is not None

    series = run_dcm2niix(prepared.dicom_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    assert len(series) > 0
    for s in series:
        print(f"\n#{s.series_number} {s.series_description!r} "
              f"slices={s.slices} vox={s.voxel_size_mm} type={s.acquisition_type}")
        assert s.nifti_path.exists()


def _write_nifti_shaped(path, shape, zooms):
    """주어진 shape·voxel 간격을 갖는 최소 NIfTI를 만든다."""
    import nibabel as nib
    import numpy as np

    data = np.zeros(shape, dtype=np.int16)
    affine = np.diag([zooms[0], zooms[1], zooms[2], 1.0])
    img = nib.Nifti1Image(data, affine)
    img.header.set_zooms(zooms)
    nib.save(img, path)
    return path


def test_slices_uses_coarsest_axis_not_third_axis(tmp_path):
    """슬라이스 축은 표본 간격이 가장 성긴 축이다.

    실제 Philips MPRAGE가 shape=(170, 288, 288), zooms=(1.2, 0.89, 0.89)로
    나온다. 슬라이스 축은 인덱스 0이고 정답은 170장인데, shape[2]를 쓰면
    288을 보고한다. 스펙 §6.3은 이 숫자를 사용자에게 표시하라고 요구한다.
    """
    path = _write_nifti_shaped(tmp_path / "mprage.nii.gz", (170, 288, 288), (1.2, 0.89, 0.89))

    out = describe_nifti(path, None)

    assert out.slices == 170


def test_slices_still_uses_third_axis_for_conventional_stack(tmp_path):
    """관통면이 3번째 축인 흔한 2D 스택에서는 동작이 그대로여야 한다.

    실제 T1W SE SAG: shape=(512, 512, 24), zooms=(0.45, 0.45, 5.0).
    """
    path = _write_nifti_shaped(tmp_path / "se.nii.gz", (512, 512, 24), (0.45, 0.45, 5.0))

    out = describe_nifti(path, None)

    assert out.slices == 24


def test_slices_falls_back_to_third_axis_when_isotropic(tmp_path):
    """등방성이면 관통면 축이라는 개념이 없다. 인덱스 2로 정한다.

    실제 Resting state fMRI: shape=(64, 64, 48), zooms=(3.31, 3.31, 3.31).
    """
    path = _write_nifti_shaped(tmp_path / "iso.nii.gz", (64, 64, 48), (3.31, 3.31, 3.31))

    out = describe_nifti(path, None)

    assert out.slices == 48


def test_output_without_sidecar_gets_name_from_filename(tmp_path):
    """dcm2niix는 ADC 같은 파생 볼륨에 사이드카를 만들지 않는다.

    실제 출력: 1101_32DIR_3mm_1NSA.nii.gz 에는 짝 .json이 있는데
    1101_32DIR_3mm_1NSA_ADC.nii.gz 에는 없다. 그대로 두면 이름 없는 항목이
    시리즈 목록에 뜬다(스펙 §6.3은 SeriesDescription 표시를 요구한다).
    """
    from mri2mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti_shaped(
        tmp_path / "1101_32DIR_3mm_1NSA_ADC.nii.gz", (224, 224, 50), (1.0, 1.0, 3.0)
    )

    (out,) = _collect_outputs({path})

    assert out.sidecar_path is None
    assert out.series_description == "32DIR_3mm_1NSA_ADC"
    assert out.series_number == 1101


def test_sidecar_wins_over_filename(tmp_path):
    """사이드카가 있으면 파일명은 쓰지 않는다.

    dcm2niix는 파일명에서 공백을 언더스코어로 바꾸지만 JSON에는 원본
    SeriesDescription을 그대로 넣는다. 실제로 파일 401_T1W_SE_SAG의
    사이드카에는 'T1W_SE SAG'(공백 포함)가 들어 있다.
    """
    import json

    from mri2mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti_shaped(
        tmp_path / "401_T1W_SE_SAG.nii.gz", (512, 512, 24), (0.45, 0.45, 5.0)
    )
    (tmp_path / "401_T1W_SE_SAG.json").write_text(
        json.dumps({"SeriesNumber": 401, "SeriesDescription": "T1W_SE SAG"}),
        encoding="utf-8",
    )

    (out,) = _collect_outputs({path})

    assert out.series_description == "T1W_SE SAG"
    assert out.series_number == 401


def test_filename_without_leading_series_number(tmp_path):
    """%s_%d 형식이 아닌 파일명이면 설명만 채우고 번호는 None으로 둔다.

    사용자가 직접 올린 NIfTI가 이 경로로 들어올 수 있다. 없는 번호를
    지어내면 안 된다.
    """
    from mri2mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti_shaped(tmp_path / "input.nii.gz", (64, 64, 48), (1.0, 1.0, 1.0))

    (out,) = _collect_outputs({path})

    assert out.series_number is None
    assert out.series_description == "input"
