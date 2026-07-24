"""T1 후보 시리즈 추천 (스펙 §6.3).

추천 기준: 등방성에 가까운 voxel(약 1mm), 슬라이스 128장 이상,
description이 mprage|t1|bravo|spgr|tfl 매칭.

이 모듈은 순위만 매긴다. 자동 확정하지 않는다. FastSurfer는 1mm T1w를
전제하므로 잘못된 시리즈를 넣으면 결과가 조용히 망가진다. 사용자 확인이 필수다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from seg_and_mesh.io.dcm2niix import SeriesOutput

#: T1 계열 시퀀스 이름 (스펙 §6.3)
T1_DESCRIPTION_PATTERN = re.compile(r"mprage|t1|bravo|spgr|tfl", re.IGNORECASE)

#: 등방성 판정 — 최대/최소 voxel 변 길이 비
_MAX_ISOTROPY_RATIO = 1.2

#: 1mm 근처 판정 범위 (mm)
_MIN_VOXEL_MM = 0.7
_MAX_VOXEL_MM = 1.4

#: 최소 슬라이스 수
_MIN_SLICES = 128

_SCORE_DESCRIPTION = 3
_SCORE_ISOTROPIC = 2
_SCORE_NEAR_1MM = 2
_SCORE_SLICES = 2
_SCORE_3D = 1


@dataclass(frozen=True)
class SeriesCandidate:
    """시리즈 하나의 점수와 근거. reasons는 UI에 그대로 보여준다."""

    series: SeriesOutput
    score: int
    reasons: list[str]


def _is_isotropic(vox: tuple[float, float, float]) -> bool:
    valid = [v for v in vox if v > 0]
    if len(valid) < 3:
        return False
    return max(valid) / min(valid) <= _MAX_ISOTROPY_RATIO


def _is_near_1mm(vox: tuple[float, float, float]) -> bool:
    valid = [v for v in vox if v > 0]
    if len(valid) < 3:
        return False
    return all(_MIN_VOXEL_MM <= v <= _MAX_VOXEL_MM for v in valid)


def score_series(series: SeriesOutput) -> SeriesCandidate:
    """시리즈 하나에 점수를 매기고 근거를 남긴다."""
    score = 0
    reasons: list[str] = []

    if T1_DESCRIPTION_PATTERN.search(series.series_description):
        score += _SCORE_DESCRIPTION
        reasons.append(f"설명이 T1 패턴과 일치: {series.series_description!r}")

    if _is_isotropic(series.voxel_size_mm):
        score += _SCORE_ISOTROPIC
        reasons.append(f"등방성 voxel: {series.voxel_size_mm}")

    if _is_near_1mm(series.voxel_size_mm):
        score += _SCORE_NEAR_1MM
        reasons.append(f"voxel이 1mm 근처: {series.voxel_size_mm}")

    if series.slices >= _MIN_SLICES:
        score += _SCORE_SLICES
        reasons.append(f"슬라이스 {series.slices}장 (>= {_MIN_SLICES})")
    else:
        reasons.append(f"슬라이스 {series.slices}장 (< {_MIN_SLICES})")

    if series.acquisition_type.upper() == "3D":
        score += _SCORE_3D
        reasons.append("3D 획득")

    return SeriesCandidate(series=series, score=score, reasons=reasons)


def rank_series(series_list: list[SeriesOutput]) -> list[SeriesCandidate]:
    """점수 내림차순으로 정렬한다. 동점이면 슬라이스 수, 그다음 시리즈 번호 순."""
    candidates = [score_series(s) for s in series_list]
    return sorted(
        candidates,
        key=lambda c: (
            -c.score,
            -c.series.slices,
            c.series.series_number if c.series.series_number is not None else 1 << 30,
        ),
    )
