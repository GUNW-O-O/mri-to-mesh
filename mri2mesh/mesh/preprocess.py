"""축1: 마스크 전처리 (안티에일리어싱, 스펙 §6.5).

이진 마스크에 곧바로 등고면을 뽑으면 voxel 계단이 남는다. 전처리로 계단을
줄이면 약한 스무딩으로 끝난다. 각 방법은 (스칼라장, isolevel)을 낸다 —
추출기는 이 둘만 있으면 된다.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def apply_preprocess(mask: np.ndarray, params) -> tuple[np.ndarray, float]:
    """이진 마스크 -> (스칼라장 float32, isolevel).

    Raises:
        ValueError: 알 수 없는 method.
    """
    binary = np.asarray(mask) > 0

    if params.method == "none":
        return binary.astype(np.float32), 0.5

    if params.method == "gaussian":
        field = ndimage.gaussian_filter(
            binary.astype(np.float32), sigma=float(params.sigma_vox)
        )
        return field, 0.5

    if params.method == "distance":
        # 부호거리장: 안쪽(+) 바깥(-). 0 등고면이 원래 경계다.
        inside = ndimage.distance_transform_edt(binary)
        outside = ndimage.distance_transform_edt(~binary)
        field = (inside - outside).astype(np.float32)
        return field, 0.0

    raise ValueError(f"알 수 없는 전처리 method: {params.method}")
