"""FastAPI 워크벤치 (스펙 §4, §6.3).

단일 사용자·로컬. 업로드 → 시리즈 게이트 → 백그라운드 파이프라인 → 뷰어.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from seg_and_mesh.jobs.layout import job_paths, new_job_id
from seg_and_mesh.jobs.pipeline import ingest_job, run_segmentation_and_mesh
from seg_and_mesh.jobs.status import read_status, record_error, sanitize_stderr

_STATIC = Path(__file__).resolve().parent / "static"


def _status_json(paths) -> dict:
    """status.json을 HTTP로 내보낼 dict로 바꾼다.

    디스크의 status.json은 스펙 §9.1대로 input.filename에 원본 파일명을
    그대로 담아 둔다(재현·디버깅용). 하지만 그 파일명은 클라이언트가 업로드
    시 보낸, 환자 식별자일 수 있는 값이라(스펙 §12) 브라우저로 나가는
    경계에서는 sanitize_stderr로 한 번 더 마스킹한다 — 과다 마스킹이
    과소 마스킹보다 항상 안전하다는 원칙을 파일명 하나에도 그대로 적용한다.
    """
    d = read_status(paths).to_json_dict()
    if d.get("input") and d["input"].get("filename"):
        d["input"] = {**d["input"], "filename": sanitize_stderr(d["input"]["filename"])}
    return d


@dataclass
class AppConfig:
    jobs_root: Path
    fastsurfer_image: str
    threads: int = 8
    #: 테스트 주입용 FastSurfer 실행기. None이면 run_fastsurfer가 기본
    #: subprocess.run(실제 docker)을 쓴다.
    fastsurfer_runner: Callable | None = None
    #: 컨테이너 안에서 돌 때, jobs_root가 호스트에서 어디에 있는지.
    #: 형제 FastSurfer 컨테이너의 -v 인자에 쓴다. 호스트 직접 실행이면 None.
    host_jobs_root: Path | None = None


class SeriesSelection(BaseModel):
    niftiPath: str


def _reject_traversal(value: str, label: str) -> None:
    """URL 경로 조각(job_id·variant_id)이 파일시스템 결합에도 그대로 쓰이므로,
    "/", "\\", ".."이 섞이면 jobs_root 밖을 가리키는 경로 조작이 된다.
    서버가 만든 id는 원래 이런 문자를 안 쓰지만, 라우트 자체는 클라이언트가
    임의 문자열을 보내는 것도 막지 않으므로 여기서 형태로 거부한다.
    """
    if not value or "/" in value or "\\" in value or ".." in value:
        raise HTTPException(400, f"잘못된 {label}")


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="seg-and-mesh")
    config.jobs_root.mkdir(parents=True, exist_ok=True)

    @app.post("/api/jobs")
    async def upload(file: UploadFile) -> dict:
        job_id = new_job_id()
        paths = job_paths(config.jobs_root, job_id).create()
        # 파일명은 클라이언트가 보낸 값이라 공격자가 조작할 수 있고 환자
        # 식별자를 담고 있을 수 있다(스펙 §12) — 디스크 저장용으로만 쓰고,
        # 실패해도 이 파일명이 sanitize_stderr 없이 그대로 새 나가면 안 된다.
        # 경로 구분자가 섞인 파일명("../../evil")을 그대로 이어붙이면
        # input_dir 밖에 쓰는 경로 조작이 되므로, 마지막 구성요소만 남긴다.
        filename = file.filename or "upload"
        safe_name = Path(filename).name or "upload"
        dst = paths.input_dir / safe_name
        dst.write_bytes(await file.read())
        try:
            # ingest_job은 알려진 io 실패(UnsupportedInputError 등)를 내부에서
            # 이미 record_error로 잡아 status.json에 남기고 예외를 던지지
            # 않는다. 여기서 잡는 건 그 외의 예기치 못한 예외뿐이다 — 그때도
            # ingest_job은 자신의 try 블록 전에 초기 status.json을 이미 써
            # 뒀으므로, record_error가 읽을 status.json은 항상 있다.
            ingest_job(paths, dst, safe_name)
        except Exception as exc:
            record_error(paths, "io", None, str(exc))
        return {"jobId": job_id}

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str) -> dict:
        _reject_traversal(job_id, "job_id")
        paths = job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        return _status_json(paths)

    @app.post("/api/jobs/{job_id}/series")
    def select_series(job_id: str, sel: SeriesSelection, bg: BackgroundTasks) -> dict:
        _reject_traversal(job_id, "job_id")
        paths = job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")

        # 클라이언트가 보낸 niftiPath를 그대로 열면 경로 조작(path traversal /
        # arbitrary read) 위험이다 — 이 잡이 ingest_job에서 랭킹해 status.json에
        # 이미 적어둔 후보 중 하나인지 반드시 확인한다.
        job_status = read_status(paths)
        candidates = {c["niftiPath"] for c in job_status.series}
        if sel.niftiPath not in candidates:
            raise HTTPException(400, "알 수 없는 시리즈 선택")

        def work():
            run_segmentation_and_mesh(
                paths, Path(sel.niftiPath), config.fastsurfer_image,
                threads=config.threads, fastsurfer_runner=config.fastsurfer_runner,
                jobs_root=config.jobs_root, host_jobs_root=config.host_jobs_root,
            )

        bg.add_task(work)
        return {"state": "running"}

    @app.get("/api/jobs/{job_id}/variants/{variant_id}/regions.glb")
    def glb(job_id: str, variant_id: str) -> FileResponse:
        _reject_traversal(job_id, "job_id")
        _reject_traversal(variant_id, "variant_id")
        p = job_paths(config.jobs_root, job_id).variant_dir(variant_id) / "regions.glb"
        if not p.is_file():
            raise HTTPException(404, "glb 없음")
        return FileResponse(p, media_type="model/gltf-binary")

    @app.get("/api/jobs/{job_id}/variants/{variant_id}/regions-meta.json")
    def meta(job_id: str, variant_id: str) -> FileResponse:
        _reject_traversal(job_id, "job_id")
        _reject_traversal(variant_id, "variant_id")
        p = job_paths(config.jobs_root, job_id).variant_dir(variant_id) / "regions-meta.json"
        if not p.is_file():
            raise HTTPException(404, "meta 없음")
        return FileResponse(p, media_type="application/json")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    return app
