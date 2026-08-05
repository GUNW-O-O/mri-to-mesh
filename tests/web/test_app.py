"""FastAPI 라우트 (스펙 §4, §6.3)."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient

from mri2mesh.segment import SEG_SOURCE_FILE
from mri2mesh.web.app import AppConfig, create_app


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
    r = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())})
    assert r.status_code == 200
    jid = r.json()["jobId"]

    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] == "awaiting_series"
    assert len(s["series"]) == 1


def test_upload_accepts_folder_of_many_files_flattened(tmp_path):
    client, _ = _client(tmp_path)
    # DICOM 폴더 흉내: 매직바이트가 DICOM인 파일 3개(파일명·상대경로 제각각)
    dicom = b"\x00" * 128 + b"DICM" + b"\x00" * 64
    files = [
        ("files", ("A/very/deep/nas/path/IM0001", dicom)),
        ("files", ("A/very/deep/nas/path/IM0002", dicom)),
        ("files", ("B/IM0001", dicom)),  # 다른 폴더의 동명 파일
    ]
    r = client.post("/api/jobs", files=files, data={"name": "환자용 라벨"})
    jid = r.json()["jobId"]
    # input_dir에 번호로 평탄 저장됐는지(원본 이름·경로 안 씀)
    input_dir = tmp_path / "jobs" / jid / "input"
    names = sorted(p.name for p in input_dir.iterdir())
    assert names == ["0001", "0002", "0003"]
    # 이름은 case_name으로 살아남아 목록에 뜬다
    row = next(r for r in client.get("/api/jobs").json() if r["jobId"] == jid)
    assert row["name"] == "환자용 라벨"


def test_upload_name_defaults_to_job_id(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    row = next(r for r in client.get("/api/jobs").json() if r["jobId"] == jid)
    assert row["name"] == jid


def test_upload_name_control_chars_stripped_and_capped(tmp_path):
    client, _ = _client(tmp_path)
    raw = "a\x00b\nc" + "x" * 300
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())},
                      data={"name": raw}).json()["jobId"]
    row = next(r for r in client.get("/api/jobs").json() if r["jobId"] == jid)
    assert "\x00" not in row["name"] and "\n" not in row["name"]
    assert len(row["name"]) <= 120


def test_series_selection_runs_pipeline_to_done(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    r = client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})
    assert r.status_code == 200

    # 백그라운드 완료 대기 (테스트는 동기 실행하거나 폴링)
    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s.get("error")
    assert len(s["variants"]) == 1


def _run_to_done(client, tmp_path, jid, holder, params=None):
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    body = {"seriesIndex": 0}
    if params is not None:
        body["params"] = params
    r = client.post(f"/api/jobs/{jid}/series", json=body)
    assert r.status_code == 200, r.text
    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    return s


def test_series_selection_custom_params_used(tmp_path):
    """시리즈와 함께 보낸 메쉬 파라미터가 첫 변형에 반영된다 — baseline과 다른
    옵션은 파라미터 해시가 달라 variantId가 달라진다."""
    client, holder = _client(tmp_path)
    base_jid = client.post("/api/jobs", files={"files": ("a.nii.gz", _nifti_bytes())}).json()["jobId"]
    base = _run_to_done(client, tmp_path, base_jid, holder)
    assert base["state"] == "done", base.get("error")

    cust_jid = client.post("/api/jobs", files={"files": ("b.nii.gz", _nifti_bytes())}).json()["jobId"]
    # minVoxel을 baseline(100)에서 바꾼다 → 다른 해시
    cust = _run_to_done(client, tmp_path, cust_jid, holder, params={
        "preprocess": {"method": "none"},
        "extractor": {"name": "vtk_contour_perlabel"},
        "smoothing": {"method": "laplacian", "iterations": 30},
        "decimation": {"method": "none", "targetRatio": 0.35},
        "minVoxel": 250,
    })
    assert cust["state"] == "done", cust.get("error")
    assert cust["variants"][0]["variantId"] != base["variants"][0]["variantId"]
    # 고른 옵션이 변형에 기록돼 UI가 "무슨 옵션으로 뽑았나"를 표시할 수 있다
    assert cust["variants"][0]["params"]["minVoxel"] == 250
    assert base["variants"][0]["params"]["minVoxel"] == 100


def test_variant_params_json_served(tmp_path):
    """변형이 무슨 옵션으로 산출됐는지 params.json으로 확인 가능 — UI 범례의 원천.
    옛 잡(status에 params 없음)도 이 파일로 요약을 복구한다."""
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("a.nii.gz", _nifti_bytes())}).json()["jobId"]
    s = _run_to_done(client, tmp_path, jid, holder)
    assert s["state"] == "done", s.get("error")
    vid = s["variants"][0]["variantId"]

    r = client.get(f"/api/jobs/{jid}/variants/{vid}/params.json")
    assert r.status_code == 200
    p = r.json()
    assert p["minVoxel"] == 100
    assert p["extractor"]["name"]  # 옵션이 사람이 읽을 형태로 존재
    # 없는 변형은 404
    assert client.get(f"/api/jobs/{jid}/variants/v99-zzzz/params.json").status_code == 404


def test_delete_variant_removes_it(tmp_path):
    """변형(메쉬 산출) 하나만 삭제 — status에서 빠지고 폴더도 지워진다.
    없는 변형은 404."""
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("a.nii.gz", _nifti_bytes())}).json()["jobId"]
    s = _run_to_done(client, tmp_path, jid, holder)
    assert s["state"] == "done", s.get("error")
    # 두 번째 변형 추가
    r = client.post(f"/api/jobs/{jid}/variants", json={"minVoxel": 300})
    assert r.status_code == 200, r.text
    vid2 = r.json()["variantId"]
    vdir2 = tmp_path / "jobs" / jid / "mesh" / vid2
    assert vdir2.is_dir()

    d = client.delete(f"/api/jobs/{jid}/variants/{vid2}")
    assert d.status_code == 200
    left = [v["variantId"] for v in client.get(f"/api/jobs/{jid}").json()["variants"]]
    assert vid2 not in left and len(left) == 1
    assert not vdir2.exists()
    # 이미 지운 것 재삭제 → 404
    assert client.delete(f"/api/jobs/{jid}/variants/{vid2}").status_code == 404


def test_series_selection_bad_params_returns_400(tmp_path):
    """잘못된 메쉬 파라미터는 백그라운드로 넘어가기 전에 400."""
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("c.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    r = client.post(f"/api/jobs/{jid}/series",
                    json={"seriesIndex": 0, "params": {"preprocess": "not-a-dict"}})
    assert r.status_code == 400
    # 여전히 시리즈 선택 대기 — 잘못된 요청이 잡을 running으로 바꾸지 않는다
    assert client.get(f"/api/jobs/{jid}").json()["state"] == "awaiting_series"


def test_served_status_has_no_niftipath(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] == "awaiting_series"
    assert "niftiPath" not in json.dumps(s)      # 어디에도 경로 없음
    assert s["series"][0]["index"] == 0          # index로 식별
    assert "description" in s["series"][0]


def test_selected_series_served_without_path(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})
    s = client.get(f"/api/jobs/{jid}").json()
    sel = s["selectedSeries"]
    assert sel is not None
    assert set(sel) <= {"index", "description", "slices", "voxelSizeMm"}
    assert "niftiPath" not in json.dumps(sel)


def test_unexpected_pipeline_error_does_not_strand_job(tmp_path):
    """백그라운드 파이프라인이 예상 못 한 예외로 죽어도 잡이 running에 박히면
    안 된다 — catch-all이 error로 기록하고, PHI(경로)는 마스킹돼야 한다."""
    leak = str(tmp_path / "jobs" / "Jane_Q_Placeholder.nii.gz")

    def boom(cmd, **kwargs):
        # SegmentError가 아닌 예상 밖 예외 — 단계별 except가 안 잡는다.
        raise RuntimeError(f"unexpected {leak}")

    cfg = AppConfig(
        jobs_root=tmp_path / "jobs", fastsurfer_image="fs:tag",
        threads=4, fastsurfer_runner=boom,
    )
    client = TestClient(create_app(cfg))
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})

    for _ in range(50):
        s = client.get(f"/api/jobs/{jid}").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "error"
    assert "Jane_Q_Placeholder" not in json.dumps(s["error"])


def test_seg_concurrency_limit_defaults_to_two(tmp_path):
    """동시 세그 상한 기본값 2 — GPU 경합 방지(전역 세마포어)."""
    cfg = AppConfig(jobs_root=tmp_path / "jobs", fastsurfer_image="fs:tag")
    assert cfg.max_concurrent_seg == 2
    # create_app이 상한으로 세마포어를 만들며 정상 기동한다
    assert create_app(cfg) is not None


def test_index_serves_viewer(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_static_assets_serve(tmp_path):
    """뷰어 페이지가 자기 스크립트와 vendor한 three.js를 못 받으면 통째로
    안 뜬다 — /static 마운트가 살아 있는지 지킨다."""
    client, _ = _client(tmp_path)
    for path in ("/static/viewer.js", "/static/vendor/three.module.js"):
        assert client.get(path).status_code == 200


def test_index_html_has_option_form_anchors(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get("/").text
    assert 'id="mesh-options"' in html
    assert 'id="gen-variant"' in html


def test_index_html_has_dicom_info_anchors(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get("/").text
    assert 'id="dicom-info"' in html
    assert 'class="job-info"' in html or 'job-info' in html


def test_static_mount_blocks_traversal(tmp_path):
    """StaticFiles가 static/ 밖으로 나가지 못해야 한다(임의 파일 읽기 방지)."""
    client, _ = _client(tmp_path)
    assert client.get("/static/../app.py").status_code == 404


def test_list_jobs_returns_summaries_without_phi(tmp_path):
    client, _ = _client(tmp_path)
    # 잡 두 개 업로드(둘 다 awaiting_series에서 멈춤)
    for _ in range(2):
        client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())})

    rows = client.get("/api/jobs").json()
    assert len(rows) == 2
    keys = set(rows[0])
    assert keys == {"jobId", "name", "state", "step", "createdAt", "variantCount"}
    # PHI 부재: 목록 응답 어디에도 파일명·경로·series 필드가 없다.
    blob = json.dumps(rows)
    assert "niftiPath" not in blob
    assert "scan.nii.gz" not in blob
    # Check that 'series' is not present as a field key in any row
    assert not any("series" in row for row in rows)


def test_list_jobs_empty(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/jobs").json() == []


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

    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

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
    r = client.post("/api/jobs/no-such-job/series", json={"seriesIndex": 0})
    assert r.status_code == 404


def test_series_selection_by_index_rejects_out_of_range(tmp_path):
    """범위 밖 index를 보내면 거부한다.

    seriesIndex는 서버가 자기 status.series 배열 길이 안에서만 받아들인다
    — 그 이상은 서버가 자기 신뢰 데이터를 벗어난 index로 임의 접근하는
    셈이라 400으로 막는다(경로를 클라가 안 보내므로 경로 조작 표면 자체가
    없어졌다는 점이 브리프의 핵심 변경이다).
    """
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

    r = client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 99})
    assert r.status_code == 400
    # 거부됐으니 세그멘테이션 산출물도 없어야 한다
    assert not (tmp_path / "jobs" / jid / "seg" / "seg.nii.gz").exists()


def test_series_selection_rejects_repeat_after_started(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"

    assert client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0}).status_code == 200
    assert client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0}).status_code == 409


def test_glb_and_meta_are_served_after_done(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"

    client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})

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
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]

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

    r = client.post("/api/jobs/a..b/series", json={"seriesIndex": 0})
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

        r = client.post(f"/api/jobs/{bad}/series", json={"seriesIndex": 0})
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
        files={"files": ("../../evil.nii.gz", _nifti_bytes())},
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
        files={"files": (fake_patient_name, b"not a real medical image, just garbage bytes")},
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
    """평탄화 회귀 점검: 클라이언트 파일명은 이제 통째로 버려지고 번호로만
    저장되므로(입력 파일명은 서버 어디에도 안 쓰인다) ".."을 파일명으로
    보내도 그 값 자체는 무해하다. 그래도 업로드는 항상 200 +
    기록된 상태(정상 진행 또는 error)여야 하고, 절대 맨 500으로 터지면
    안 된다는 걸 지킨다.
    """
    client, _ = _client(tmp_path)
    r = client.post("/api/jobs", files={"files": ("..", b"whatever bytes")})
    assert r.status_code == 200
    jid = r.json()["jobId"]

    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] in ("awaiting_series", "error")


# --- 변형(variant) 생성 테스트 (Task 5) ---


def _job_to_done(client, holder, tmp_path):
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})
    return jid


def test_post_variant_creates_new(tmp_path):
    client, holder = _client(tmp_path)
    jid = _job_to_done(client, holder, tmp_path)
    r = client.post(f"/api/jobs/{jid}/variants",
                    json={"smoothing": {"method": "none"}})
    assert r.status_code == 200
    body = r.json()
    assert body["deduped"] is False
    s = client.get(f"/api/jobs/{jid}").json()
    assert body["variantId"] in [v["variantId"] for v in s["variants"]]


def test_post_variant_dedupes_baseline(tmp_path):
    client, holder = _client(tmp_path)
    jid = _job_to_done(client, holder, tmp_path)
    r = client.post(f"/api/jobs/{jid}/variants", json={})  # 빈 = baseline
    assert r.json()["deduped"] is True


def test_post_variant_rejects_bad_params(tmp_path):
    client, holder = _client(tmp_path)
    jid = _job_to_done(client, holder, tmp_path)
    r = client.post(f"/api/jobs/{jid}/variants",
                    json={"decimation": {"method": "quadric", "targetRatio": 9.0}})
    assert r.status_code == 400


def test_post_variant_rejects_non_dict_axis(tmp_path):
    """리뷰 발견 #1: 축이 dict가 아니면(.get이 AttributeError로 터지는 대신)
    400이어야 한다(500이 아니라)."""
    client, holder = _client(tmp_path)
    jid = _job_to_done(client, holder, tmp_path)
    r = client.post(f"/api/jobs/{jid}/variants", json={"preprocess": "x"})
    assert r.status_code == 400


def test_post_variant_generate_error_maps_to_422(tmp_path):
    """리뷰 발견 #2: 파라미터가 유효해도 minVoxel이 모든 라벨을 걸러내
    만들 메시가 없으면(GenerateError) 500이 아니라 422여야 한다.

    목 세그의 라벨 17은 복셀 1000개(10*10*10)뿐이라 minVoxel을 그보다
    크게 주면 유일한 라벨이 걸러져 GenerateError("만들 메시가 없다")가 난다.
    """
    client, holder = _client(tmp_path)
    jid = _job_to_done(client, holder, tmp_path)
    r = client.post(f"/api/jobs/{jid}/variants", json={"minVoxel": 1001})
    assert r.status_code == 422
    # PHI: seg 경로 등 예외 원문이 본문에 안 새어 나가야 한다
    assert "seg" not in r.text.lower()
    assert ".nii" not in r.text


def test_post_variant_rejects_non_done(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    r = client.post(f"/api/jobs/{jid}/variants", json={})
    assert r.status_code == 409


def test_delete_job_removes_it(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    assert (tmp_path / "jobs" / jid).is_dir()

    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == jid
    assert not (tmp_path / "jobs" / jid).exists()
    # 목록·상태에서 사라진다
    assert jid not in [row["jobId"] for row in client.get("/api/jobs").json()]
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_delete_missing_job_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.delete("/api/jobs/nope-1234").status_code == 404


def test_dicom_meta_served_for_nifti_upload(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs",
                      files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    r = client.get(f"/api/jobs/{jid}/dicom-meta")
    assert r.status_code == 200
    m = r.json()
    assert m["source"] == "nifti"
    assert m["originalFilenames"] == ["scan.nii.gz"]


def test_dicom_meta_404_when_absent(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/jobs/nope-1234/dicom-meta").status_code == 404
