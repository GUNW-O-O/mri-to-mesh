"""T1 시리즈 추천 테스트 (스펙 §6.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mri2mesh.io.dcm2niix import SeriesOutput
from mri2mesh.io.series import rank_series, score_series


def _series(
    description="",
    slices=176,
    vox=(1.0, 1.0, 1.0),
    acq="3D",
    number=1,
) -> SeriesOutput:
    return SeriesOutput(
        nifti_path=Path(f"{number}.nii.gz"),
        sidecar_path=None,
        series_number=number,
        series_description=description,
        slices=slices,
        voxel_size_mm=vox,
        acquisition_type=acq,
    )


def test_ideal_mprage_scores_highest():
    ideal = score_series(_series("MPRAGE", 176, (1.0, 1.0, 1.0), "3D"))
    poor = score_series(_series("AX FLAIR", 24, (0.5, 0.5, 5.0), "2D"))

    assert ideal.score > poor.score


@pytest.mark.parametrize("description", [
    "MPRAGE", "mprage", "T1w", "3D BRAVO", "SPGR", "t1_mprage_sag_p2_iso", "tfl3d",
])
def test_t1_descriptions_are_recognized(description):
    candidate = score_series(_series(description))
    assert any("설명" in r for r in candidate.reasons)


@pytest.mark.parametrize("description", ["AX FLAIR", "DWI", "SWI", "Localizer"])
def test_non_t1_descriptions_are_not_recognized(description):
    candidate = score_series(_series(description))
    assert not any("설명" in r for r in candidate.reasons)


@pytest.mark.parametrize("description", ["AX T2 SAT1", "T1 MAP", "T1rho"])
def test_t1_pattern_false_positives_are_a_known_spec_limitation(description):
    """T1_DESCRIPTION_PATTERN(스펙 §6.3에 문자열 그대로 고정됨)에는 단어 경계가
    없어 "SAT1"의 "T1", "T1 MAP"/"T1rho"의 "t1"이 모두 매치된다.

    이는 스펙 문구를 그대로 옮긴 결과라 이 브랜치에서 고칠 수 없는 알려진
    한계다 — 조용히 "고쳐서" 회귀를 감추지 않도록 현재 동작(매치됨)을 그대로
    고정해 문서화해 둔다. 패턴 자체를 좁히려면 스펙 개정이 먼저 필요하다.
    """
    candidate = score_series(_series(description))
    assert any("설명" in r for r in candidate.reasons)


@pytest.mark.parametrize("vox", [(0.0, 1.0, 1.0), (-1.0, 1.0, 1.0), (0.0, 0.0, 0.0)])
def test_malformed_voxel_size_gets_no_isotropy_or_1mm_bonus(vox):
    """voxel_size_mm에 0 또는 음수가 섞여 있어도 예외 없이 처리되고, 그런
    값은 '유효한 voxel'로 치지 않아 등방성/1mm 근접 가점을 받지 못해야 한다.

    지금까지 이 경로를 실제로 실행하는 테스트가 없었다 — 처리 자체는
    올바르지만(0 초과인 값만 valid로 거르는 필터) 검증되지 않은 상태였다.
    """
    candidate = score_series(_series("MPRAGE", vox=vox))
    assert not any("등방성" in r for r in candidate.reasons)
    assert not any("1mm" in r for r in candidate.reasons)


def test_anisotropic_voxel_loses_points():
    isotropic = score_series(_series("MPRAGE", vox=(1.0, 1.0, 1.0)))
    anisotropic = score_series(_series("MPRAGE", vox=(0.5, 0.5, 5.0)))

    assert isotropic.score > anisotropic.score


def test_thin_stack_loses_points():
    thick = score_series(_series("MPRAGE", slices=176))
    thin = score_series(_series("MPRAGE", slices=40))

    assert thick.score > thin.score


def test_slice_threshold_is_128():
    """스펙 §6.3 — 슬라이스 128장 이상."""
    at_threshold = score_series(_series("MPRAGE", slices=128))
    below = score_series(_series("MPRAGE", slices=127))

    assert at_threshold.score > below.score


def test_ranking_puts_best_first():
    ranked = rank_series([
        _series("Localizer", 3, (1.5, 1.5, 8.0), "2D", number=1),
        _series("AX T2 FLAIR", 30, (0.5, 0.5, 5.0), "2D", number=2),
        _series("Sag 3D T1 MPRAGE", 176, (1.0, 1.0, 1.0), "3D", number=3),
    ])

    assert [c.series.series_number for c in ranked] == [3, 2, 1]


def test_ranking_breaks_ties_by_slice_count():
    ranked = rank_series([
        _series("MPRAGE", 160, (1.0, 1.0, 1.0), "3D", number=7),
        _series("MPRAGE", 192, (1.0, 1.0, 1.0), "3D", number=4),
    ])

    assert ranked[0].series.series_number == 4


def test_ranking_empty_list():
    assert rank_series([]) == []


def test_reasons_are_human_readable():
    candidate = score_series(_series("MPRAGE", 176, (1.0, 1.0, 1.0), "3D"))

    joined = " ".join(candidate.reasons)
    assert "등방성" in joined
    assert "슬라이스" in joined
    assert "설명" in joined


@pytest.mark.dcm2niix
@pytest.mark.realdata
def test_real_data_ranking_puts_t1_first(real_data_dir, dcm2niix_bin, tmp_path):
    """실제 스터디에서 1위가 T1인지 눈으로 확인한다.

    자동 확정은 하지 않으므로 이 테스트는 순위 출력이 목적이다. 1위가 T1이
    아니면 점수 가중치를 조정하고 회귀 케이스를 추가한다.
    """
    from mri2mesh.io.dcm2niix import run_dcm2niix
    from mri2mesh.io.ingest import prepare_input

    candidates = sorted(real_data_dir.rglob("*.zip")) or [real_data_dir]
    prepared = prepare_input(candidates[0], tmp_path / "work")
    series = run_dcm2niix(prepared.dicom_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    ranked = rank_series(series)

    print("\n추천 순위:")
    for i, c in enumerate(ranked, 1):
        print(f"  {i}. score={c.score} #{c.series.series_number} "
              f"{c.series.series_description!r} slices={c.series.slices} "
              f"vox={c.series.voxel_size_mm}")
        for reason in c.reasons:
            print(f"       - {reason}")

    assert ranked[0].score >= ranked[-1].score
