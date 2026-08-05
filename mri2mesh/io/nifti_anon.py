"""NIfTI 완전 익명화 — 영상 구성요소만 남기고 헤더 잔여 메타를 제거한다.

brain-educate로 반출되는 필수 요소는 orig.nii.gz·seg.nii.gz·GLB다. 이 NIfTI
둘에는 영상 데이터·affine·dtype·voxel size·단위 외의 헤더 필드가 전부 불필요한
메타데이터다(식별 정보가 실릴 수 있다). 원본 헤더를 신뢰하지 않고, 새 NIfTI를
영상 구성요소만으로 재작성해 descrip·aux_file·intent_name을 비우고 헤더
확장영역(NIfTI extensions)을 통째로 떨어뜨린다.

보존: 영상 배열(값·dtype), affine(sform/qform = 방향·위치), voxel size(zooms),
      xyzt 단위.
제거: descrip, aux_file, intent_name, 헤더 extensions, 기타 비영상 헤더 잔재.
"""

from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np


class NiftiAnonError(RuntimeError):
    """NIfTI 익명화에 실패했다 — 반출물에 헤더 메타가 남을 수 있으므로 진행 금지."""


def anonymize_nifti(path) -> None:
    """path의 NIfTI를 제자리에서 익명화한다(영상 구성요소만 남긴 새 파일로 덮어씀).

    Raises:
        NiftiAnonError: 읽기·재작성 실패 시. 호출부는 이걸 파이프라인 실패로
            다뤄야 한다 — 익명화 안 된 NIfTI를 반출하면 안 된다.
    """
    path = Path(path)
    try:
        img = nib.load(str(path))
        src = img.header
        # 저장 dtype·스케일을 반영한 배열을 그대로 가져온다(라벨맵은 정수 그대로).
        data = np.asanyarray(img.dataobj)

        # 새 이미지 = 영상 배열 + affine 만. 새 헤더는 descrip/aux_file/intent_name이
        # 기본 빈값이고 extensions가 없다 — 원본 헤더의 잔재가 따라오지 않는다.
        clean = nib.Nifti1Image(data, img.affine)
        h = clean.header
        h.set_data_dtype(src.get_data_dtype())     # dtype 보존
        h.set_zooms(src.get_zooms())               # voxel size 보존
        h.set_xyzt_units(*src.get_xyzt_units())    # 단위 보존

        nib.save(clean, str(path))
    except Exception as exc:  # nibabel은 다양한 예외를 던진다
        raise NiftiAnonError(f"NIfTI 익명화 실패: {path}") from exc
