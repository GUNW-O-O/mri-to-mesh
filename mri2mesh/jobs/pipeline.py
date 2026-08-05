"""파이프라인 오케스트레이션 (스펙 §6, §12).

io → (게이트) → segment → remap → mesh를 순서대로 호출하고 status.json을
갱신한다. FastSurfer 실패 등은 record_error로 PHI-안전하게 남긴다.

시리즈 선택은 게이트다(스펙 §6.3). ingest_job은 랭킹까지만 하고 멈춘다.
사용자가 고르면 run_segmentation_and_mesh가 이어 돈다.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from mri2mesh.io import (
    Dcm2niixError,
    SourceKind,
    UnsafeArchiveError,
    UnsupportedInputError,
    describe_nifti,
    prepare_input,
    rank_series,
    run_dcm2niix,
)
from mri2mesh.io.deface import deface_nifti
from mri2mesh.io.dicom_meta import build_meta, read_meta, write_meta
from mri2mesh.io.nifti_anon import NiftiAnonError, anonymize_nifti
from mri2mesh.jobs.layout import JobPaths, to_host_path
from mri2mesh.jobs.meta import build_regions_meta, write_regions_meta
from mri2mesh.jobs.status import (
    JobStatus,
    now_iso,
    read_status,
    record_error,
    write_status,
)
from mri2mesh.labels import RemapError, load_canonical, remap_segmentation
from mri2mesh.mesh import GenerateError, baseline_params, generate_variant
from mri2mesh.segment import SEG_SOURCE_FILE, SegmentError, run_fastsurfer


def _load_sidecar_dict(sidecar_path) -> dict:
    """사이드카 JSON을 dict로 로드한다. 없거나 오류면 {}."""
    if sidecar_path is None:
        return {}
    try:
        return json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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


def ingest_job(paths: JobPaths, src: Path, filename: str, *, dcm2niix_runner=None, original_filenames=None) -> JobStatus:
    """io 단계. 랭킹 후 awaiting_series로 멈춘다(스펙 §6.3 게이트).

    dcm2niix_runner: 지금은 받기만 하고 안 쓴다 — io.run_dcm2niix에는 주입
        가능한 runner 인자가 없다(binary 교체만 지원). 나중에 그 인자가
        생기면 여기서 이어준다. 호출자는 이 값이 지금 dcm2niix 실행을
        가로채지 않는다는 걸 알아야 한다.
    original_filenames: 익명화 감사를 위한 원본 파일명 목록(dicom-meta.json용).
    """
    # io 실패도(스펙 §12) record_error를 거쳐야 한다 — 그래야 status.json이
    # "running"에 멈춰 있지 않고, 원본 경로가 섞인 예외 메시지가
    # sanitize_stderr 없이 그대로 새지 않는다. 그러려면 실패해도 되돌아갈
    # status.json이 미리 있어야 하므로, 여기서 먼저 하나 써 둔다.
    #
    # case_name: 호출자(web/app.py의 upload)가 이미 사용자 라벨을 담아
    # status.json을 써 둔 채로 여기 들어올 수 있다 — 그 라벨을 모르는
    # job_id로 덮어쓰면 안 되므로, 디스크에 이미 있으면 그대로 이어받는다.
    # 없으면(직접 ingest_job을 부르는 테스트 등) job_id로 대체한다.
    if paths.status_file.is_file():
        case_name = read_status(paths).case_name
    else:
        case_name = paths.root.name
    status = JobStatus(
        job_id=paths.root.name,
        case_name=case_name,
        created_at=now_iso(),
        updated_at=now_iso(),
        state="running",
        step="io",
        input={"filename": filename, "bytes": Path(src).stat().st_size},
    )
    write_status(paths, status)

    try:
        prepared = prepare_input(Path(src), paths.nifti_dir)
        if prepared.nifti_file is not None:
            series = [describe_nifti(prepared.nifti_file, None)]
        else:
            series = run_dcm2niix(prepared.dicom_dir, paths.nifti_dir)
        ranked = rank_series(series)
    except (Dcm2niixError, UnsupportedInputError, UnsafeArchiveError) as exc:
        record_error(paths, "io", None, str(exc))
        return read_status(paths)

    # dicom-meta (익명화 감사) — 부가기능이므로 실패해도 파이프라인을 막지 않는다.
    try:
        if prepared.nifti_file is not None:
            _meta = build_meta(
                source="nifti", original_filenames=original_filenames,
                dicom_file=None, nifti_path=prepared.nifti_file, sidecar=None,
            )
        else:
            rep = ranked[0].series
            _meta = build_meta(
                source="dicom", original_filenames=original_filenames,
                dicom_file=prepared.dicom_files[0],
                nifti_path=rep.nifti_path,
                sidecar=_load_sidecar_dict(rep.sidecar_path),
            )
        write_meta(paths.dicom_meta_file, _meta)
    except Exception:  # noqa: BLE001 — 감사 부가기능 실패가 파이프라인을 막으면 안 된다
        pass  # 감사 메타 없이 진행

    status = read_status(paths)
    status.state = "awaiting_series"
    status.series = [_series_dict(c) for c in ranked]
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
    params=None,
    deface: bool = False,
    jobs_root: Path | None = None,
    host_jobs_root: Path | None = None,
) -> JobStatus:
    """선택 후: segment → remap → mesh(첫 변형) → 4파일. 실패는 PHI-안전 기록.

    params: 사용자가 시리즈 선택 화면에서 고른 메쉬 파라미터(MeshParams).
        None이면 baseline_params()(brainds 프로덕션 기준값)로 첫 변형을 만든다.
    deface: True면 익명화한 orig.nii.gz에 얼굴 마스킹(defacing)을 적용한다.
        현재 미구현(스텁) — True면 NotImplementedError로 명시 실패한다. UI 토글은
        예정 상태다.

    jobs_root/host_jobs_root: api가 컨테이너 안에서 돌 때, 형제 FastSurfer
        컨테이너의 `-v` 인자를 호스트 경로로 바꾸기 위한 짝(Task 1
        to_host_path). jobs_root가 None이면 변환하지 않는다.
    """
    table = table or load_canonical()
    status = read_status(paths)
    status.state = "running"
    status.selected_series = {"niftiPath": str(selected_nifti)}
    # 서빙(_strip_selected)이 series와 대조해 index를 찾지만, 선택 시점의 얕은
    # 메타를 같이 남겨 두면 series가 나중에 비어도 표시가 안정적이다.
    for c in status.series:
        if c.get("niftiPath") == str(selected_nifti):
            status.selected_series.update({
                "description": c.get("description"),
                "slices": c.get("slices"),
                "voxelSizeMm": c.get("voxelSizeMm"),
            })
            break
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

    # --- remap (orig 복사 포함) ---
    status = read_status(paths)
    status.step = "remap"
    status.engine = {"name": "fastsurfer", "version": _image_version(image), "device": "cuda"}
    write_status(paths, status)

    seg_canon = paths.seg_dir / "seg.nii.gz"
    try:
        # orig을 seg/로 옮긴다
        orig_dst = paths.seg_dir / "orig.nii.gz"
        shutil.copy2(seg_result.orig_path, orig_dst)

        remap_segmentation(seg_result.seg_source_path, seg_canon, table)

        # 반출 필수 요소(orig·seg)를 영상 구성요소만 남기고 익명화한다 — 헤더
        # 잔여 메타·확장영역 제거. 실패는 remap 실패로 다뤄 진행을 막는다
        # (익명화 안 된 NIfTI를 산출하면 안 된다).
        anonymize_nifti(orig_dst)
        anonymize_nifti(seg_canon)
        # 얼굴 마스킹(옵션, 현재 스텁). deface=True면 미구현이라 명시 실패한다.
        if deface:
            deface_nifti(orig_dst)
    except (OSError, RemapError, NiftiAnonError) as exc:
        record_error(paths, "remap", None, str(exc))
        return read_status(paths)

    # 세그·익명화 성공 — 원본·중간물을 정리한다. 익명 orig.nii.gz·seg.nii.gz만
    # 남기고, raw 업로드(input)·dcm2niix 중간물과 비선택 시리즈(nifti)·FastSurfer
    # 작업폴더(fs, 익명화 안 된 orig.mgz 포함)를 지운다. 재작업은 원본 재수입
    # 플로우로 한다. 정리 실패는 파이프라인을 막지 않는다(부가 작업).
    _cleanup_source(paths)
    _strip_dicom_before(paths)

    # --- mesh (기본 변형 하나) ---
    status = read_status(paths)
    status.step = "mesh"
    write_status(paths, status)

    try:
        params = params or baseline_params()
        # variantId를 파라미터 해시로 먼저 계산해(스펙 §7) 바로 올바른 폴더에
        # 생성한다 — 임시 폴더에 썼다가 옮기는 우회가 필요 없다.
        variant_id = params.variant_id(1)
        vdir = paths.variant_dir(variant_id)
        variant = generate_variant(seg_canon, vdir, params, index=1, table=table)

        meta = build_regions_meta(
            seg_canon, variant.regions, variant.variant_id,
            engine=status.engine, seg_file=SEG_SOURCE_FILE,
        )
        write_regions_meta(vdir / "regions-meta.json", meta)
    except (GenerateError, OSError) as exc:
        record_error(paths, "mesh", None, str(exc))
        return read_status(paths)

    # --- 완료 ---
    status = read_status(paths)
    status.state = "done"
    status.step = "done"
    status.variants = [{
        "variantId": variant.variant_id,
        "bytes": variant.metrics["glbBytes"],
        "createdAt": variant.params["createdAt"],
        "params": _variant_params_view(variant.params),
    }]
    write_status(paths, status)
    return read_status(paths)


def _cleanup_source(paths: JobPaths) -> None:
    """세그 성공 후 원본·중간물 삭제. 익명 orig.nii.gz·seg.nii.gz(seg/)와 mesh/만
    남긴다. 실패해도 파이프라인을 막지 않는다(ignore_errors)."""
    for d in (paths.input_dir, paths.nifti_dir, paths.fs_dir):
        shutil.rmtree(d, ignore_errors=True)


def _strip_dicom_before(paths: JobPaths) -> None:
    """dicom-meta.json에서 before(원본 DICOM 헤더값)·원본 파일명을 제거한다 —
    원본 PHI 그 자체이므로 원본을 지우면 이것도 지운다. 익명화 감사(after/removed)는
    남긴다. 파일이 없거나 실패해도 조용히 넘어간다(부가 작업)."""
    p = paths.dicom_meta_file
    if not p.is_file():
        return
    try:
        meta = read_meta(p)
        meta["before"] = None
        meta["originalFilenames"] = []
        write_meta(p, meta)
    except (OSError, ValueError):
        pass


def _variant_params_view(p: dict) -> dict:
    """status.variants에 실을 표시용 파라미터부(사용자가 고른 축만). segSource·
    labelTable·variantId·createdAt는 뺀다(PHI/중복 회피, 표시에 불필요)."""
    return {k: p[k] for k in ("preprocess", "extractor", "smoothing", "decimation", "minVoxel") if k in p}


def _image_version(image: str) -> str:
    """이미지 태그에서 버전 문자열을 뽑는다(예: deepmi/fastsurfer:cuda-v2.5.4 → cuda-v2.5.4)."""
    return image.rsplit(":", 1)[-1] if ":" in image else image


def add_variant(paths: JobPaths, params, *, table=None, progress_cb=None) -> dict:
    """done 잡의 seg 캐시에서 메쉬만 다시 만들어 변형을 추가한다.

    같은 파라미터(해시 일치)면 생성하지 않고 기존 variantId를 돌려준다.
    progress_cb(done, total): 라벨 단위 진행 콜백(UI 진행바용).
    Raises:
        ValueError: 잡이 done이 아니거나 seg.nii.gz가 없을 때.
    """
    table = table or load_canonical()
    status = read_status(paths)
    seg_canon = paths.seg_dir / "seg.nii.gz"
    if status.state != "done" or not seg_canon.is_file():
        raise ValueError("변형 생성은 done 잡에서만 가능하다")

    h = params.param_hash()
    for v in status.variants:
        if v["variantId"].endswith(f"-{h}"):
            return {"variantId": v["variantId"], "deduped": True}

    index = len(status.variants) + 1
    variant_id = params.variant_id(index)
    vdir = paths.variant_dir(variant_id)
    variant = generate_variant(seg_canon, vdir, params, index=index, table=table,
                               progress_cb=progress_cb)

    meta = build_regions_meta(
        seg_canon, variant.regions, variant.variant_id,
        engine=status.engine, seg_file=SEG_SOURCE_FILE,
    )
    write_regions_meta(vdir / "regions-meta.json", meta)

    status = read_status(paths)
    status.variants.append({
        "variantId": variant.variant_id,
        "bytes": variant.metrics["glbBytes"],
        "createdAt": variant.params["createdAt"],
        "params": _variant_params_view(variant.params),
    })
    write_status(paths, status)
    return {"variantId": variant.variant_id, "deduped": False}
