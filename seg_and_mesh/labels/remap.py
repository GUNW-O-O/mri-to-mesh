"""정적 LUT 리맵 (스펙 §3).

FastSurfer가 낸 불연속 FreeSurfer 번호를 canonical 표의 조밀 번호로 바꾼다.
표에 없는 라벨은 0(배경)이 된다 — 이웃 번호로 붙이거나 잘라내지 않는다.

라벨별 마스크를 볼륨 전체에 한 번씩 돌리지 않고 조밀 룩업 배열 하나로
색인한다. 라벨이 100개면 전자는 볼륨을 100번 훑는다.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np

from seg_and_mesh.labels.table import CanonicalTable, load_canonical


class RemapError(RuntimeError):
    """세그멘테이션을 읽지 못했거나 리맵 결과를 쓰지 못했다."""


@dataclass(frozen=True)
class RemapResult:
    """리맵 결과와, 메타데이터 작성에 필요한 부피 재료.

    voxel_counts는 리맵 **후** 번호(canonical id)를 키로 쓰되, 세는 시점은
    리맵 전 볼륨이다 — 스펙 §2.2가 요구하는 값이 그것이다. 배경(0)은 넣지
    않는다.
    """

    seg_path: Path
    voxel_counts: dict[int, int] = field(compare=False)
    voxel_volume_mm3: float = 0.0

    def volume_mm3(self, label_id: int) -> float:
        """스펙 §2.2 volumeMm3. 케이스에 없는 구조는 0.0이다."""
        return self.voxel_counts.get(label_id, 0) * self.voxel_volume_mm3


def build_lookup(table: CanonicalTable) -> np.ndarray:
    """fs_id로 색인하면 canonical id가 나오는 조밀 배열을 만든다.

    표에 없는 자리는 0이다. dtype이 uint8이라 결과를 그대로 저장할 수 있다 —
    라벨이 100개뿐이므로 안전하다.
    """
    size = max(e.fs_id for e in table.entries) + 1
    lut = np.zeros(size, dtype=np.uint8)
    for entry in table.entries:
        lut[entry.fs_id] = entry.id
    return lut


def remap_segmentation(
    seg_in: Path,
    seg_out: Path,
    table: CanonicalTable | None = None,
) -> RemapResult:
    """세그멘테이션을 canonical 번호로 바꿔 uint8 NIfTI로 쓴다.

    affine과 voxel 간격은 입력에서 그대로 옮긴다 — 메시가 이 기하로 월드
    좌표를 만들기 때문에 어긋나면 메시가 통째로 밀린다.

    Args:
        seg_in: FastSurfer가 낸 세그멘테이션 (.mgz 또는 .nii.gz).
        seg_out: 쓸 경로. 상위 폴더가 없으면 만든다.
        table: 쓸 표. None이면 커밋된 canonical-v1.

    Raises:
        RemapError: 입력을 읽지 못했거나 출력을 쓰지 못했을 때.
    """
    table = table or load_canonical()
    seg_in = Path(seg_in)
    seg_out = Path(seg_out)

    try:
        img = nib.load(seg_in)
        data = np.asanyarray(img.dataobj)
    except (nib.filebasedimages.ImageFileError, OSError, EOFError, zlib.error) as exc:
        raise RemapError(f"세그멘테이션을 읽지 못했다: {seg_in}") from exc

    lut = build_lookup(table)
    # 음수는 룩업 인덱스가 배열 뒤에서부터 잡혀 엉뚱한 라벨이 된다.
    # 범위 밖 큰 값도 배경으로 눌러야 IndexError가 나지 않는다.
    source = np.asarray(data, dtype=np.int64)
    source = np.where((source >= 0) & (source < len(lut)), source, 0)
    remapped = lut[source]

    values, counts = np.unique(remapped, return_counts=True)
    voxel_counts = {
        int(v): int(c) for v, c in zip(values, counts) if int(v) != 0
    }

    zooms = img.header.get_zooms()[:3]
    voxel_volume = float(np.prod([float(z) for z in zooms]))

    seg_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out_img = nib.Nifti1Image(remapped, img.affine)
        out_img.header.set_zooms(tuple(float(z) for z in zooms))
        out_img.set_data_dtype(np.uint8)
        nib.save(out_img, seg_out)
    except (OSError, nib.filebasedimages.ImageFileError) as exc:
        raise RemapError(f"리맵 결과를 쓰지 못했다: {seg_out}") from exc

    return RemapResult(
        seg_path=seg_out,
        voxel_counts=voxel_counts,
        voxel_volume_mm3=voxel_volume,
    )
