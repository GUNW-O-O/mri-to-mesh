"""입력 판별 테스트 (스펙 §6.1).

확장자를 신뢰하지 않는다는 계약을 지키는지 확인한다 — 모든 fixture 파일은
내용과 무관한 확장자를 갖는다.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import pytest
from pydicom.dataset import Dataset

from mri2mesh.io.detect import InputKind, detect_format


def _nifti1_bytes(endian: str = "<", magic: bytes = b"n+1\x00") -> bytes:
    """NIfTI-1 헤더 348바이트. sizeof_hdr==348, offset 344에 magic."""
    hdr = bytearray(348)
    struct.pack_into(endian + "i", hdr, 0, 348)
    hdr[344:348] = magic
    return bytes(hdr) + b"\x00" * 100


def _nifti2_bytes(endian: str = "<") -> bytes:
    """NIfTI-2 헤더. sizeof_hdr==540, offset 4에 'n+2\\0'."""
    hdr = bytearray(540)
    struct.pack_into(endian + "i", hdr, 0, 540)
    hdr[4:8] = b"n+2\x00"
    hdr[8:12] = b"\r\n\x1a\n"
    return bytes(hdr) + b"\x00" * 100


def _dicom_with_preamble() -> bytes:
    """offset 128에 'DICM'이 있는 표준 DICOM 선두."""
    return b"\x00" * 128 + b"DICM" + b"\x02\x00\x00\x00UL\x04\x00" + b"\x00" * 200


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_zip_detected_by_magic(tmp_path):
    path = _write(tmp_path, "study.dcm", b"PK\x03\x04" + b"\x00" * 200)
    assert detect_format(path) is InputKind.ZIP


def test_nifti1_little_endian(tmp_path):
    path = _write(tmp_path, "IM000001", _nifti1_bytes("<"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti1_big_endian(tmp_path):
    path = _write(tmp_path, "noext", _nifti1_bytes(">"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti1_hdr_img_pair_magic(tmp_path):
    """.hdr/.img 쌍의 magic 'ni1\\0'도 NIfTI로 인정한다."""
    path = _write(tmp_path, "vol.img", _nifti1_bytes("<", b"ni1\x00"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti2(tmp_path):
    path = _write(tmp_path, "vol.ima", _nifti2_bytes("<"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti2_big_endian(tmp_path):
    path = _write(tmp_path, "noext", _nifti2_bytes(">"))
    assert detect_format(path) is InputKind.NIFTI


def test_gzipped_nifti(tmp_path):
    path = _write(tmp_path, "anything", gzip.compress(_nifti1_bytes("<")))
    assert detect_format(path) is InputKind.NIFTI


def test_dicom_with_preamble(tmp_path):
    path = _write(tmp_path, "I10", _dicom_with_preamble())
    assert detect_format(path) is InputKind.DICOM


def test_dicom_without_preamble(tmp_path):
    """메타 헤더 없이 raw dataset으로 내보낸 DICOM은 'DICM'이 없다.

    pydicom 폴백으로 SOPClassUID / Rows·Columns를 보고 인정한다.
    """
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    ds.Rows = 256
    ds.Columns = 256
    path = tmp_path / "1.2.840.113619.2.1.1.1"
    ds.save_as(path, implicit_vr=True, little_endian=True, enforce_file_format=False)

    assert path.read_bytes()[128:132] != b"DICM"
    assert detect_format(path) is InputKind.DICOM


def test_garbage_is_unknown(tmp_path):
    path = _write(tmp_path, "notes.nii.gz", b"hello world, not an image" * 10)
    assert detect_format(path) is InputKind.UNKNOWN


def test_empty_file_is_unknown(tmp_path):
    path = _write(tmp_path, "empty.zip", b"")
    assert detect_format(path) is InputKind.UNKNOWN


def test_truncated_gzip_is_unknown(tmp_path):
    """깨진 gzip은 예외를 던지지 않고 UNKNOWN을 반환한다."""
    path = _write(tmp_path, "broken", gzip.compress(_nifti1_bytes())[:20])
    assert detect_format(path) is InputKind.UNKNOWN


def test_corrupted_gzip_is_unknown(tmp_path):
    """gzip 헤더는 있지만 압축 페이로드가 손상되면 UNKNOWN을 반환한다.

    zlib.error를 던지는 파손(truncation이 아님)을 테스트한다.
    """
    compressed = gzip.compress(_nifti1_bytes())
    # gzip 헤더(10바이트)를 건너뛰고 압축 페이로드를 손상시킨다.
    # 위치 10의 비트를 뒤집으면 zlib.error("invalid code lengths set")을 발생시킨다.
    corrupted = bytearray(compressed)
    corrupted[10] ^= 0xFF
    path = _write(tmp_path, "corrupted", bytes(corrupted))
    assert detect_format(path) is InputKind.UNKNOWN


def test_gzip_wrapped_zip_is_unknown(tmp_path):
    """gzip 안에 ZIP이 든 이중 압축은 지원하지 않으므로 UNKNOWN을 돌려준다.

    detect_format이 여기서 InputKind.ZIP을 돌려주면 ingest.prepare_input이
    압축을 풀지 않은 원본 경로를 그대로 zipfile.ZipFile에 넘겨
    zipfile.BadZipFile로 이어진다(스펙 리뷰 I1의 원인 (b)).
    """
    import zipfile

    inner_zip = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.writestr("a.txt", b"x" * 1000)
    wrapped = _write(tmp_path, "wrapped.gz", gzip.compress(inner_zip.read_bytes()))

    assert detect_format(wrapped) is InputKind.UNKNOWN


def test_input_kind_nifti_shares_value_with_source_kind_but_is_distinct_type():
    """InputKind.NIFTI와 ingest.SourceKind.NIFTI는 값("nifti")을 공유하는
    서로 다른 str Enum이다. == 비교는 True이지만 is 비교는 False다 — 두 값을
    혼동하지 않도록 현재 동작을 고정해 둔다.
    """
    from mri2mesh.io.ingest import SourceKind

    assert InputKind.NIFTI == SourceKind.NIFTI
    assert InputKind.NIFTI is not SourceKind.NIFTI


def test_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        detect_format(tmp_path)


def test_ds_store_and_thumbs_are_unknown(tmp_path):
    """스펙 §6.2 — __MACOSX/, .DS_Store 등은 매직바이트 검사에서 자동 탈락한다."""
    path = _write(tmp_path, ".DS_Store", b"\x00\x00\x00\x01Bud1" + b"\x00" * 100)
    assert detect_format(path) is InputKind.UNKNOWN


def test_fallback_does_not_leak_pydicom_warnings(tmp_path, monkeypatch, recwarn):
    """폴백이 pydicom 경고를 호출자에게 흘리면 안 된다.

    -W error에서는 이 경고가 예외가 되고 폴백의 except Exception이 그것을
    삼켜 UNKNOWN을 돌려준다. 그러면 같은 파일이 테스트에서는 UNKNOWN,
    운영에서는 DICOM으로 갈린다. 실제 Philips 스터디에서 이 경고를 내는
    파일이 7개 나왔다.

    경고를 내는 실물 파일을 픽스처로 만들려고 하지 말 것 — pydicom이 어떤
    바이트에서 이 경고를 내는지는 내부 휴리스틱에 달려 있어 재현이 불안정하다.
    Dataset(implicit_vr=False)로 만들어 봤으나 경고가 나지 않았다. dcmread를
    감싸서 경고를 확실히 발생시키고, 그것이 detect_format 밖으로 나오는지만 본다.
    """
    import warnings

    import pydicom

    real_dcmread = pydicom.dcmread

    def warning_dcmread(*args, **kwargs):
        warnings.warn("Expected implicit VR, but found explicit VR", UserWarning)
        return real_dcmread(*args, **kwargs)

    monkeypatch.setattr(pydicom, "dcmread", warning_dcmread)

    path = tmp_path / "garbage"
    path.write_bytes(b"\xde\xad\xbe\xef" * 500)

    detect_format(path)

    assert [str(w.message) for w in recwarn.list] == [], "폴백 밖으로 경고가 샜다"


def test_fallback_reads_only_the_three_decision_tags(tmp_path, monkeypatch):
    """폴백은 SOPClassUID / Rows / Columns만 읽어야 한다.

    이 셋만 읽으면 pydicom이 나머지 element의 값을 물화하지 않는다.
    쓰레기 바이트를 거대한 element 길이로 오해해 통째로 읽어들이는 것이
    파일당 0.15~0.5초를 먹던 원인이었다(실측: 26개 파일 6.22s → 0.01s).
    """
    import pydicom
    from pydicom.tag import Tag

    seen: dict = {}
    real = pydicom.dcmread

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(pydicom, "dcmread", spy)

    path = tmp_path / "garbage"
    path.write_bytes(b"\xde\xad\xbe\xef" * 500)
    detect_format(path)

    assert seen.get("specific_tags") == [
        Tag(0x0008, 0x0016),
        Tag(0x0028, 0x0010),
        Tag(0x0028, 0x0011),
    ], f"specific_tags가 지정되지 않았다: {seen}"
    assert seen.get("stop_before_pixels") is True


@pytest.mark.realdata
def test_real_data_files_are_all_recognized(real_data_dir):
    """MRI2MESH_TEST_DATA_DIR 안의 파일이 ZIP/NIfTI/DICOM 중 하나로 판별되는지 본다.

    UNKNOWN이 나온 파일 목록을 그대로 보여준다. 진짜 잡파일(readme 등)이면
    무시하고, DICOM인데 UNKNOWN이면 판별 로직에 구멍이 있다는 뜻이다.
    """
    kinds: dict[InputKind, list[str]] = {k: [] for k in InputKind}
    for path in sorted(real_data_dir.rglob("*")):
        if path.is_file():
            kinds[detect_format(path)].append(str(path.relative_to(real_data_dir)))

    recognized = sum(len(v) for k, v in kinds.items() if k is not InputKind.UNKNOWN)
    assert recognized > 0, f"인식된 파일이 하나도 없다: {kinds}"
    print(f"\nZIP={len(kinds[InputKind.ZIP])} NIFTI={len(kinds[InputKind.NIFTI])} "
          f"DICOM={len(kinds[InputKind.DICOM])} UNKNOWN={len(kinds[InputKind.UNKNOWN])}")
    if kinds[InputKind.UNKNOWN]:
        print("UNKNOWN 목록 (앞 20개):")
        for name in kinds[InputKind.UNKNOWN][:20]:
            print(f"  {name}")
