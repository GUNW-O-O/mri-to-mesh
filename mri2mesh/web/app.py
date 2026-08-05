"""FastAPI 워크벤치 (스펙 §4, §6.3).

단일 사용자·로컬. 업로드 → 시리즈 게이트 → 백그라운드 파이프라인 → 뷰어.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Lock

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mri2mesh.jobs.layout import JobPaths, job_paths, new_job_id
from mri2mesh.jobs.pipeline import add_variant, ingest_job, run_segmentation_and_mesh
from mri2mesh.jobs.status import (
    JobStatus,
    now_iso,
    read_status,
    record_error,
    write_status,
)
from mri2mesh.mesh import GenerateError
from mri2mesh.mesh.params import parse_mesh_params

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
    raw_series = d.get("series") or []
    d["series"] = _strip_series(raw_series)
    d["selectedSeries"] = _strip_selected(d.get("selectedSeries"), raw_series)
    return d


def _strip_series(series: list[dict]) -> list[dict]:
    """서빙용 series — niftiPath를 벗기고 배열 위치를 index로 노출(스펙 §12)."""
    out = []
    for i, c in enumerate(series):
        out.append({
            "index": i,
            "description": c.get("description"),
            "slices": c.get("slices"),
            "voxelSizeMm": c.get("voxelSizeMm"),
            "acquisitionType": c.get("acquisitionType"),
            "score": c.get("score"),
            "reasons": c.get("reasons"),
        })
    return out


def _strip_selected(sel: dict | None, series: list[dict]) -> dict | None:
    """서빙용 selectedSeries — 경로 대신 index + 얕은 메타(스펙 §9.1)."""
    if not sel:
        return None
    idx = None
    for i, c in enumerate(series):
        if c.get("niftiPath") == sel.get("niftiPath"):
            idx = i
            break
    return {
        "index": idx,
        "description": sel.get("description") or (series[idx].get("description") if idx is not None else None),
        "slices": sel.get("slices") or (series[idx].get("slices") if idx is not None else None),
        "voxelSizeMm": sel.get("voxelSizeMm") or (series[idx].get("voxelSizeMm") if idx is not None else None),
    }


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
    #: 동시 세그멘테이션 상한. FastSurfer가 GPU를 물어 여러 잡을 동시에 돌리면
    #: 경합·OOM이 나므로 전역 세마포어로 제한한다(잡별 lock으론 부족).
    max_concurrent_seg: int = 2


class SeriesSelection(BaseModel):
    seriesIndex: int
    # 시리즈와 함께 고른 메쉬 파라미터. None이면 baseline(프로덕션 기준값)으로
    # 첫 변형을 만든다. 세그멘테이션은 이 값과 무관하게 항상 동일하게 돈다.
    params: dict | None = None
    # orig.nii.gz 얼굴 마스킹(defacing). 현재 미구현 — UI 토글 예정. True면
    # 파이프라인이 명시 실패한다.
    deface: bool = False


def _checked_job_paths(jobs_root: Path, job_id: str) -> JobPaths:
    """job_id를 jobs_root에 묶어 확인한 뒤 JobPaths를 돌려준다(경로 조작 방지)."""
    root = _confined_child(jobs_root, job_id, "job_id")
    return JobPaths(root=root)


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="mri2mesh")
    config.jobs_root.mkdir(parents=True, exist_ok=True)
    series_locks: dict[str, Lock] = {}
    # 변형 생성 진행 상태(토큰 → {done,total,variantId,finished,error}). 단일
    # 프로세스 로컬 도구라 인메모리로 충분. 프론트가 토큰으로 폴링한다.
    gen_progress: dict[str, dict] = {}
    # 동시 세그멘테이션 상한(GPU 경합 방지). 백그라운드 태스크는 스레드풀에서
    # 돌므로 threading 세마포어로 게이트한다. 초과 잡은 여기서 대기한다.
    seg_semaphore = BoundedSemaphore(max(1, config.max_concurrent_seg))

    def _clean_name(name: str | None, fallback: str) -> str:
        if not name:
            return fallback
        # 사용자 표시용 라벨 — 파일시스템 경로로는 절대 안 쓴다. 제어문자만
        # 걷어내고 길이를 자른다(PHI 판단은 안 한다 — 본인이 자기 브라우저에
        # 붙이는 라벨이다).
        cleaned = "".join(ch for ch in name if ch.isprintable()).strip()
        return cleaned[:120] or fallback

    @app.post("/api/jobs")
    async def upload(files: list[UploadFile], name: str = Form(None)) -> dict:
        job_id = new_job_id()
        paths = job_paths(config.jobs_root, job_id).create()
        case_name = _clean_name(name, job_id)

        # record_error가 되돌아갈 최소 status.json을 파일 쓰기보다 먼저 써
        # 둔다 — 디스크 풀·권한 오류·클라이언트 연결 끊김으로 파일 쓰기 자체가
        # 터져도(아래 for 루프), status.json 없이 맨 500이 나가거나 잡
        # 디렉터리가 기록 하나 없이 버려지는 일이 없어야 한다.
        write_status(paths, JobStatus(
            job_id=job_id, case_name=case_name, created_at=now_iso(), updated_at=now_iso(),
            state="running", step="io", input={"filename": "<file>", "bytes": 0},
        ))

        try:
            # 클라가 준 파일명·상대경로는 통째 버리고 번호로 평탄 저장한다. NAS
            # 긴 경로·하위폴더 동명 충돌·파일명 PHI·윈도우 MAX_PATH를 한 번에
            # 없앤다. DICOM 순서는 dcm2niix가 태그로 잡으므로 이름은 무의미하다.
            orig_names = [f.filename or "" for f in files]
            saved = []
            for i, f in enumerate(files, start=1):
                dst = paths.input_dir / f"{i:04d}"
                dst.write_bytes(await f.read())
                saved.append(dst)

            # 입력 판별: 파일 1개면 그 파일로(zip/nifti 자동판별), 여러 개면
            # input_dir 전체를 DICOM 폴더로 넘긴다(prepare_input이 디렉터리
            # 모드).
            src = saved[0] if len(saved) == 1 else paths.input_dir
            ingest_job(paths, src, "<file>", original_filenames=orig_names)
        except Exception as exc:  # noqa: BLE001
            record_error(paths, "io", None, str(exc))
        return {"jobId": job_id}

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        rows = []
        for child in config.jobs_root.iterdir():
            if not child.is_dir():
                continue
            paths = JobPaths(root=child)
            if not paths.status_file.is_file():
                continue
            try:
                st = read_status(paths)
            except (OSError, ValueError, KeyError):
                # 반쯤 쓰다 만 status.json은 목록에서 조용히 건너뛴다 —
                # 목록 하나 때문에 500을 내지 않는다.
                continue
            rows.append({
                "jobId": st.job_id,
                "name": st.case_name,
                "state": st.state,
                "step": st.step,
                "createdAt": st.created_at,
                "variantCount": len(st.variants),
            })
        rows.sort(key=lambda r: r["createdAt"], reverse=True)
        return rows

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        return _status_json(paths)

    @app.get("/api/jobs/{job_id}/dicom-meta")
    def dicom_meta(job_id: str) -> FileResponse:
        paths = _checked_job_paths(config.jobs_root, job_id)
        p = paths.dicom_meta_file
        if not p.is_file():
            raise HTTPException(404, "dicom-meta 없음")
        return FileResponse(p, media_type="application/json")

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict:
        # _checked_job_paths가 job_id를 jobs_root 밑으로 가둔다(경로 조작 방지).
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        shutil.rmtree(paths.root)
        return {"deleted": job_id}

    @app.post("/api/jobs/{job_id}/series")
    def select_series(job_id: str, sel: SeriesSelection, bg: BackgroundTasks) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")

        # 메쉬 파라미터를 여기서 파싱해 잘못된 입력이면 즉시 400을 준다(백그라운드로
        # 넘어가기 전에). None이면 파이프라인이 baseline으로 채운다.
        mesh_params = None
        if sel.params is not None:
            try:
                mesh_params = parse_mesh_params(sel.params)
            except ValueError:
                raise HTTPException(400, "잘못된 메쉬 파라미터")

        # 버튼 연타·반복 POST가 같은 출력 폴더에서 FastSurfer를 중복 실행하지
        # 못하게, 잡별 lock 안에서 상태 확인과 running 전환을 한 동작으로 묶는다.
        # 프로세스 하나인 로컬 워크벤치 범위의 lock이다.
        lock = series_locks.setdefault(job_id, Lock())
        with lock:
            # 클라가 index를 보내고, 서버는 자기 신뢰 데이터(status.series)에서
            # niftiPath를 꺼낸다 — 클라가 경로 자체를 보낼 필요가 없어져
            # 경로조작(path traversal / arbitrary read) 표면이 사라진다.
            job_status = read_status(paths)
            if job_status.state != "awaiting_series":
                raise HTTPException(409, "이미 시작됐거나 선택할 수 없는 job")
            series = job_status.series
            if not (0 <= sel.seriesIndex < len(series)):
                raise HTTPException(400, "잘못된 시리즈 index")
            selected_nifti = series[sel.seriesIndex]["niftiPath"]
            job_status.state = "running"
            job_status.step = "segment"
            write_status(paths, job_status)

        def work():
            # 파이프라인은 예상한 실패(SegmentError 등)는 단계별로 record_error
            # 하지만, 예상 못 한 예외(ValueError·KeyError·MemoryError 등)는
            # 새 나간다. 백그라운드 태스크에는 그걸 받아줄 곳이 없어 잡이
            # status="running"에 영원히 박힌다 — 업로드 경로처럼 catch-all로
            # 막는다. record_error가 sanitize_stderr로 PHI를 지운다.
            try:
                # 전역 세마포어로 동시 세그를 상한까지만 — 초과분은 여기서 대기.
                # GPU를 무는 FastSurfer가 여러 개 겹쳐 OOM 나는 걸 막는다.
                with seg_semaphore:
                    run_segmentation_and_mesh(
                        paths, Path(selected_nifti), config.fastsurfer_image,
                        threads=config.threads, fastsurfer_runner=config.fastsurfer_runner,
                        params=mesh_params, deface=sel.deface,
                        jobs_root=config.jobs_root, host_jobs_root=config.host_jobs_root,
                    )
            except Exception as exc:  # noqa: BLE001 — 잡을 running에 남기지 않는다
                record_error(paths, read_status(paths).step or "pipeline", None, str(exc))

        bg.add_task(work)
        return {"state": "running"}

    @app.post("/api/jobs/{job_id}/variants")
    def create_variant(job_id: str, payload: dict, bg: BackgroundTasks) -> dict:
        # 변형 메쉬 생성은 라벨 수십~백 개라 수십 초 걸린다 — 백그라운드로 돌리고
        # 토큰으로 진행(라벨 done/total)을 폴링하게 한다.
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        try:
            params = parse_mesh_params(payload)
        except ValueError:
            # 메시지에 입력값을 되쏘지 않는다(PHI/안전)
            raise HTTPException(400, "잘못된 메쉬 파라미터")

        token = uuid.uuid4().hex
        gen_progress[token] = {"done": 0, "total": 0, "variantId": None,
                               "deduped": False, "finished": False, "error": None}

        def work():
            # 같은 잡에서 동시 생성이 순번을 겹치지 않게 잡별 lock으로 묶는다
            lock = series_locks.setdefault(job_id, Lock())
            with lock:
                try:
                    res = add_variant(
                        paths, params,
                        progress_cb=lambda d, t: gen_progress[token].update(done=d, total=t),
                    )
                    gen_progress[token].update(
                        variantId=res["variantId"], deduped=res.get("deduped", False), finished=True)
                except ValueError:
                    gen_progress[token].update(error="done 잡에서만 변형을 생성할 수 있다", finished=True)
                except GenerateError:
                    # 예외 메시지에 seg 경로가 섞일 수 있어(PHI) 본문엔 안 담는다
                    gen_progress[token].update(error="메쉬를 생성하지 못했습니다", finished=True)
                except Exception:  # noqa: BLE001 — 잔여 예외로 토큰이 영원히 미완료로 남지 않게
                    gen_progress[token].update(error="변형 생성 실패", finished=True)

        bg.add_task(work)
        return {"token": token}

    @app.get("/api/jobs/{job_id}/variants-progress/{token}")
    def variant_progress(job_id: str, token: str) -> dict:
        # 로컬 인메모리 진행 상태(단일 프로세스). 토큰은 추측 불가한 uuid다.
        p = gen_progress.get(token)
        if p is None:
            raise HTTPException(404, "진행 없음")
        return p

    @app.delete("/api/jobs/{job_id}/variants/{variant_id}")
    def delete_variant(job_id: str, variant_id: str) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        vdir = _confined_child(paths.mesh_dir, variant_id, "variant_id")
        # status 갱신과 폴더 삭제를 잡별 lock 안에서 — 동시 변형 생성/삭제가
        # variants 목록을 깨뜨리지 않게.
        lock = series_locks.setdefault(job_id, Lock())
        with lock:
            status = read_status(paths)
            kept = [v for v in status.variants if v["variantId"] != variant_id]
            if len(kept) == len(status.variants):
                raise HTTPException(404, "변형 없음")
            status.variants = kept
            write_status(paths, status)
            if vdir.is_dir():
                shutil.rmtree(vdir)
        return {"deleted": variant_id}

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

    @app.get("/api/jobs/{job_id}/variants/{variant_id}/params.json")
    def variant_params(job_id: str, variant_id: str) -> FileResponse:
        # 변형이 무슨 옵션으로 산출됐는지(사람이 읽을 요약의 원천). 메쉬 파라미터
        # 뿐이라 PHI 아님. 옛 잡은 status.variants에 params가 없어도 이 파일은 있다.
        paths = _checked_job_paths(config.jobs_root, job_id)
        vdir = _confined_child(paths.mesh_dir, variant_id, "variant_id")
        p = vdir / "params.json"
        if not p.is_file():
            raise HTTPException(404, "params 없음")
        return FileResponse(p, media_type="application/json")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    # viewer.js와 vendor한 three.js를 서빙한다. StaticFiles는 마운트 디렉터리
    # 밖으로 나가지 못한다(Starlette 자체 방어) — static/ 안만 노출된다.
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    return app
