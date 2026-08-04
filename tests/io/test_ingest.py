"""입력 정규화 테스트 (스펙 §6.1)."""

from __future__ import annotations

import gzip
import struct
import zipfile
from pathlib import Path

import pytest

from mri2mesh.io.archive import UnsafeArchiveError
from mri2mesh.io.ingest import (
    PreparedInput,
    SourceKind,
    UnsupportedInputError,
    collect_dicom_files,
    prepare_input,
)


def _nifti1_bytes() -> bytes:
    hdr = bytearray(348)
    struct.pack_into("<i", hdr, 0, 348)
    hdr[344:348] = b"n+1\x00"
    return bytes(hdr) + b"\x00" * 100


def _dicom_bytes() -> bytes:
    return b"\x00" * 128 + b"DICM" + b"\x00" * 200


def test_nifti_file_is_copied_into_workdir(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(gzip.compress(_nifti1_bytes()))
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.NIFTI
    assert prepared.nifti_file is not None
    assert prepared.nifti_file.exists()
    assert prepared.nifti_file.parent == workdir
    assert prepared.dicom_files == []


def test_uncompressed_nifti_keeps_nii_suffix(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(_nifti1_bytes())
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.nifti_file.name == "input.nii"


def test_gzipped_nifti_keeps_nii_gz_suffix(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(gzip.compress(_nifti1_bytes()))
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.nifti_file.name == "input.nii.gz"


def test_zip_keeps_only_dicom_entries(tmp_path):
    """파일명은 보지 않는다 — 내용으로만 추린다."""
    src = tmp_path / "study.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("study/s1/IM000001", _dicom_bytes())
        zf.writestr("study/s1/IM000002", _dicom_bytes())
        zf.writestr("study/readme.txt", b"not an image")
        zf.writestr("__MACOSX/._IM000001", b"\x00\x05\x16\x07junk")
        zf.writestr("study/DICOMDIR", b"random bytes not dicom")
        zf.writestr("looks_like.dcm", b"totally not dicom either")
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) == 2
    assert {p.name for p in prepared.dicom_files} == {"IM000001", "IM000002"}
    assert prepared.dicom_dir is not None
    assert prepared.nifti_file is None


def test_directory_of_dicom_is_accepted(tmp_path):
    src = tmp_path / "series"
    src.mkdir()
    (src / "I10").write_bytes(_dicom_bytes())
    (src / "I11").write_bytes(_dicom_bytes())
    (src / "notes.txt").write_bytes(b"hello")
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.DICOM_DIR
    assert len(prepared.dicom_files) == 2
    assert prepared.dicom_dir == src


def test_zip_without_dicom_is_rejected(tmp_path):
    """거부되면 workdir에 전개물이 남지 않아야 한다 — 그래야 같은 workdir로 재시도할 수 있다."""
    src = tmp_path / "docs.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a.txt", b"hello")
    workdir = tmp_path / "work"

    with pytest.raises(UnsupportedInputError, match="DICOM"):
        prepare_input(src, workdir)

    assert not (workdir / "extracted").exists()


def test_zip_rejection_allows_retry_into_same_workdir(tmp_path):
    """DICOM 없는 ZIP이 거부된 뒤, 같은 workdir로 유효한 DICOM ZIP을 재시도하면 성공해야 한다."""
    workdir = tmp_path / "work"

    empty_src = tmp_path / "docs.zip"
    with zipfile.ZipFile(empty_src, "w") as zf:
        zf.writestr("a.txt", b"hello")
    with pytest.raises(UnsupportedInputError, match="DICOM"):
        prepare_input(empty_src, workdir)

    valid_src = tmp_path / "study.zip"
    with zipfile.ZipFile(valid_src, "w") as zf:
        zf.writestr("IM0001", _dicom_bytes())

    prepared = prepare_input(valid_src, workdir)

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) == 1


def test_truncated_zip_raises_unsafe_archive_error_not_bad_zip_file(tmp_path):
    """중앙 디렉터리가 잘려나간 ZIP은 zipfile.BadZipFile이 새어나가지 않고
    UnsafeArchiveError로 감싸져야 한다 — prepare_input의 계약이
    UnsupportedInputError/UnsafeArchiveError만 약속하기 때문이다(스펙 리뷰 I1-a).
    """
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("a.txt", _dicom_bytes())
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(good.read_bytes()[:-50])

    with pytest.raises(UnsafeArchiveError):
        prepare_input(truncated, tmp_path / "work")


def test_gzip_wrapped_zip_is_rejected_as_unsupported(tmp_path):
    """gzip 안에 ZIP이 든 이중 압축은 판별 실패로 거부한다 — safe_extract에
    아직 압축된 경로가 그대로 전달되어 zipfile.BadZipFile로 새어나가는 일이
    없어야 한다(스펙 리뷰 I1-b).
    """
    inner_zip = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.writestr("a.txt", _dicom_bytes())
    wrapped = tmp_path / "wrapped.gz"
    wrapped.write_bytes(gzip.compress(inner_zip.read_bytes()))

    with pytest.raises(UnsupportedInputError, match="판별"):
        prepare_input(wrapped, tmp_path / "work")


def test_garbage_file_is_rejected(tmp_path):
    src = tmp_path / "junk"
    src.write_bytes(b"not anything" * 50)

    with pytest.raises(UnsupportedInputError, match="판별"):
        prepare_input(src, tmp_path / "work")


def test_single_dicom_file_is_rejected(tmp_path):
    """DICOM 한 장으로는 볼륨을 못 만든다. 폴더나 ZIP을 요구한다."""
    src = tmp_path / "IM000001"
    src.write_bytes(_dicom_bytes())

    with pytest.raises(UnsupportedInputError, match="폴더|ZIP"):
        prepare_input(src, tmp_path / "work")


def test_empty_directory_is_rejected(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()

    with pytest.raises(UnsupportedInputError, match="DICOM"):
        prepare_input(src, tmp_path / "work")


def test_symlink_cycle_does_not_hang_and_real_dicom_is_found(tmp_path):
    """자기 참조 심볼릭 링크가 있어도 멈추지 않고, 다른 곳의 실제 DICOM은 찾아야 한다."""
    root = tmp_path / "series"
    root.mkdir()
    (root / "I10").write_bytes(_dicom_bytes())
    nested = root / "nested"
    nested.mkdir()
    (nested / "I11").write_bytes(_dicom_bytes())

    try:
        (root / "loop").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("이 환경에서는 심볼릭 링크를 만들 수 없다")

    found = collect_dicom_files(root)

    assert found == sorted([root / "I10", nested / "I11"])


@pytest.mark.realdata
def test_real_zip_yields_dicom_files(real_data_dir, tmp_path):
    """MRI2MESH_TEST_DATA_DIR 안의 첫 ZIP을 실제로 정규화한다."""
    zips = sorted(real_data_dir.rglob("*.zip"))
    if not zips:
        pytest.skip("테스트 데이터에 ZIP이 없다")

    prepared = prepare_input(zips[0], tmp_path / "work")

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) > 0
    print(f"\n{zips[0].name}: DICOM {len(prepared.dicom_files)}개")
