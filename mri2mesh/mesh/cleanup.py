"""축0: 라벨별 연결요소 정리 (스펙 §6.5 보강).

FastSurfer seg는 한 라벨을 뇌실 주변에 작은 파편으로 흩뿌리곤 한다(특히
WM-hypointensities·choroid-plexus). 라벨 마스크를 통째로 추출하면 이 파편이
본체와 떨어진 위성 메쉬 = 스파이크가 된다. 추출 전에 작은 연결요소를 버려
스파이크를 없앤다. 큰 조각(실제 병변·구조)은 유지 — 부피 손실은 미미하다.

전처리(preprocess)와 별개 축이다: 이쪽은 위상(어떤 voxel을 남길지), 저쪽은
경계 모양(계단 완화)을 다룬다.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

# 26-이웃(면·변·꼭짓점 접촉) 연결성. 대각으로만 닿은 조각도 한 덩어리로 본다.
_CC26 = np.ones((3, 3, 3), dtype=int)


def drop_small_components(mask: np.ndarray, params) -> np.ndarray:
    """라벨 마스크에서 min_component_vox 미만 연결요소를 제거한다.

    method가 "none"이거나 임계가 1 이하면 원본을 그대로 돌려준다 —
    baseline 출력은 바뀌지 않는다.

    비용을 줄이려 마스크의 바운딩박스만 라벨링한다(전역 볼륨 대신).
    """
    if params.method == "none" or params.min_component_vox <= 1:
        return mask

    coords = np.argwhere(mask)
    if len(coords) == 0:
        return mask
    lo = coords.min(axis=0)
    hi = coords.max(axis=0) + 1
    sl = tuple(slice(int(lo[i]), int(hi[i])) for i in range(mask.ndim))

    lab, n = ndimage.label(mask[sl], structure=_CC26)
    if n <= 1:
        return mask  # 조각 하나뿐 — 버릴 게 없다

    sizes = np.bincount(lab.ravel())
    sizes[0] = 0  # 배경 제외
    keep_ids = np.flatnonzero(sizes >= params.min_component_vox)

    out = np.zeros_like(mask)
    out[sl] = np.isin(lab, keep_ids)
    return out
