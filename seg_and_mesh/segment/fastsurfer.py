"""FastSurfer를 docker로 실행 (스펙 §5.1, §6.4).

brainds는 FastSurfer를 호스트에 설치해 네이티브로 돌렸지만, 우리는 spec §5.1
대로 docker socket으로 형제 컨테이너를 띄운다(로컬 단독·비노출 전제).

검증된 실행(RTX 3070, deepmi/fastsurfer:cuda-v2.5.4): 2분28초, orig 256³ uint8,
withCC 세그 100 라벨. `--user root`(docker)와 `--allow_root`(FastSurfer) 둘 다
필요하다 — 하나만으론 nonroot/root 가드 중 하나에 걸린다.

이 모듈은 orig.nii.gz(conform T1, uint8)를 만들고 withCC 세그 파일 경로를
낸다. 라벨 리맵(canonical seg.nii.gz)과 메시는 downstream(labels·mesh)이다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

#: 세그 입력 파일(스펙 §14 — withCC 판, 뇌량 5개 포함). --no_cc를 절대 주지
#: 않는다. 이 파일이 리맵의 입력이다.
SEG_SOURCE_FILE = "aparc.DKTatlas+aseg.deep.withCC.mgz"

#: conform된 T1. 0~255 uchar이라 uint8이 원본 그대로다(스펙 §2.1).
_ORIG_FILE = "orig.mgz"


class SegmentError(RuntimeError):
    """FastSurfer 실행이나 출력 추출이 실패했다.

    메시지에 입력 파일명·경로를 넣지 않는다 — 환자 식별자가 들어갈 수 있어
    로그·status에 새면 PHI 유출이다(스펙 §12).
    """


@dataclass(frozen=True)
class SegmentResult:
    """FastSurfer 산출물 경로."""

    subject_dir: Path       # <sd>/<sid>
    orig_path: Path         # orig.nii.gz (conform T1, uint8)
    seg_source_path: Path   # withCC 세그 .mgz — 리맵의 입력


def build_fastsurfer_command(
    t1_path: Path,
    subject_dir_root: Path,
    sid: str,
    image: str,
    threads: int = 8,
    *,
    host_t1_dir: Path | None = None,
    host_subject_dir_root: Path | None = None,
) -> list[str]:
    """docker run 명령을 만든다.

    입력 볼륨의 폴더를 /data(읽기전용), 출력 루트를 /output으로 마운트한다.
    FastSurfer가 /output/<sid>/mri/에 결과를 쓴다.

    host_t1_dir / host_subject_dir_root: `-v` 마운트 원본으로 쓸 호스트 경로.
        api가 컨테이너 안에서 돌 때 필요하다 — 도커 데몬은 호스트에 있어
        api 내부 경로를 모른다. None이면 t1_path.parent / subject_dir_root를
        그대로 쓴다(호스트 직접 실행).
    """
    t1_path = Path(t1_path)
    mount_t1_dir = Path(host_t1_dir) if host_t1_dir else t1_path.parent
    mount_sd = Path(host_subject_dir_root) if host_subject_dir_root else Path(subject_dir_root)
    return [
        "docker", "run", "--rm", "--gpus", "all", "--user", "root",
        "-v", f"{mount_t1_dir}:/data:ro",
        "-v", f"{mount_sd}:/output",
        image,
        "--t1", f"/data/{t1_path.name}",
        "--sd", "/output",
        "--sid", sid,
        "--seg_only",
        "--vox_size", "1.0",
        "--threads", str(threads),
        "--allow_root",
    ]


def _write_orig_nifti(orig_mgz: Path, orig_nii: Path) -> None:
    """orig.mgz(conform T1) -> orig.nii.gz. uint8로 저장하고 기하는 그대로
    옮긴다(스펙 §2.1). conform 출력은 0~255라 uint8이 무손실이다."""
    img = nib.load(orig_mgz)
    data = np.asanyarray(img.dataobj).astype(np.uint8)
    out = nib.Nifti1Image(data, img.affine)
    out.header.set_zooms(tuple(float(z) for z in img.header.get_zooms()[:3]))
    out.set_data_dtype(np.uint8)
    nib.save(out, orig_nii)


def run_fastsurfer(
    t1_path,
    subject_dir_root,
    image: str,
    *,
    sid: str = "case",
    threads: int = 8,
    runner=subprocess.run,
    host_t1_dir: Path | None = None,
    host_subject_dir_root: Path | None = None,
) -> SegmentResult:
    """FastSurfer를 docker로 돌리고 orig.nii.gz를 만든다.

    Args:
        t1_path: 입력 T1 (.nii/.nii.gz). dcm2niix·시리즈 선택은 io가 끝냈다.
        subject_dir_root: FastSurfer `--sd`. 결과는 <root>/<sid>/에 쌓인다.
        image: FastSurfer 이미지 태그(고정, latest 금지).
        sid: subject id (폴더명).
        threads: CPU 스레드.
        runner: subprocess.run 대체 지점(테스트에서 목으로 바꾼다, 스펙 §13).
        host_t1_dir / host_subject_dir_root: `-v` 마운트 원본으로 쓸 호스트
            경로. api가 컨테이너 안에서 돌 때 필요하다 — 도커 데몬은
            호스트에 있어 api 내부 경로를 모른다. None이면(호스트 직접
            실행) t1_path.parent / subject_dir_root를 그대로 쓴다. 파일을
            실제로 읽고 쓰는 경로(subject_dir_root 자체)는 이 인자와 무관하게
            항상 api 자기 파일시스템 경로다 — 마운트 인자에만 쓴다.

    Returns:
        SegmentResult(subject_dir, orig_path, seg_source_path).

    Raises:
        SegmentError: FastSurfer가 실패했거나(0 아닌 종료) 기대 출력이 없을 때.
            메시지에 입력 파일명을 넣지 않는다(스펙 §12 PHI).
    """
    t1_path = Path(t1_path)
    subject_dir_root = Path(subject_dir_root)
    subject_dir_root.mkdir(parents=True, exist_ok=True)

    cmd = build_fastsurfer_command(
        t1_path, subject_dir_root, sid, image, threads,
        host_t1_dir=host_t1_dir, host_subject_dir_root=host_subject_dir_root,
    )
    proc = runner(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["(stderr 없음)"]
        raise SegmentError(f"FastSurfer 실패 (종료 {proc.returncode}): {tail[0]}")

    mri_dir = subject_dir_root / sid / "mri"
    orig_mgz = mri_dir / _ORIG_FILE
    seg_source = mri_dir / SEG_SOURCE_FILE

    if not orig_mgz.is_file():
        raise SegmentError("FastSurfer 출력에 orig.mgz가 없다")
    if not seg_source.is_file():
        raise SegmentError(f"FastSurfer 출력에 {SEG_SOURCE_FILE}가 없다")

    orig_nii = mri_dir / "orig.nii.gz"
    _write_orig_nifti(orig_mgz, orig_nii)

    return SegmentResult(
        subject_dir=subject_dir_root / sid,
        orig_path=orig_nii,
        seg_source_path=seg_source,
    )
