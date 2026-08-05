from pathlib import Path
import numpy as np
import nibabel as nib
import pytest
from pydicom.dataset import Dataset
from mri2mesh.io.dicom_meta import read_dicom_header, DicomMetaError, build_meta, write_meta, read_meta


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


def _fake_nifti(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.zeros((4, 5, 6), np.int16), np.eye(4)), path)
    return path


def test_build_meta_dicom(tmp_path):
    dcm = _fake_dicom(tmp_path / "IM0001")
    nii = _fake_nifti(tmp_path / "s.nii.gz")
    meta = build_meta(
        source="dicom", original_filenames=["A/IM0001"],
        dicom_file=dcm, nifti_path=nii,
        sidecar={"Modality": "MR", "SeriesDescription": "T1"},
    )
    assert meta["source"] == "dicom"
    assert meta["originalFilenames"] == ["A/IM0001"]
    assert meta["before"]["PatientName"] == "Hong^Gil^Dong"
    assert meta["after"]["nifti"]["dims"] == [4, 5, 6]
    assert meta["after"]["sidecar"]["Modality"] == "MR"
    # PatientName은 사이드카에 없으니 removed에 들어간다
    assert "PatientName" in meta["removed"]
    assert "Modality" not in meta["removed"]   # before·sidecar 양쪽에 있음


def test_build_meta_nifti_has_no_before(tmp_path):
    nii = _fake_nifti(tmp_path / "s.nii.gz")
    meta = build_meta(source="nifti", original_filenames=["scan.nii.gz"],
                      dicom_file=None, nifti_path=nii, sidecar=None)
    assert meta["source"] == "nifti"
    assert meta["before"] is None
    assert meta["removed"] == []
    assert meta["after"]["sidecar"] == {}


def test_write_read_roundtrip(tmp_path):
    meta = {"source": "nifti", "before": None, "after": {}, "removed": [],
            "originalFilenames": []}
    p = tmp_path / "dicom-meta.json"
    write_meta(p, meta)
    assert read_meta(p) == meta
