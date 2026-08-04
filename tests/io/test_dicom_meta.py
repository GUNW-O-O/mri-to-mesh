from pathlib import Path
import pytest
from pydicom.dataset import Dataset
from mri2mesh.io.dicom_meta import read_dicom_header, DicomMetaError


def _fake_dicom(path: Path, **tags) -> Path:
    ds = Dataset()
    ds.PatientName = tags.get("PatientName", "Hong^Gil^Dong")
    ds.PatientID = tags.get("PatientID", "FAKE-001")
    ds.StudyDate = tags.get("StudyDate", "20240101")
    ds.Modality = tags.get("Modality", "MR")
    ds.PixelSpacing = tags.get("PixelSpacing", [0.89, 0.89])
    # Required for PixelData
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.SamplesPerPixel = 1
    ds.Rows = 4
    ds.Columns = 4
    ds.PixelData = b"\x00\x00" * 8   # 제외돼야 함
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, implicit_vr=True, little_endian=True, enforce_file_format=False)
    return path


def test_read_header_keeps_tags_drops_pixeldata(tmp_path):
    p = _fake_dicom(tmp_path / "IM0001")
    h = read_dicom_header(p)
    assert h["PatientName"] == "Hong^Gil^Dong"
    assert h["PatientID"] == "FAKE-001"
    assert h["Modality"] == "MR"
    assert "PixelData" not in h
    # json 직렬화 가능해야 한다(값이 pydicom 타입으로 남으면 안 됨)
    import json; json.dumps(h)


def test_read_header_bad_file_raises(tmp_path):
    bad = tmp_path / "notdicom.bin"
    bad.write_bytes(b"not a dicom at all")
    with pytest.raises(DicomMetaError):
        read_dicom_header(bad)
