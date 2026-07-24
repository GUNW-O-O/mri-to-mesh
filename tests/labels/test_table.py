"""canonical 표 로딩 (스펙 §3)."""

from __future__ import annotations

import pytest

from seg_and_mesh.labels import (
    CANONICAL_VERSION,
    LabelTableError,
    load_canonical,
)


def test_loads_the_committed_table():
    table = load_canonical()
    assert len(table.entries) == 100


def test_canonical_version_string_matches_spec_field():
    """스펙 §2.2 regions-meta.json의 labelTable 값이다."""
    assert CANONICAL_VERSION == "canonical-v1"
    assert load_canonical().version == "canonical-v1"


def test_lookup_by_fs_id():
    """매칭은 fs_id로 한다 — 이름으로 하면 LUT 버전 변화에 조용히 깨진다."""
    by_fs = load_canonical().by_fs_id()

    assert by_fs[17].name == "Left-Hippocampus"
    assert by_fs[17].color == (220, 216, 20)
    assert by_fs[17].group == "subcortical"
    assert by_fs[17].side == "L"


def test_lookup_by_id_round_trips():
    table = load_canonical()
    by_id = table.by_id()
    for entry in table.entries:
        assert by_id[entry.id] is entry


def test_entries_are_immutable():
    """표가 계약이므로 실수로 고쳐지면 안 된다."""
    entry = load_canonical().entries[0]
    with pytest.raises(Exception):
        entry.id = 999


def test_unknown_version_raises():
    with pytest.raises(LabelTableError):
        load_canonical("does-not-exist")


def test_load_is_cached():
    """리맵마다 파일을 다시 읽지 않는다."""
    assert load_canonical() is load_canonical()
