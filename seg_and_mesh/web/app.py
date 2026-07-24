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

from seg_and_mesh.jobs.layout import JobPaths, job_paths, new_job_id
from seg_and_mesh.jobs.pipeline import ingest_job, run_segmentation_and_mesh
from seg_and_mesh.jobs.status import (
    JobStatus,
    now_iso,
    read_status,
    record_error,
    write_status,
)

_STATIC = Path(__file__).resolve().parent / "static"


def _status_json(paths) -> dict:
    """status.json을 HTTP로 내보낼 dict로 바꾼다.

    디스크의 status.json은 스펙 §9.1대로 input.filename에 원본 파일명을
    그대로 담아 둔다(재현·디버깅용). 하지만 그 파일명은 클라이언트가 업로드
    시 보낸, 환자 식별자일 수 있는 값이라(스펙 §12) 브라우저로는 절대 나가면
    안 된다.

    sanitize_stderr는 여기 쓰기에 맞지 않는 도구다 — 그건 진단 문구를
    최대한 살리면서 경로 "모양"만 마스킹하는 자유 텍스트용이라, 확장자도
    구분자도 없는 맨 식별자(예: 필립스 DICOM 흔적인 "IM0001", 사람 이름)는
    의도적으로 건드리지 않는다(status.py의 sanitize_stderr 문서 참고).
    input.filename은 애초에 자유 텍스트가 아니라 파일명 그 자체이고,
    브라우저에는 진단 가치가 없으므로 모양과 상관없이 통째로 지운다.
    """
    d = read_status(paths).to_json_dict()
    if d.get("input") and d["input"].get("filename"):
        d["input"] = {**d["input"], "filename": "<file>"}
    return d


def _confined_child(base: Path, name: str, label: str) -> Path:
    """base 아래의 자식 경로 하나만 허용한다.

    "/", "\\", ".."을 문자로 금지하는 방식은 막다른 길이다 — 예를 들어
    윈도우 드라이브-상대 경로("D:evil")는 이 세 문자를 하나도 안 쓰고도
    `Path(base) / "D:evil"`에서 base를 통째로 버리고 `WindowsPath("D:evil")`
    이 돼 버린다(PureWindowsPath 결합 규칙). 문자 블랙리스트 대신, 실제로
    결합해 resolve()한 뒤 base 밑에 있는지 "결과"로 검사한다 — 그러면 어떤
    트릭으로 결합됐든 base를 벗어난 결과는 전부 잡힌다.
    """
    base_resolved = base.resolve()
    candidate = (base / name).resolve()
    if not (candidate == base_resolved or candidate.is_relative_to(base_resolved)):
        raise HTTPException(400, f"잘못된 {label}")
    return candidate


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


def _checked_job_paths(jobs_root: Path, job_id: str) -> JobPaths:
    """job_id를 jobs_root에 묶어 확인한 뒤 JobPaths를 돌려준다(경로 조작 방지)."""
    root = _confined_child(jobs_root, job_id, "job_id")
    return JobPaths(root=root)


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="seg-and-mesh")
    config.jobs_root.mkdir(parents=True, exist_ok=True)

    @app.post("/api/jobs")
    async def upload(file: UploadFile) -> dict:
        job_id = new_job_id()
        paths = job_paths(config.jobs_root, job_id).create()
        # 파일명은 클라이언트가 보낸 값이라 공격자가 조작할 수 있고 환자
        # 식별자를 담고 있을 수 있다(스펙 §12) — 디스크 저장용으로만 쓰고,
        # 실패해도 이 파일명이 그대로 새 나가면 안 된다.
        # 경로 구분자가 섞인 파일명("../../evil")을 그대로 이어붙이면
        # input_dir 밖에 쓰는 경로 조작이 되므로, 마지막 구성요소만 남긴다.
        # 다만 Path(".").name과 Path("..").name은 각각 "."·".."을 그대로
        # 돌려주므로(빈 문자열이 아니다!) 그 둘은 따로 "upload"로 바꾼다 —
        # 안 그러면 input_dir/".." 는 잡 루트 자체(이미 있는 디렉터리)라
        # write_bytes가 IsADirectoryError로 터진다.
        filename = file.filename or "upload"
        safe_name = Path(filename).name or "upload"
        if safe_name in (".", ".."):
            safe_name = "upload"
        dst = paths.input_dir / safe_name

        # record_error는 항상 되돌아갈 status.json이 있어야 한다. ingest_job은
        # 자기 try 블록 전에 초기 status.json을 쓰지만, write_bytes는 그보다도
        # 먼저 실패할 수 있으므로(디스크 쓰기 실패 등) 여기서 최소 상태를 먼저
        # 써 둔다 — ingest_job이 시작되면 이 값을 자신의 초기 status로 덮는다.
        write_status(paths, JobStatus(
            job_id=job_id, case_name=job_id, created_at=now_iso(), updated_at=now_iso(),
            state="running", step="io", input={"filename": safe_name, "bytes": 0},
        ))
        try:
            # ingest_job은 알려진 io 실패(UnsupportedInputError 등)를 내부에서
            # 이미 record_error로 잡아 status.json에 남기고 예외를 던지지
            # 않는다. 여기서 잡는 건 write_bytes 실패나 그 외 예기치 못한
            # 예외뿐이다 — 어느 쪽이든 위에서 미리 status.json을 써 뒀으므로
            # record_error가 읽을 대상은 항상 있다.
            dst.write_bytes(await file.read())
            ingest_job(paths, dst, safe_name)
        except Exception as exc:
            record_error(paths, "io", None, str(exc))
        return {"jobId": job_id}

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        return _status_json(paths)

    @app.post("/api/jobs/{job_id}/series")
    def select_series(job_id: str, sel: SeriesSelection, bg: BackgroundTasks) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
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
        paths = _checked_job_paths(config.jobs_root, job_id)
        vdir = _confined_child(paths.mesh_dir, variant_id, "variant_id")
        p = vdir / "regions.glb"
        if not p.is_file():
            raise HTTPException(404, "glb 없음")
        return FileResponse(p, media_type="model/gltf-binary")

    @app.get("/api/jobs/{job_id}/variants/{variant_id}/regions-meta.json")
    def meta(job_id: str, variant_id: str) -> FileResponse:
        paths = _checked_job_paths(config.jobs_root, job_id)
        vdir = _confined_child(paths.mesh_dir, variant_id, "variant_id")
        p = vdir / "regions-meta.json"
        if not p.is_file():
            raise HTTPException(404, "meta 없음")
        return FileResponse(p, media_type="application/json")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    return app
