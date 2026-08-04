"""파이프라인 오케스트레이션 (스펙 §6, §12, §13 — FastSurfer 목)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np

import mri2mesh.jobs.pipeline as pipeline_mod
from mri2mesh.io.dicom_meta import read_meta
from mri2mesh.jobs.layout import job_paths
from mri2mesh.jobs.pipeline import ingest_job, run_segmentation_and_mesh, add_variant
from mri2mesh.jobs.status import JobStatus, read_status, write_status
from mri2mesh.labels import RemapError
from mri2mesh.mesh import GenerateError, baseline_params
from mri2mesh.mesh.params import parse_mesh_params
from mri2mesh.segment import SEG_SOURCE_FILE


def _nifti(path, value_map=None, shape=(16, 16, 16), zooms=(1.0, 1.0, 1.0)):
    vol = np.zeros(shape, np.int16)
    for (i, j, k), v in (value_map or {}).items():
        vol[i, j, k] = v
    img = nib.Nifti1Image(vol, np.diag([*zooms, 1.0]))
    img.header.set_zooms(zooms)
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(img, path)
    return path


def _t1_upload(tmp_path):
    """직접 NIfTI 업로드를 흉내낸다(dcm2niix 없이)."""
    return _nifti(tmp_path / "up" / "input.nii.gz", shape=(16, 16, 16))


def test_ingest_nifti_stops_at_awaiting_series(tmp_path):
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = _t1_upload(tmp_path)
    status = ingest_job(p, src, "input.nii.gz")
    assert status.state == "awaiting_series"
    assert len(status.series) == 1  # 직접 NIfTI는 시리즈 하나
    # 게이트: 아직 segment 안 돌았다
    assert not (p.seg_dir / "seg.nii.gz").exists()


def test_ingest_preserves_existing_case_name(tmp_path):
    """web/app.py의 upload는 ingest_job 부르기 전에 사용자 라벨을 담은
    status.json을 미리 써 둔다 — ingest_job이 자기 status를 새로 쓸 때
    그 라벨을 job_id로 덮어쓰면 안 된다(리뷰 반영: 루트 원인 수정)."""
    p = job_paths(tmp_path / "jobs", "job1").create()
    write_status(p, JobStatus(
        job_id="job1", case_name="라벨X", created_at="t", updated_at="t",
        state="running", step="io", input={"filename": "<file>", "bytes": 0},
    ))
    src = _t1_upload(tmp_path)

    status = ingest_job(p, src, "input.nii.gz")

    assert status.case_name == "라벨X"
    assert read_status(p).case_name == "라벨X"


def test_ingest_nifti_writes_meta_without_before(tmp_path):
    """ingest_job이 original_filenames를 받아 dicom-meta.json을 생성한다.

    NIfTI 직접 입력이므로 before=None, source='nifti'."""
    p = job_paths(tmp_path / "jobs", "jobM").create()
    ingest_job(p, _t1_upload(tmp_path), "input.nii.gz",
               original_filenames=["scan.nii.gz"])
    meta = read_meta(p.dicom_meta_file)
    assert meta["source"] == "nifti"
    assert meta["before"] is None
    assert meta["originalFilenames"] == ["scan.nii.gz"]
    assert meta["after"]["nifti"]["dims"][0] > 0


def _fastsurfer_mock(subject_dir_root, sid):
    """FastSurfer 성공을 흉내낸다 — orig.mgz + withCC 세그를 쓴다."""
    def run(cmd, **kwargs):
        mri = Path(subject_dir_root) / sid / "mri"
        mri.mkdir(parents=True, exist_ok=True)
        nib.save(nib.MGHImage(np.full((16, 16, 16), 100, np.uint8), np.eye(4)),
                 mri / "orig.mgz")
        seg = np.zeros((16, 16, 16), np.int16)
        seg[2:12, 2:12, 2:12] = 17  # 해마 덩어리(min_voxel 넘게)
        nib.save(nib.MGHImage(seg, np.eye(4)), mri / SEG_SOURCE_FILE)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    return run


def test_full_run_produces_four_files(tmp_path):
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = _t1_upload(tmp_path)
    ingest_job(p, src, "input.nii.gz")
    selected = read_status(p).series[0]["niftiPath"]

    status = run_segmentation_and_mesh(
        p, Path(selected), image="fs:tag",
        fastsurfer_runner=_fastsurfer_mock(p.fs_dir, "case"),
    )

    assert status.state == "done"
    assert (p.seg_dir / "seg.nii.gz").is_file()
    assert (p.seg_dir / "orig.nii.gz").is_file()
    # 변형 하나
    assert len(status.variants) == 1
    vid = status.variants[0]["variantId"]
    vdir = p.variant_dir(vid)
    assert (vdir / "regions.glb").is_file()
    assert (vdir / "regions-meta.json").is_file()
    assert (vdir / "metrics.json").is_file()
    assert (vdir / "params.json").is_file()


def test_run_segmentation_translates_mount_paths_for_sibling_container(tmp_path):
    """jobs_root/host_jobs_root가 있으면 -v 마운트만 호스트 경로로 바뀐다.

    api가 컨테이너 안에서 돌 때 도커 데몬(호스트)이 찾을 수 있는 경로여야
    한다(형제 컨테이너). 로컬 파일 I/O(seg/orig 산출)는 여전히 api 자기
    파일시스템 경로(jobs_root 밑) 그대로여야 한다.
    """
    jobs_root = tmp_path / "work" / "jobs"
    host_jobs_root = tmp_path / "host" / "output"
    p = job_paths(jobs_root, "job1").create()
    src = _t1_upload(tmp_path)
    ingest_job(p, src, "input.nii.gz")
    selected = Path(read_status(p).series[0]["niftiPath"])

    captured = {}

    def spy_runner(cmd, **kwargs):
        captured["cmd"] = cmd
        mri = p.fs_dir / "case" / "mri"
        mri.mkdir(parents=True, exist_ok=True)
        nib.save(nib.MGHImage(np.full((16, 16, 16), 100, np.uint8), np.eye(4)),
                  mri / "orig.mgz")
        seg = np.zeros((16, 16, 16), np.int16)
        seg[2:12, 2:12, 2:12] = 17
        nib.save(nib.MGHImage(seg, np.eye(4)), mri / SEG_SOURCE_FILE)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    status = run_segmentation_and_mesh(
        p, selected, image="fs:tag", fastsurfer_runner=spy_runner,
        jobs_root=jobs_root, host_jobs_root=host_jobs_root,
    )

    assert status.state == "done"
    cmd = captured["cmd"]
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
    expected_t1_host = host_jobs_root / "job1" / "nifti"
    expected_sd_host = host_jobs_root / "job1" / "fs"
    assert any(m == f"{expected_t1_host}:/data:ro" for m in mounts)
    assert any(m == f"{expected_sd_host}:/output" for m in mounts)
    # 로컬 파일은 여전히 api 자기 경로(jobs_root) 밑에서 산출된다
    assert (p.seg_dir / "seg.nii.gz").is_file()
    assert (p.seg_dir / "orig.nii.gz").is_file()


def test_segment_failure_records_phi_safe_error(tmp_path):
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = _t1_upload(tmp_path)
    ingest_job(p, src, "input.nii.gz")
    selected = read_status(p).series[0]["niftiPath"]

    def failing(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout="",
            stderr=f"error at {selected}",  # 경로가 stderr에 있다
        )

    status = run_segmentation_and_mesh(
        p, Path(selected), image="fs:tag", fastsurfer_runner=failing,
    )
    assert status.state == "error"
    assert status.error["step"] == "segment"
    # 느슨한 OR가 아니라, 실제로 넣은 원본 경로 문자열이 그대로 안 남았는지
    # 직접 확인한다 — sanitize_stderr를 우회해도 "<file>"이 다른 곳에서
    # 우연히 나타나면 통과하는 약한 단언을 피한다.
    assert selected not in json.dumps(status.error)


def test_ingest_failure_records_phi_safe_error(tmp_path):
    """io 단계 실패(판별 불가)도 record_error를 거쳐 PHI-안전하게 남는다."""
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = tmp_path / "upload" / "Fake_Patient_Name_scan.dat"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"not a real medical image - just garbage bytes for the test")

    status = ingest_job(p, src, "scan.dat")

    assert status.state == "error"
    assert status.error["step"] == "io"
    assert str(src) not in json.dumps(status.error)


def test_remap_failure_records_phi_safe_error(tmp_path, monkeypatch):
    """remap 단계 실패(RemapError)도 record_error를 거쳐 PHI-안전하게 남는다."""
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = _t1_upload(tmp_path)
    ingest_job(p, src, "input.nii.gz")
    selected = read_status(p).series[0]["niftiPath"]

    fake_path = str(tmp_path / "up" / "Fake_Patient_Name_seg.mgz")

    def failing_remap(seg_in, seg_out, table=None):
        raise RemapError(f"세그멘테이션을 읽지 못했다: {fake_path}")

    monkeypatch.setattr(pipeline_mod, "remap_segmentation", failing_remap)

    status = run_segmentation_and_mesh(
        p, Path(selected), image="fs:tag",
        fastsurfer_runner=_fastsurfer_mock(p.fs_dir, "case"),
    )

    assert status.state == "error"
    assert status.error["step"] == "remap"
    assert fake_path not in json.dumps(status.error)


def test_mesh_failure_records_phi_safe_error(tmp_path, monkeypatch):
    """mesh 단계 실패(GenerateError)도 record_error를 거쳐 PHI-안전하게 남는다."""
    p = job_paths(tmp_path / "jobs", "job1").create()
    src = _t1_upload(tmp_path)
    ingest_job(p, src, "input.nii.gz")
    selected = read_status(p).series[0]["niftiPath"]

    fake_path = str(tmp_path / "seg" / "Fake_Patient_Name_seg.nii.gz")

    def failing_generate(seg_path, out_dir, params, index=1, table=None):
        raise GenerateError(f"세그를 읽지 못했다: {fake_path}")

    monkeypatch.setattr(pipeline_mod, "generate_variant", failing_generate)

    status = run_segmentation_and_mesh(
        p, Path(selected), image="fs:tag",
        fastsurfer_runner=_fastsurfer_mock(p.fs_dir, "case"),
    )

    assert status.state == "error"
    assert status.error["step"] == "mesh"
    assert fake_path not in json.dumps(status.error)


def _done_paths(tmp_path, job_id="jobV"):
    """잡을 done까지 몰고 가 JobPaths를 돌려준다(test_full_run 패턴 재사용)."""
    p = job_paths(tmp_path / "jobs", job_id).create()
    ingest_job(p, _t1_upload(tmp_path), "input.nii.gz")
    selected = read_status(p).series[0]["niftiPath"]
    run_segmentation_and_mesh(
        p, Path(selected), image="fs:tag",
        fastsurfer_runner=_fastsurfer_mock(p.fs_dir, "case"),
    )
    return p


def test_initial_variant_uses_baseline_params(tmp_path):
    p = _done_paths(tmp_path)
    status = json.loads(p.status_file.read_text(encoding="utf-8"))
    vid = status["variants"][0]["variantId"]
    params = json.loads((p.variant_dir(vid) / "params.json").read_text(encoding="utf-8"))
    assert params["smoothing"]["method"] == "laplacian"
    assert params["extractor"]["name"] == "vtk_contour_perlabel"
    # variantId 해시가 baseline 해시와 일치
    assert vid.endswith(baseline_params().param_hash())


def test_add_variant_appends_new(tmp_path):
    p = _done_paths(tmp_path)
    before = len(json.loads(p.status_file.read_text("utf-8"))["variants"])
    params = parse_mesh_params({"smoothing": {"method": "none"}})
    res = add_variant(p, params)
    assert res["deduped"] is False
    status = json.loads(p.status_file.read_text("utf-8"))
    assert len(status["variants"]) == before + 1
    assert res["variantId"] in [v["variantId"] for v in status["variants"]]
    assert (p.variant_dir(res["variantId"]) / "regions.glb").is_file()
    assert (p.variant_dir(res["variantId"]) / "regions-meta.json").is_file()


def test_add_variant_dedupes_by_param_hash(tmp_path):
    p = _done_paths(tmp_path)
    # baseline은 초기 v01과 같은 파라미터 → 중복
    res = add_variant(p, baseline_params())
    assert res["deduped"] is True
    status = json.loads(p.status_file.read_text("utf-8"))
    assert len(status["variants"]) == 1  # 늘지 않음


def test_add_variant_rejects_non_done(tmp_path):
    # ingest만 해 awaiting_series에 세운다(done 아님)
    p = job_paths(tmp_path / "jobs", "jobA").create()
    ingest_job(p, _t1_upload(tmp_path), "input.nii.gz")
    import pytest
    with pytest.raises(ValueError):
        add_variant(p, baseline_params())
