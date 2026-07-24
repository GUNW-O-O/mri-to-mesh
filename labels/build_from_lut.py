"""FastSurfer 이미지의 LUT 두 개로 labels/canonical-v1.tsv를 생성한다 (스펙 §3).

이건 개발용 스크립트다. 런타임 코드가 아니다 — 스펙 §3은 생성물만 커밋하고
런타임에 LUT를 읽지 않는다고 정했다.

라벨 집합을 정하는 방법:

  FastSurferCNN/config/FastSurfer_ColorLUT.tsv 는 시상면 네트워크용 목록이라
  그대로 쓰면 안 된다. 시상면에서는 좌우를 구분할 수 없어 우반구 외측 피질
  17개가 빠져 있다. FastSurfer는 이 목록을 재측화해서 전체 집합을 만든다.

  뇌량(251~255)은 이 TSV에 아예 없다. FastSurferCC 모듈이 따로 분할하고
  paint_cc_into_pred.py가 asegdkt 결과에 칠해 넣어 withCC 파일을 만든다.

  따라서: TSV(배경 제외) ∪ {빠진 ctx-lh-* 의 짝 ctx-rh-*} ∪ {251..255} = 100개.

이 100개가 맞다는 것은 실제 실행으로 확인했다. RTX 3070에서
`--seg_only --vox_size 1.0 --3T`로 돌린 결과 볼륨의 고유 라벨이 정확히 이
집합이었고, FastSurfer가 stats 단계에서 넘기는 `--ids` 목록과도 일치한다.

이름과 색은 FreeSurferColorLUT.txt에서 가져온다 — 스펙 §3이 정한 출처다.

사용법:
    docker create --name sam-lut deepmi/fastsurfer:cuda-v2.5.4 | Out-Null
    docker cp sam-lut:/fastsurfer/FastSurferCNN/config/FreeSurferColorLUT.txt labels/
    docker cp sam-lut:/fastsurfer/FastSurferCNN/config/FastSurfer_ColorLUT.tsv labels/
    docker container remove sam-lut | Out-Null
    uv run python labels/build_from_lut.py

PowerShell에서 `docker rm`은 `rm` 별칭(Remove-Item)과 충돌할 수 있다.
`docker container remove`를 쓴다.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

LABELS_DIR = Path(__file__).resolve().parent
FREESURFER_LUT = LABELS_DIR / "FreeSurferColorLUT.txt"
FASTSURFER_LUT = LABELS_DIR / "FastSurfer_ColorLUT.tsv"
OUTPUT = LABELS_DIR / "canonical-v1.tsv"

HEADER = ["id", "fs_id", "name", "group", "side", "r", "g", "b"]

#: 뇌량. FastSurfer_ColorLUT.tsv에 없고 FastSurferCC가 따로 만든다.
CC_FS_IDS = range(251, 256)

#: aseg 라벨의 group. 피질(1000 초과)과 뇌량은 규칙으로 정하므로 여기 없다.
_ASEG_GROUPS = {
    2: "wm", 41: "wm", 77: "wm",
    4: "ventricle", 5: "ventricle", 43: "ventricle", 44: "ventricle",
    14: "ventricle", 15: "ventricle",
    7: "cerebellum", 8: "cerebellum", 46: "cerebellum", 47: "cerebellum",
    16: "brainstem",
    10: "subcortical", 11: "subcortical", 12: "subcortical", 13: "subcortical",
    17: "subcortical", 18: "subcortical", 26: "subcortical", 28: "subcortical",
    49: "subcortical", 50: "subcortical", 51: "subcortical", 52: "subcortical",
    53: "subcortical", 54: "subcortical", 58: "subcortical", 60: "subcortical",
    24: "other",              # CSF
    31: "other", 63: "other",  # choroid plexus — 뇌실 안에 있지만 뇌실이 아니다
}


def read_freesurfer_lut(path: Path) -> dict[int, tuple[str, int, int, int]]:
    """FreeSurferColorLUT.txt에서 {fs_id: (name, r, g, b)}를 읽는다.

    형식은 공백 구분 `id name R G B A`이고 주석(#)과 빈 줄이 섞여 있다.
    """
    table: dict[int, tuple[str, int, int, int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        table[int(parts[0])] = (parts[1], int(parts[2]), int(parts[3]), int(parts[4]))
    return table


def read_fastsurfer_membership(path: Path) -> set[int]:
    """FastSurfer_ColorLUT.tsv에서 배경을 뺀 fs_id 집합을 읽는다."""
    ids: set[int] = set()
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        next(reader, None)  # 헤더
        for row in reader:
            if len(row) < 5 or not row[0].strip().isdigit():
                continue
            fs_id = int(row[0])
            if fs_id != 0:
                ids.add(fs_id)
    return ids


def full_label_set(sag_ids: set[int]) -> set[int]:
    """시상면 목록을 재측화하고 뇌량을 더해 실제 출력 집합을 만든다."""
    lh_cortex = {i for i in sag_ids if 1000 < i < 2000}
    mirrored = {i + 1000 for i in lh_cortex}
    return sag_ids | mirrored | set(CC_FS_IDS)


def classify_group(fs_id: int) -> str:
    if fs_id in CC_FS_IDS:
        return "cc"
    if 1000 < fs_id < 3000:
        return "cortex"
    group = _ASEG_GROUPS.get(fs_id)
    if group is None:
        raise SystemExit(
            f"fs_id {fs_id}의 group이 정의되지 않았다. _ASEG_GROUPS에 추가할 것."
        )
    return group


def classify_side(fs_id: int, name: str) -> str:
    if name.startswith("Left-") or name.startswith("ctx-lh-"):
        return "L"
    if name.startswith("Right-") or name.startswith("ctx-rh-"):
        return "R"
    return "M"


def build() -> list[list[str]]:
    if not FREESURFER_LUT.exists() or not FASTSURFER_LUT.exists():
        raise SystemExit(
            f"LUT가 없다. 모듈 docstring의 docker cp 명령으로 먼저 뽑을 것:\n"
            f"  {FREESURFER_LUT}\n  {FASTSURFER_LUT}"
        )

    colors = read_freesurfer_lut(FREESURFER_LUT)
    fs_ids = sorted(full_label_set(read_fastsurfer_membership(FASTSURFER_LUT)))

    missing = [i for i in fs_ids if i not in colors]
    if missing:
        raise SystemExit(f"FreeSurferColorLUT.txt에 없는 fs_id: {missing}")

    rows: list[list[str]] = []
    # id는 fs_id 오름차순으로 1부터 붙인다. 결정론적이고 재생성해도 같은 번호가
    # 나온다 — 스펙 §3이 요구하는 "입력과 무관하게 고정"의 기반이다.
    for new_id, fs_id in enumerate(fs_ids, start=1):
        name, r, g, b = colors[fs_id]
        rows.append([
            str(new_id), str(fs_id), name,
            classify_group(fs_id), classify_side(fs_id, name),
            str(r), str(g), str(b),
        ])
    return rows


def main() -> int:
    rows = build()
    with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"{OUTPUT} — {len(rows)}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
