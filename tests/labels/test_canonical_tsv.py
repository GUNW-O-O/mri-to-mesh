"""커밋된 labels/canonical-v1.tsv의 불변식 (스펙 §3, §2.2).

이 표는 계약이다. id가 한 번 정해지면 바뀌면 안 된다 — 이미 만들어진
seg.nii.gz와 GLB 노드명(label_<id>)이 그 번호를 가리키고 있다.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

TSV = Path(__file__).resolve().parents[2] / "labels" / "canonical-v1.tsv"

EXPECTED_HEADER = ["id", "fs_id", "name", "group", "side", "r", "g", "b"]
VALID_GROUPS = {
    "cortex", "subcortical", "ventricle", "wm",
    "cerebellum", "brainstem", "cc", "other",
}
VALID_SIDES = {"L", "R", "M"}


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with TSV.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        assert header == EXPECTED_HEADER, f"열 구성이 다르다: {header}"
        return [dict(zip(header, row)) for row in reader if row]


def test_row_count_is_the_measured_fastsurfer_output_set(rows):
    """FastSurfer DKT + 뇌량 = 100개. 이미지 안에서 직접 계산해 확인한 수다."""
    assert len(rows) == 100


def test_ids_are_dense_and_start_at_one(rows):
    """id는 1부터 빈틈없이 이어진다. 0은 배경이라 표에 없다."""
    ids = [int(r["id"]) for r in rows]
    assert ids == list(range(1, len(rows) + 1))


def test_ids_fit_in_uint8(rows):
    """seg.nii.gz를 uint8로 쓴다. id가 255를 넘으면 조용히 잘린다."""
    assert max(int(r["id"]) for r in rows) <= 255


def test_fs_ids_are_unique(rows):
    """fs_id가 겹치면 리맵 룩업이 어느 쪽을 이길지 알 수 없다."""
    fs_ids = [int(r["fs_id"]) for r in rows]
    assert len(set(fs_ids)) == len(fs_ids)


def test_corpus_callosum_labels_are_present(rows):
    """뇌량 5개는 스펙 §14가 확정한 항목이다.

    FastSurfer_ColorLUT.tsv에는 251~255가 없다 — FastSurferCC 모듈이 따로
    만들어 withCC 파일에 칠해 넣는다. 표를 만들 때 이걸 빠뜨리기 쉽다.
    """
    fs_ids = {int(r["fs_id"]) for r in rows}
    assert {251, 252, 253, 254, 255} <= fs_ids

    cc = [r for r in rows if r["group"] == "cc"]
    assert len(cc) == 5
    assert all(r["side"] == "M" for r in cc)


def test_both_hemispheres_have_all_31_dkt_regions(rows):
    """좌우 피질이 31개씩이어야 한다.

    FastSurfer_ColorLUT.tsv는 시상면 네트워크용 병합 목록이라 우반구 외측
    피질 17개가 빠져 있다. 재측화를 빠뜨리면 여기서 걸린다.
    """
    fs_ids = {int(r["fs_id"]) for r in rows}
    lh = {i for i in fs_ids if 1000 < i < 2000}
    rh = {i for i in fs_ids if i > 2000}

    assert len(lh) == 31, f"좌반구 피질이 31개가 아니다: {len(lh)}"
    assert len(rh) == 31, f"우반구 피질이 31개가 아니다: {len(rh)}"
    assert {i + 1000 for i in lh} == rh, "좌우 피질 짝이 맞지 않는다"


def test_groups_and_sides_are_from_the_fixed_vocabulary(rows):
    """스펙 §2.2가 정한 값 밖이 나오면 소비 측 필터가 조용히 실패한다."""
    for r in rows:
        assert r["group"] in VALID_GROUPS, f"{r['name']}: group={r['group']}"
        assert r["side"] in VALID_SIDES, f"{r['name']}: side={r['side']}"


def test_colors_are_bytes(rows):
    for r in rows:
        for channel in ("r", "g", "b"):
            value = int(r[channel])
            assert 0 <= value <= 255, f"{r['name']}: {channel}={value}"


def test_hippocampus_matches_the_spec_example(rows):
    """스펙 §2.2 예시 JSON과 일치하는지 본다 (색 220 216 20)."""
    by_fs = {int(r["fs_id"]): r for r in rows}

    left = by_fs[17]
    assert left["name"] == "Left-Hippocampus"
    assert left["group"] == "subcortical"
    assert left["side"] == "L"
    assert [left["r"], left["g"], left["b"]] == ["220", "216", "20"]

    right = by_fs[53]
    assert right["name"] == "Right-Hippocampus"
    assert right["side"] == "R"


def test_left_right_names_carry_matching_sides(rows):
    """이름의 좌우와 side 열이 어긋나면 좌우 필터가 틀린다."""
    for r in rows:
        name = r["name"]
        if name.startswith("Left-") or name.startswith("ctx-lh-"):
            assert r["side"] == "L", f"{name}: side={r['side']}"
        elif name.startswith("Right-") or name.startswith("ctx-rh-"):
            assert r["side"] == "R", f"{name}: side={r['side']}"


def test_cortical_labels_are_group_cortex(rows):
    for r in rows:
        if int(r["fs_id"]) > 1000 and int(r["fs_id"]) < 3000:
            assert r["group"] == "cortex", f"{r['name']}: group={r['group']}"
