"""파이프라인 오케스트레이션 (스펙 §6, §12).

io → (게이트) → segment → remap → mesh를 순서대로 호출하고 status.json을
갱신한다. FastSurfer 실패 등은 record_error로 PHI-안전하게 남긴다.

시리즈 선택은 게이트다(스펙 §6.3). ingest_job은 랭킹까지만 하고 멈춘다.
사용자가 고르면 run_segmentation_and_mesh가 이어 돈다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from seg_and_mesh.io import (
    describe_nifti,
    prepare_input,
    rank_series,
    run_dcm2niix,
)
from seg_and_mesh.jobs.layout import JobPaths, to_host_path
from seg_and_mesh.jobs.meta import build_regions_meta, write_regions_meta
from seg_and_mesh.jobs.status import (
    JobStatus,
    now_iso,
    read_status,
    record_error,
    write_status,
)
from seg_and_mesh.labels import load_canonical, remap_segmentation
from seg_and_mesh.mesh import default_params, generate_variant
from seg_and_mesh.segment import SEG_SOURCE_FILE, SegmentError, run_fastsurfer


def _series_dict(candidate) -> dict:
    s = candidate.series
    return {
        "niftiPath": str(s.nifti_path),
        "description": s.series_description,
        "slices": s.slices,
        "voxelSizeMm": list(s.voxel_size_mm),
        "acquisitionType": s.acquisition_type,
        "score": candidate.score,
        "reasons": candidate.reasons,
    }


def ingest_job(paths: JobPaths, src: Path, filename: str, *, dcm2niix_runner=None) -> JobStatus:
    """io 단계. 랭킹 후 awaiting_series로 멈춘다(스펙 §6.3 게이트)."""
    prepared = prepare_input(Path(src), paths.nifti_dir)

    if prepared.nifti_file is not None:
        series = [describe_nifti(prepared.nifti_file, None)]
    else:
        series = run_dcm2niix(prepared.dicom_dir, paths.nifti_dir)

    ranked = rank_series(series)

    status = JobStatus(
        job_id=paths.root.name,
        case_name=paths.root.name,
        created_at=now_iso(),
        updated_at=now_iso(),
        state="awaiting_series",
        step="io",
        input={"filename": filename, "bytes": Path(src).stat().st_size},
        series=[_series_dict(c) for c in ranked],
    )
    write_status(paths, status)
    return status


def run_segmentation_and_mesh(
    paths: JobPaths,
    selected_nifti: Path,
    image: str,
    *,
    threads: int = 8,
    fastsurfer_runner=None,
    table=None,
    jobs_root: Path | None = None,
    host_jobs_root: Path | None = None,
) -> JobStatus:
    """선택 후: segment → remap → mesh(기본 변형) → 4파일. 실패는 PHI-안전 기록.

    jobs_root/host_jobs_root: api가 컨테이너 안에서 돌 때, 형제 FastSurfer
        컨테이너의 `-v` 인자를 호스트 경로로 바꾸기 위한 짝(Task 1
        to_host_path). jobs_root가 None이면 변환하지 않는다.
    """
    table = table or load_canonical()
    status = read_status(paths)
    status.state = "running"
    status.selected_series = {"niftiPath": str(selected_nifti)}
    status.step = "segment"
    write_status(paths, status)

    # --- segment ---
    try:
        kwargs = {"sid": "case", "threads": threads}
        if fastsurfer_runner is not None:
            kwargs["runner"] = fastsurfer_runner
        if jobs_root is not None:
            # 형제 컨테이너 -v 인자는 호스트 경로여야 한다(Task 1 to_host_path).
            kwargs["host_t1_dir"] = to_host_path(
                selected_nifti.parent, jobs_root=jobs_root, host_jobs_root=host_jobs_root
            )
            kwargs["host_subject_dir_root"] = to_host_path(
                paths.fs_dir, jobs_root=jobs_root, host_jobs_root=host_jobs_root
            )
        seg_result = run_fastsurfer(selected_nifti, paths.fs_dir, image, **kwargs)
    except SegmentError as exc:
        record_error(paths, "segment", None, str(exc))
        return read_status(paths)

    # orig을 seg/로 옮긴다
    orig_dst = paths.seg_dir / "orig.nii.gz"
    shutil.copy2(seg_result.orig_path, orig_dst)

    # --- remap ---
    status = read_status(paths)
    status.step = "remap"
    status.engine = {"name": "fastsurfer", "version": _image_version(image), "device": "cuda"}
    write_status(paths, status)

    seg_canon = paths.seg_dir / "seg.nii.gz"
    remap_segmentation(seg_result.seg_source_path, seg_canon, table)

    # --- mesh (기본 변형 하나) ---
    status = read_status(paths)
    status.step = "mesh"
    write_status(paths, status)

    params = default_params()
    # variantId를 파라미터 해시로 먼저 계산해(스펙 §7) 바로 올바른 폴더에 생성한다
    # — 임시 폴더에 썼다가 옮기는 우회가 필요 없다.
    variant_id = params.variant_id(1)
    vdir = paths.variant_dir(variant_id)
    variant = generate_variant(seg_canon, vdir, params, index=1, table=table)

    meta = build_regions_meta(
        seg_canon, variant.regions, variant.variant_id,
        engine=status.engine, seg_file=SEG_SOURCE_FILE,
    )
    write_regions_meta(vdir / "regions-meta.json", meta)

    # --- 완료 ---
    status = read_status(paths)
    status.state = "done"
    status.step = "done"
    status.variants = [{
        "variantId": variant.variant_id,
        "bytes": variant.metrics["glbBytes"],
        "createdAt": variant.params["createdAt"],
    }]
    write_status(paths, status)
    return read_status(paths)


def _image_version(image: str) -> str:
    """이미지 태그에서 버전 문자열을 뽑는다(예: deepmi/fastsurfer:cuda-v2.5.4 → cuda-v2.5.4)."""
    return image.rsplit(":", 1)[-1] if ":" in image else image
