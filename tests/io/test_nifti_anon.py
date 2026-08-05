"""NIfTI 완전 익명화(mri2mesh/io/nifti_anon.py).

반출 NIfTI(orig·seg)에서 영상 구성요소만 남기고 헤더 잔여 메타·확장영역을
제거하는지 확인한다. 테스트 헤더의 PHI는 가짜(Hong Gil Dong).
"""

from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

from mri2mesh.io.nifti_anon import NiftiAnonError, anonymize_nifti


def _dirty_nifti(path):
    # 서로 다른 값의 정수 볼륨(값 보존 확인용)
    data = np.arange(4 * 5 * 6, dtype=np.int16).reshape(4, 5, 6)
    affine = np.array([[0.0, 0.0, 1.2, 10.0],
                       [0.89, 0.0, 0.0, -20.0],
                       [0.0, 0.89, 0.0, 30.0],
                       [0.0, 0.0, 0.0, 1.0]])
    img = nib.Nifti1Image(data, affine)
    img.header.set_zooms((1.2, 0.89, 0.89))
    img.header.set_xyzt_units("mm", "sec")
    # 식별 정보가 실릴 수 있는 헤더 필드(가짜 PHI)
    img.header["descrip"] = b"PatientName: Hong Gil Dong"
    img.header["aux_file"] = b"CASE-12345.dat"
    img.header["intent_name"] = b"MRN0007"
    # 헤더 확장영역(전체가 임의 blob — 여기에도 PHI가 실릴 수 있다)
    img.header.extensions.append(nib.nifti1.Nifti1Extension("comment", b"Hong Gil Dong PHI blob"))
    nib.save(img, str(path))
    return data, affine


def test_anonymize_strips_metadata_keeps_image(tmp_path):
    p = tmp_path / "orig.nii.gz"
    data, affine = _dirty_nifti(p)

    anonymize_nifti(p)

    img = nib.load(str(p))
    h = img.header
    # 영상 구성요소 보존
    assert np.array_equal(np.asanyarray(img.dataobj), data)
    assert img.header.get_data_dtype() == np.dtype(np.int16)
    assert np.allclose(img.affine, affine)
    assert np.allclose(h.get_zooms(), (1.2, 0.89, 0.89))
    assert h.get_xyzt_units() == ("mm", "sec")
    # 헤더 잔여 메타 제거
    assert bytes(h["descrip"]).rstrip(b"\x00") == b""
    assert bytes(h["aux_file"]).rstrip(b"\x00") == b""
    assert bytes(h["intent_name"]).rstrip(b"\x00") == b""
    # 확장영역 통째 제거
    assert len(h.extensions) == 0


def test_anonymize_no_phi_substring_anywhere(tmp_path):
    """가짜 PHI 문자열이 파일 어디에도(헤더·확장영역) 남지 않는다."""
    p = tmp_path / "seg.nii.gz"
    _dirty_nifti(p)
    anonymize_nifti(p)
    # .nii.gz라 gzip — nibabel로 헤더 바이트를 다시 뽑아 확인
    img = nib.load(str(p))
    raw = img.header.binaryblock
    assert b"Hong" not in raw
    assert b"CASE-12345" not in raw
    assert b"MRN0007" not in raw


def test_anonymize_bad_file_raises(tmp_path):
    p = tmp_path / "notnifti.nii.gz"
    p.write_bytes(b"this is not a nifti")
    with pytest.raises(NiftiAnonError):
        anonymize_nifti(p)
