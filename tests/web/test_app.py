"""FastAPI 라우트 (스펙 §4, §6.3)."""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient

from seg_and_mesh.segment import SEG_SOURCE_FILE
from seg_and_mesh.web.app import AppConfig, create_app


def _nifti_bytes():
    import tempfile
    vol = np.zeros((16, 16, 16), np.int16)
    img = nib.Nifti1Image(vol, np.eye(4))
    img.header.set_zooms((1.0, 1.0, 1.0))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "input.nii.gz"
        nib.save(img, p)
        return p.read_bytes()


def _fastsurfer_mock(root_getter):
    def run(cmd, **kwargs):
        # --sd 다음 인자가 subject_dir_root, --sid 다음이 sid
        sd = Path(cmd[cmd.index("--sd") + 1])
        # 컨테이너 내부 경로가 아니라 호스트 경로를 목이 알아야 하므로
        # root_getter로 실제 fs_dir를 받는다
        mri = root_getter() / "case" / "mri"
        mri.mkdir(parents=True, exist_ok=True)
        nib.save(nib.MGHImage(np.full((16, 16, 16), 100, np.uint8), np.eye(4)), mri / "orig.mgz")
        seg = np.zeros((16, 16, 16), np.int16)
        seg[2:12, 2:12, 2:12] = 17
        nib.save(nib.MGHImage(seg, np.eye(4)), mri / SEG_SOURCE_FILE)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    return run


def _client(tmp_path):
    holder = {}
    cfg = AppConfig(
        jobs_root=tmp_path / "jobs",
        fastsurfer_image="fs:tag",
        threads=4,
        fastsurfer_runner=_fastsurfer_mock(lambda: holder["fs_dir"]),
    )
    app = create_app(cfg)
    return TestClient(app), holder


def test_upload_creates_job_awaiting_series(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())})
    assert r.status_code == 200
    jid = r.json()["jobId"]

    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] == "awaiting_series"
    assert len(s["series"]) == 1


def test_series_selection_runs_pipeline_to_done(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"

    nifti_path = client.get(f"/api/jobs/{jid}").json()["series"][0]["niftiPath"]
    r = client.post(f"/api/jobs/{jid}/series", json={"niftiPath": nifti_path})
    assert r.status_code == 200

    # 백그라운드 완료 대기 (테스트는 동기 실행하거나 폴링)
    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s.get("error")
    assert len(s["variants"]) == 1


def test_index_serves_viewer(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# --- 아래는 브리프의 3개 테스트를 넘어서는 추가 커버리지다 ---
# (§6.3 게이트가 HTTP 경계에서도 지켜지는지, 경로 검증, PHI 안전성, 404들)


def test_upload_alone_never_produces_segmentation(tmp_path):
    """업로드만으로는 세그멘테이션이 시작되면 안 된다(§6.3 게이트).

    fastsurfer_runner를 절대 호출하지 않는 목으로 교체해, 만약 업로드가
    실수로 segment까지 이어 돌면 이 테스트가 즉시 실패하게 한다.
    """

    def _boom(cmd, **kwargs):
        raise AssertionError("업로드만으로 FastSurfer가 호출되면 안 된다")

    cfg = AppConfig(
        jobs_root=tmp_path / "jobs", fastsurfer_image="fs:tag", threads=4,
        fastsurfer_runner=_boom,
    )
    client = TestClient(create_app(cfg))

    jid = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] == "awaiting_series"
    assert not (tmp_path / "jobs" / jid / "seg" / "seg.nii.gz").exists()
    assert not (tmp_path / "jobs" / jid / "fs").exists() or not any(
        (tmp_path / "jobs" / jid / "fs").iterdir()
    )


def test_get_unknown_job_is_404(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/api/jobs/no-such-job")
    assert r.status_code == 404


def test_series_selection_unknown_job_is_404(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/jobs/no-such-job/series", json={"niftiPath": "whatever"})
    assert r.status_code == 404


def test_series_selection_rejects_path_not_in_candidates(tmp_path):
    """클라이언트가 이 잡의 랭킹 후보에 없는 임의 경로를 보내면 거부한다.

    niftiPath는 이 잡의 status.series에 있는 값이어야만 한다 — 그렇지
    않으면 서버가 클라이언트가 지정한 임의 파일을 그대로 열어 FastSurfer에
    넘기는 경로 조작(path traversal / arbitrary read) 위험이 된다.
    """
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

    other_job_dir = tmp_path / "jobs" / "other-job" / "nifti"
    other_job_dir.mkdir(parents=True)
    sneaky = other_job_dir / "secret.nii.gz"
    sneaky.write_bytes(b"not a real nifti")

    r = client.post(f"/api/jobs/{jid}/series", json={"niftiPath": str(sneaky)})
    assert r.status_code == 400
    # 거부됐으니 세그멘테이션 산출물도 없어야 한다
    assert not (tmp_path / "jobs" / jid / "seg" / "seg.nii.gz").exists()


def test_glb_and_meta_are_served_after_done(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"

    nifti_path = client.get(f"/api/jobs/{jid}").json()["series"][0]["niftiPath"]
    client.post(f"/api/jobs/{jid}/series", json={"niftiPath": nifti_path})

    s = None
    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s.get("error")
    variant_id = s["variants"][0]["variantId"]

    glb_r = client.get(f"/api/jobs/{jid}/variants/{variant_id}/regions.glb")
    assert glb_r.status_code == 200
    assert glb_r.headers["content-type"] == "model/gltf-binary"
    assert len(glb_r.content) > 0

    meta_r = client.get(f"/api/jobs/{jid}/variants/{variant_id}/regions-meta.json")
    assert meta_r.status_code == 200
    assert "application/json" in meta_r.headers["content-type"]


def test_glb_missing_variant_is_404(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"file": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

    r = client.get(f"/api/jobs/{jid}/variants/no-such-variant/regions.glb")
    assert r.status_code == 404

    r = client.get(f"/api/jobs/{jid}/variants/no-such-variant/regions-meta.json")
    assert r.status_code == 404


def test_dotted_job_id_is_not_falsely_rejected(tmp_path):
    """"a..b"는 ".."을 부분 문자열로 포함하지만 실제로는 jobs_root를 벗어나지
    않는, 그냥 점 두 개가 든 평범한 이름이다. 문자 블랙리스트라면 이것도
    걸리겠지만(과잉 차단), 결합 결과가 실제로 jobs_root 밖인지로 판단하는
    지금 방식은 이런 무해한 이름을 잘못 막지 않는다 — 해당 잡이 없어서
    나는 404여야지, 검증에 걸린 400이면 안 된다.
    """
    client, _ = _client(tmp_path)

    r = client.get("/api/jobs/a..b")
    assert r.status_code == 404

    r = client.post("/api/jobs/a..b/series", json={"niftiPath": "x"})
    assert r.status_code == 404

    r = client.get("/api/jobs/foo/variants/a..b/regions.glb")
    assert r.status_code == 404


def test_windows_drive_relative_job_id_is_rejected(tmp_path):
    """윈도우 드라이브-상대 경로("D:evil", "D:")는 "/", "\\", ".." 중 아무것도
    안 쓰고도 `Path(base) / value`에서 base를 통째로 버린다(PureWindowsPath
    결합 규칙) — 문자 블랙리스트로는 못 잡고, 결합 결과가 jobs_root 밑인지
    확인해야만 잡힌다.

    주의: jobs_root와 같은 드라이브 문자(예: jobs_root가 C:\\...일 때 "C:")는
    pathlib이 "같은 드라이브의 현재 경로"로 취급해 base를 버리지 않으므로
    (진짜 탈출이 아니다) 여기서는 다루지 않는다 — 실제 탈출은 jobs_root와
    다른 드라이브 문자를 썼을 때다.
    """
    client, _ = _client(tmp_path)

    for bad in ("D:evil", "D:", "D:secret.txt"):
        r = client.get(f"/api/jobs/{bad}")
        assert r.status_code == 400, f"{bad!r}: {r.status_code}"

        r = client.post(f"/api/jobs/{bad}/series", json={"niftiPath": "x"})
        assert r.status_code == 400, f"{bad!r}: {r.status_code}"

        r = client.get(f"/api/jobs/foo/variants/{bad}/regions.glb")
        assert r.status_code == 400, f"{bad!r}: {r.status_code}"

        r = client.get(f"/api/jobs/foo/variants/{bad}/regions-meta.json")
        assert r.status_code == 400, f"{bad!r}: {r.status_code}"


def test_upload_filename_traversal_is_contained(tmp_path):
    """멀티파트 파일명에 "../"가 섞여도 input_dir 밖에 쓰이면 안 된다.

    파일명은 클라이언트가 통째로 지정하는 값이라, 방어하지 않으면 서버가
    임의 경로에 쓰는 업로드 경로 조작(path traversal write) 벡터가 된다.
    """
    client, _ = _client(tmp_path)
    r = client.post(
        "/api/jobs",
        files={"file": ("../../evil.nii.gz", _nifti_bytes())},
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]

    # jobs_root 밖에는 아무것도 쓰이지 않았어야 한다
    assert not (tmp_path / "evil.nii.gz").exists()
    # input_dir 안에는 뭔가 저장됐다(마지막 구성요소로 정리된 이름으로)
    assert list((tmp_path / "jobs" / jid / "input").iterdir())


def test_upload_failure_is_phi_safe(tmp_path):
    """판별 실패 업로드가 환자 식별 파일명을 status.json이나 응답에 남기지 않는다.

    확장자도 구분자도 없는 맨 이름(필립스 DICOM처럼)을 일부러 쓴다 —
    sanitize_stderr는 이 모양을 의도적으로 건드리지 않으므로(스펙 §12 참고,
    status.py 문서), input.filename에 그런 이름이 그대로 남는지가 바로
    이 테스트가 잡아야 하는 구멍이다. 실제로 오늘 리포에 환자 이름이 샌
    사고가 있었으므로, 가짜 이름으로만 검증한다 — test-asset/의 실제
    이름을 절대 베끼지 않는다.
    """
    client, _ = _client(tmp_path)
    fake_patient_name = "Jane_Q_Placeholder"  # 확장자·구분자 없는 맨 이름
    r = client.post(
        "/api/jobs",
        files={"file": (fake_patient_name, b"not a real medical image, just garbage bytes")},
    )
    # 업로드 자체는 잡을 만들고(ingest_job이 내부적으로 io 실패를 잡아 상태에
    # 기록한다), 응답에도 200이 나온다 — 실패는 status.json의 error로 드러난다.
    assert r.status_code == 200
    assert fake_patient_name not in r.text

    jid = r.json()["jobId"]
    body = client.get(f"/api/jobs/{jid}")
    assert fake_patient_name not in body.text
    s = body.json()
    assert s["state"] == "error"
    assert fake_patient_name not in (s["error"] or {}).get("message", "")
    assert fake_patient_name not in (s["input"] or {}).get("filename", "")


def test_upload_dotdot_filename_does_not_500(tmp_path):
    """멀티파트 파일명이 정확히 ".."이면 Path("..").name도 ".."을 그대로
    돌려준다(빈 문자열이 아니다) — input_dir/".."은 잡 루트 자체(이미 있는
    디렉터리)라 안전장치 없이 write_bytes하면 500으로 터진다. 항상
    200 + 기록된 상태(정상 진행 또는 error)여야 한다.
    """
    client, _ = _client(tmp_path)
    r = client.post("/api/jobs", files={"file": ("..", b"whatever bytes")})
    assert r.status_code == 200
    jid = r.json()["jobId"]

    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] in ("awaiting_series", "error")
