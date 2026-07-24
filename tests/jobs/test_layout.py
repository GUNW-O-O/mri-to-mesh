"""잡 디렉터리 레이아웃 (스펙 §7, §9.1)."""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from seg_and_mesh.jobs.layout import JobPaths, job_paths, new_job_id, to_host_path


def test_job_id_format():
    jid = new_job_id(datetime(2026, 7, 22, 14, 30, 11))
    assert re.fullmatch(r"2026-07-22-143011-[0-9a-f]{4}", jid)


def test_job_ids_are_unique():
    now = datetime(2026, 7, 22, 14, 30, 11)
    assert new_job_id(now) != new_job_id(now)  # 랜덤 접미가 다르다


def test_paths_follow_spec_layout(tmp_path):
    p = job_paths(tmp_path, "2026-07-22-143011-a3f9")
    assert p.root == tmp_path / "2026-07-22-143011-a3f9"
    assert p.input_dir.name == "input"
    assert p.nifti_dir.name == "nifti"
    assert p.fs_dir.name == "fs"
    assert p.seg_dir.name == "seg"
    assert p.mesh_dir.name == "mesh"
    assert p.out_dir.name == "out"
    assert p.status_file.name == "status.json"
    assert p.variant_dir("v01-3c81") == p.mesh_dir / "v01-3c81"


def test_create_makes_all_dirs(tmp_path):
    p = job_paths(tmp_path, "job1").create()
    for d in (p.input_dir, p.nifti_dir, p.fs_dir, p.seg_dir, p.mesh_dir, p.out_dir):
        assert d.is_dir()


def test_to_host_path_identity_when_no_host_root(tmp_path):
    jobs_root = tmp_path / "jobs"
    path = jobs_root / "job1" / "seg" / "seg.nii.gz"
    assert to_host_path(path, jobs_root=jobs_root, host_jobs_root=None) == path


def test_to_host_path_swaps_prefix(tmp_path):
    jobs_root = tmp_path / "work" / "jobs"
    host_jobs_root = tmp_path / "host" / "output"
    path = jobs_root / "job1" / "seg" / "seg.nii.gz"
    assert to_host_path(
        path, jobs_root=jobs_root, host_jobs_root=host_jobs_root
    ) == host_jobs_root / "job1" / "seg" / "seg.nii.gz"


def test_to_host_path_rejects_path_outside_jobs_root(tmp_path):
    jobs_root = tmp_path / "work" / "jobs"
    host_jobs_root = tmp_path / "host" / "output"
    outside = tmp_path / "elsewhere" / "file.txt"
    with pytest.raises(ValueError):
        to_host_path(outside, jobs_root=jobs_root, host_jobs_root=host_jobs_root)


def test_to_host_path_same_as_jobs_root_returns_host_root(tmp_path):
    jobs_root = tmp_path / "work" / "jobs"
    host_jobs_root = tmp_path / "host" / "output"
    assert to_host_path(
        jobs_root, jobs_root=jobs_root, host_jobs_root=host_jobs_root
    ) == host_jobs_root
