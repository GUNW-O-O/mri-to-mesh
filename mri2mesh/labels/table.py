"""canonical 라벨 표 로딩 (스펙 §3).

표는 저장소에 커밋된 labels/canonical-v1.tsv 하나다. 런타임에 FreeSurfer
LUT를 읽지 않는다 — 그러면 이미지 버전에 따라 번호가 흔들린다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: 스펙 §2.2 regions-meta.json의 labelTable 필드에 그대로 들어간다.
CANONICAL_VERSION = "canonical-v1"

#: 저장소 루트의 labels/ 폴더. 이 파일은 mri2mesh/labels/table.py이므로
#: 두 단계 올라가면 루트다.
_LABELS_DIR = Path(__file__).resolve().parents[2] / "labels"

_EXPECTED_HEADER = ["id", "fs_id", "name", "group", "side", "r", "g", "b"]


class LabelTableError(RuntimeError):
    """라벨 표를 읽지 못했거나 형식이 계약과 다르다."""


@dataclass(frozen=True)
class LabelEntry:
    """표의 한 행. id는 입력과 무관하게 고정이다 (스펙 §3)."""

    id: int
    fs_id: int
    name: str
    group: str
    side: str
    color: tuple[int, int, int]


@dataclass(frozen=True)
class CanonicalTable:
    """읽어들인 표 전체."""

    version: str
    entries: tuple[LabelEntry, ...]

    def by_fs_id(self) -> dict[int, LabelEntry]:
        """FreeSurfer 원본 번호로 찾는다. 리맵이 쓰는 방향이다."""
        return {e.fs_id: e for e in self.entries}

    def by_id(self) -> dict[int, LabelEntry]:
        """리맵 후 번호로 찾는다. 메타데이터 작성이 쓰는 방향이다."""
        return {e.id: e for e in self.entries}


@lru_cache(maxsize=None)
def load_canonical(version: str = "v1") -> CanonicalTable:
    """labels/canonical-<version>.tsv를 읽는다.

    Raises:
        LabelTableError: 파일이 없거나, 헤더가 계약과 다르거나, 값이 정수로
            해석되지 않거나, id/fs_id가 중복될 때.
    """
    path = _LABELS_DIR / f"canonical-{version}.tsv"
    if not path.is_file():
        raise LabelTableError(f"라벨 표가 없다: {path}")

    entries: list[LabelEntry] = []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader, None)
            if header != _EXPECTED_HEADER:
                raise LabelTableError(
                    f"열 구성이 계약과 다르다: {header} (기대: {_EXPECTED_HEADER})"
                )
            for line_no, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(row) != len(_EXPECTED_HEADER):
                    raise LabelTableError(f"{path}:{line_no} 열 수가 맞지 않는다: {row}")
                entries.append(
                    LabelEntry(
                        id=int(row[0]),
                        fs_id=int(row[1]),
                        name=row[2],
                        group=row[3],
                        side=row[4],
                        color=(int(row[5]), int(row[6]), int(row[7])),
                    )
                )
    except OSError as exc:
        raise LabelTableError(f"라벨 표를 읽지 못했다: {path}") from exc
    except ValueError as exc:
        raise LabelTableError(f"라벨 표에 정수가 아닌 값이 있다: {path}") from exc

    ids = [e.id for e in entries]
    fs_ids = [e.fs_id for e in entries]
    if len(set(ids)) != len(ids):
        raise LabelTableError(f"id가 중복된다: {path}")
    if len(set(fs_ids)) != len(fs_ids):
        raise LabelTableError(f"fs_id가 중복된다: {path}")

    return CanonicalTable(version=f"canonical-{version}", entries=tuple(entries))
