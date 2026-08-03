# 워크벤치 P1 — 뼈대 + 실사용 흐름 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 브라우저 하나로 업로드→시리즈 선택→진행률→단일 뷰어를 완주한다.

**Architecture:** FastAPI가 정적 `index.html`을 한 번 뱉고(SSR 없음), 클라이언트 ES
모듈이 `status.json`을 폴링하며 DOM을 조작한다. 사이드바(잡 목록) + 업로드 모달 +
상태별 메인 스테이지 + 항상 마운트된 three.js 캔버스. 시리즈 선택은 index 계약으로
바꿔 절대경로 노출을 없앤다. NAS 긴 경로는 업로드 시 번호 평탄저장으로 견딘다.

**Tech Stack:** Python/FastAPI, 순수 ES 모듈(프레임워크·번들러 없음), vendored
three.js r0.160.0, pytest + TestClient(httpx2).

**설계:** `docs/superpowers/specs/2026-08-03-workbench-series-compare-design.md`
**시각 목표:** `wireframe.html`(리포 루트, 레이아웃 참조용 목업).

## Global Constraints

- **완전 로컬 · 외부 통신 0.** 프론트 에셋에 CDN·외부 폰트·외부 이미지·analytics 금지.
  importmap은 `/static/vendor`만. `api.js` fetch는 same-origin 상대경로만.
- **PHI (§12).** 브라우저로 나가는 JSON에 원본 파일명·경로·DICOM 식별자 금지. 디스크
  `status.json`은 `niftiPath` 유지, **서빙 시에만** 벗긴다.
- **시리즈 자동선택 금지 (§6.3).** `ingest_job`은 `awaiting_series`에서 멈춘다.
- **`--no_cc` 금지.** FastSurfer 호출에 절대 안 넘긴다.
- **산출 4파일 유지.** 에셋 산출 툴이다. 과설계 금지.
- **리포에 `.env.example`만.** test-asset/ 실데이터 커밋 금지. 실제 환자 식별자를
  코드·테스트·픽스처·문서에 절대 넣지 않는다(가짜 이름 `Hong_Gil_Dong` 사용).
- **STATES = ("awaiting_series", "running", "done", "error").** `write_status`가 검증.

---

## File Structure

- `seg_and_mesh/web/app.py` — 라우트. list·upload(다중+이름)·series(index)·`_status_json` 수정.
- `seg_and_mesh/jobs/pipeline.py` — `ingest_job`은 그대로(series 배열 위치=index). 신규 없음.
- `seg_and_mesh/jobs/status.py` — 변경 없음(선택). selectedSeries는 pipeline이 dict로 씀.
- `seg_and_mesh/web/static/index.html` — 셸 재작성.
- `seg_and_mesh/web/static/api.js` — 신규(fetch 래퍼).
- `seg_and_mesh/web/static/app.js` — 신규(컨트롤러).
- `seg_and_mesh/web/static/viewer.js` — 리팩터(단일 슬롯 구조, 진입점을 app.js가 부름).
- `tests/web/test_app.py` — index 계약으로 마이그레이션 + 신규 테스트.
- `tests/web/test_local_only.py` — 신규(외부참조 가드).

---

## Task 1: `GET /api/jobs` 목록 엔드포인트

**Files:**
- Modify: `seg_and_mesh/web/app.py`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `config.jobs_root: Path`, `read_status(paths) -> JobStatus`, `JobPaths(root=...)`.
- Produces: `GET /api/jobs` → `list[dict]`, 각 `{jobId, name, state, step, createdAt, variantCount}`.

- [ ] **Step 1: 실패 테스트 작성**

`tests/web/test_app.py`에 추가:

```python
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
    assert "series" not in blob


def test_list_jobs_empty(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/jobs").json() == []
```

파일 상단 import에 `import json`이 이미 있는지 확인, 없으면 추가.

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest tests/web/test_app.py::test_list_jobs_returns_summaries_without_phi -q`
Expected: FAIL (404 또는 라우트 없음).

- [ ] **Step 3: 구현**

`app.py`의 `create_app` 안, `@app.get("/api/jobs/{job_id}")` **위에** 추가
(FastAPI는 구체 경로를 먼저 등록해야 `{job_id}`가 `jobs`를 안 삼킨다 — 하지만
`/api/jobs`와 `/api/jobs/{job_id}`는 경로 세그먼트 수가 달라 충돌하지 않는다.
그래도 가독성 위해 위에 둔다):

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest tests/web/test_app.py -k list_jobs -q`
Expected: PASS (2개).

- [ ] **Step 5: 커밋**

```bash
git add seg_and_mesh/web/app.py tests/web/test_app.py
git commit -m "feat(web): GET /api/jobs 목록 엔드포인트(PHI 없는 요약)"
```

---

## Task 2: 다중 파일 업로드 + 이름 + 번호 평탄저장

**Files:**
- Modify: `seg_and_mesh/web/app.py` (`upload` 라우트)
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `job_paths`, `new_job_id`, `ingest_job(paths, src, filename)`, `write_status`,
  `JobStatus`, `now_iso`, `record_error`.
- Produces: `POST /api/jobs` multipart `files: list[UploadFile]` + `name: str = Form(None)`
  → `{jobId}`. 파일은 `input_dir/0001, 0002, …`로 평탄 저장. `case_name`은 정제된 `name`
  (없으면 job_id).

**배경:** 현행 `upload`는 단건 `file: UploadFile`을 받아 `paths.input_dir/safe_name`에
쓴다. NAS DICOM 폴더는 파일 여럿 + 긴 상대경로다. `prepare_input`은 폴더를 받으면
매직바이트로 DICOM을 추리고 파일명을 안 본다(`io/ingest.py:_prepare_dir`). 그래서
이름을 통째 버리고 번호로 평탄 저장하면 긴 경로·충돌·파일명 PHI·MAX_PATH가 한 번에
해소된다.

- [ ] **Step 1: 실패 테스트 작성**

기존 `test_upload_creates_job_awaiting_series`는 `files={"file": ...}` 단건이라
새 계약(`files` 리스트)으로 **고치고**, 아래를 추가:

```python
def _sanitize_name(raw):
    # 테스트 편의용 기대값 헬퍼 — 구현과 무관하게 규칙만 표현
    return raw

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
```

`_fastsurfer_mock`가 DICOM 목 파일에 반응하지 않도록 — 이 테스트들은 `awaiting_series`
까지만 가므로 세그는 안 돈다(DICOM 목은 dcm2niix가 없으면 `Dcm2niixError`로 error가
될 수 있다). 단정은 `input_dir` 파일명과 `name`뿐이라 state와 무관하게 통과한다.

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest tests/web/test_app.py -k "flattened or name_defaults or control_chars" -q`
Expected: FAIL.

- [ ] **Step 3: 구현**

`app.py` import에 `Form` 추가: `from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile`.

`upload` 라우트를 교체:

```python
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

        # 클라가 준 파일명·상대경로는 통째 버리고 번호로 평탄 저장한다. NAS
        # 긴 경로·하위폴더 동명 충돌·파일명 PHI·윈도우 MAX_PATH를 한 번에
        # 없앤다. DICOM 순서는 dcm2niix가 태그로 잡으므로 이름은 무의미하다.
        saved = []
        for i, f in enumerate(files, start=1):
            dst = paths.input_dir / f"{i:04d}"
            dst.write_bytes(await f.read())
            saved.append(dst)

        # record_error가 되돌아갈 최소 status.json을 먼저 써 둔다.
        write_status(paths, JobStatus(
            job_id=job_id, case_name=case_name, created_at=now_iso(), updated_at=now_iso(),
            state="running", step="io", input={"filename": "<file>", "bytes": 0},
        ))

        # 입력 판별: 파일 1개면 그 파일로(zip/nifti 자동판별), 여러 개면
        # input_dir 전체를 DICOM 폴더로 넘긴다(prepare_input이 디렉터리 모드).
        src = saved[0] if len(saved) == 1 else paths.input_dir
        try:
            ingest_job(paths, src, "<file>")
        except Exception as exc:  # noqa: BLE001
            record_error(paths, "io", None, str(exc))
        return {"jobId": job_id}
```

**주의:** `ingest_job`의 세 번째 인자 `filename`은 `status.input["filename"]`에 들어가
서빙 시 어차피 `<file>`로 마스킹되지만, 여기서 미리 `"<file>"`을 넘겨 디스크에도 원본
파일명이 안 남게 한다(설계의 "디스크에도 안 남김" 강화).

`ingest_job`이 `input_dir`(디렉터리)를 `src`로 받는 경우 `Path(src).stat().st_size`가
디렉터리 크기를 재는데(pipeline.py:70), 디렉터리에도 `stat()`은 동작하므로 예외는 안
난다. bytes 값 의미가 약할 뿐이라 그대로 둔다.

- [ ] **Step 4: 통과 확인 + 회귀**

Run: `uv run --frozen pytest tests/web/test_app.py -q`
Expected: 이 태스크 신규 3개 PASS. 단, `files={"file": ...}` 단건을 쓰던 **기존
테스트들이 깨진다** — 다음 스텝에서 전부 `files={"files": ...}`로 고친다.

- [ ] **Step 5: 기존 테스트 계약 이전**

`tests/web/test_app.py`에서 `files={"file":` 를 전부 `files={"files":` 로 바꾼다
(단건도 `files` 키를 쓴다 — FastAPI `list[UploadFile]`은 같은 키 반복 또는 단일도 받음).
grep로 남은 것 없나 확인:

Run: `uv run --frozen pytest tests/web/test_app.py -q`
Expected: PASS (niftiPath 계약을 쓰는 series 관련 테스트는 Task 3에서 마저 고친다 —
이 스텝에서는 업로드 키만 정리).

- [ ] **Step 6: 커밋**

```bash
git add seg_and_mesh/web/app.py tests/web/test_app.py
git commit -m "feat(web): 다중 파일 업로드·이름 지정·번호 평탄저장(NAS 긴 경로 견딤)"
```

---

## Task 3: 시리즈 index 계약 (절대경로 노출 제거)

**Files:**
- Modify: `seg_and_mesh/web/app.py` (`_status_json`, `select_series`, `SeriesSelection`)
- Modify: `seg_and_mesh/jobs/pipeline.py` (`run_segmentation_and_mesh`의 `selected_series`)
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `read_status(paths).series: list[dict]` (각 dict에 `niftiPath` 키 있음).
- Produces:
  - `POST /api/jobs/{id}/series` 바디 `{seriesIndex: int}` → `{state: "running"}`. 범위 밖 400.
  - 서빙 `series[]` = `{index, description, slices, voxelSizeMm, acquisitionType, score, reasons}`
    (**niftiPath 없음**).
  - 서빙 `selectedSeries` = `{index, description, slices, voxelSizeMm}` (경로 없음).

- [ ] **Step 1: 실패 테스트 작성 + 기존 series 테스트 이전**

기존 series 테스트들을 index 계약으로 고친다. 예:

```python
def test_series_selection_runs_pipeline_to_done(tmp_path):
    client, holder = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    holder["fs_dir"] = tmp_path / "jobs" / jid / "fs"
    r = client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 0})
    assert r.status_code == 200
    # ... 이하 done 확인은 그대로
```

신규:

```python
def test_served_status_has_no_niftipath(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    s = client.get(f"/api/jobs/{jid}").json()
    assert s["state"] == "awaiting_series"
    assert "niftiPath" not in json.dumps(s)      # 어디에도 경로 없음
    assert s["series"][0]["index"] == 0          # index로 식별
    assert "description" in s["series"][0]


def test_series_selection_by_index_rejects_out_of_range(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("scan.nii.gz", _nifti_bytes())}).json()["jobId"]
    r = client.post(f"/api/jobs/{jid}/series", json={"seriesIndex": 99})
    assert r.status_code == 400


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
```

`test_series_selection_rejects_path_not_in_candidates`,
`test_series_selection_unknown_job_is_404`(바디를 `{"seriesIndex": 0}`로),
`test_glb_and_meta_are_served_after_done`, `test_dotted_job_id_is_not_falsely_rejected`
등 `niftiPath`를 바디로 보내던 테스트를 전부 `{"seriesIndex": ...}`로 이전. 경로가
후보에 없을 때를 검사하던 테스트는 "범위 밖 index → 400"으로 대체(위 신규가 흡수).

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest tests/web/test_app.py -k "niftipath or by_index or selected_series or runs_pipeline" -q`
Expected: FAIL.

- [ ] **Step 3: 구현 — 서빙 벗기기**

`app.py`의 `_status_json`을 확장:

```python
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
```

`_status_json` 본문에서 `series`·`selectedSeries`를 교체:

```python
    d = read_status(paths).to_json_dict()
    if d.get("input") and d["input"].get("filename"):
        d["input"] = {**d["input"], "filename": "<file>"}
    raw_series = d.get("series") or []
    d["series"] = _strip_series(raw_series)
    d["selectedSeries"] = _strip_selected(d.get("selectedSeries"), raw_series)
    return d
```

- [ ] **Step 4: 구현 — 선택을 index로**

`SeriesSelection` 교체:

```python
class SeriesSelection(BaseModel):
    seriesIndex: int
```

`select_series` 라우트의 후보 검증·매핑을 index로:

```python
        job_status = read_status(paths)
        series = job_status.series
        if not (0 <= sel.seriesIndex < len(series)):
            raise HTTPException(400, "잘못된 시리즈 index")
        selected_nifti = series[sel.seriesIndex]["niftiPath"]

        def work():
            try:
                run_segmentation_and_mesh(
                    paths, Path(selected_nifti), config.fastsurfer_image,
                    threads=config.threads, fastsurfer_runner=config.fastsurfer_runner,
                    jobs_root=config.jobs_root, host_jobs_root=config.host_jobs_root,
                )
            except Exception as exc:  # noqa: BLE001 — 잡을 running에 남기지 않는다
                record_error(paths, read_status(paths).step or "pipeline", None, str(exc))

        bg.add_task(work)
        return {"state": "running"}
```

`selected_nifti`은 서버가 자기 신뢰 데이터(`status.series`)에서 꺼낸 값이라 클라가
경로를 보낼 필요가 없다 — 경로조작 표면이 사라진다.

- [ ] **Step 5: 구현 — selectedSeries 디스크 기록 보강**

`pipeline.py`의 `run_segmentation_and_mesh`에서 `selected_series`가 지금
`{"niftiPath": str(selected_nifti)}`만 쓴다(line 112). 서빙 쪽에서 index를 도로
찾을 수 있게 얕은 메타도 같이 남긴다:

```python
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
```

- [ ] **Step 6: 통과 확인 (전체)**

Run: `uv run --frozen pytest tests/web/ -q`
Expected: PASS. 남은 `niftiPath` 바디/응답 참조가 있으면 이전 누락 — 고친다.

- [ ] **Step 7: 커밋**

```bash
git add seg_and_mesh/web/app.py seg_and_mesh/jobs/pipeline.py tests/web/test_app.py
git commit -m "feat(web): 시리즈 index 계약 — 서빙에서 niftiPath 제거(절대경로 노출 해소)"
```

---

## Task 4: 외부 참조 없음 가드 테스트

**Files:**
- Create: `tests/web/test_local_only.py`

**Interfaces:** 없음(정적 트리 검사).

- [ ] **Step 1: 테스트 작성**

```python
"""완전 로컬 · 외부 통신 0 가드 — static 에셋에 외부 참조가 없어야 한다."""
import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "seg_and_mesh" / "web" / "static"

# vendor/ 는 로컬 번들이라 검사 대상이 아니지만, 그 안에도 외부 URL이 박혀선
# 안 되므로 함께 훑는다. 예외: three.js 소스 주석의 라이선스 URL은 실행 시
# 요청이 아니다 — 그래서 <script src>/import/fetch/url() 문맥만 잡는다.
_EXTERNAL = re.compile(
    r"""(?:src|href)\s*=\s*['"]https?://"""
    r"""|from\s+['"]https?://"""
    r"""|import\s*\(\s*['"]https?://"""
    r"""|fetch\(\s*['"]https?://"""
    r"""|url\(\s*['"]?https?://"""
    r"""|//(?:cdn|unpkg|esm\.sh|cdn\.jsdelivr|fonts\.googleapis)""",
)


def test_no_external_references_in_own_assets():
    offenders = []
    for p in _STATIC.rglob("*"):
        if not p.is_file() or p.suffix not in (".html", ".js", ".css"):
            continue
        # vendored three.js 자체는 우리가 작성한 코드가 아니므로 제외한다.
        if "vendor" in p.relative_to(_STATIC).parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in _EXTERNAL.finditer(text):
            offenders.append(f"{p.name}: …{text[max(0,m.start()-20):m.start()+40]}…")
    assert not offenders, "외부 참조 발견:\n" + "\n".join(offenders)
```

- [ ] **Step 2: 통과 확인**

Run: `uv run --frozen pytest tests/web/test_local_only.py -q`
Expected: PASS (현재 자산엔 외부 참조 없음). 이후 프론트 태스크가 이 가드를 계속 통과해야 한다.

- [ ] **Step 3: 커밋**

```bash
git add tests/web/test_local_only.py
git commit -m "test(web): static 에셋 외부 참조 없음 가드(완전 로컬)"
```

---

## Task 5: 프론트 셸 — index.html

**Files:**
- Modify: `seg_and_mesh/web/static/index.html`

**Interfaces:**
- Produces: DOM 컨트랙트 — `#sidebar #joblist`, `#new-job`(버튼), `#overlay #modal`,
  `#drop`, `#name`, `#file-input`, `#upload-go`, `#upload-cancel`, `#canvas`,
  `#stage-select`, `#stage-progress`, `#stage-error`, `#stage-empty`, `#vpanel`,
  `#groups`, `#metrics`, `#transparent`. app.js/viewer.js가 이 id들을 잡는다.

**참조:** `wireframe.html`(리포 루트)의 레이아웃·CSS를 실제 셸로 옮긴다. 단 와이어프레임
전용 "상태 미리보기 스위처"와 하드코딩된 더미 잡·시리즈는 뺀다.

- [ ] **Step 1: index.html 작성**

`wireframe.html`의 `<style>`과 마크업 골격을 가져오되:
- 사이드바 `#joblist`는 **비운다**(app.js가 채움).
- 메인 스테이지 4개를 빈 컨테이너로: `#stage-empty`(안내), `#stage-select`,
  `#stage-progress`, `#stage-error`. 전부 `display:none`으로 시작, app.js가 토글.
- `#canvas`는 고정 레이어로 항상 존재(스테이지 위/아래 관계는 CSS z-index).
- 뷰어 컨트롤 패널 `#vpanel`(반투명 체크 `#transparent`, `#groups`, `#metrics`)은
  `done` 스테이지에서만 보이게 app.js가 토글.
- importmap은 기존 그대로(`/static/vendor`만).
- 스크립트 진입점은 `<script type="module" src="/static/app.js"></script>` **하나**.
  viewer.js는 app.js가 import한다.

산출 마크업(요지):

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>seg-and-mesh 워크벤치</title>
<style>/* wireframe.html의 스타일 이식 — 스위처 관련 규칙 제외 */</style>
<script type="importmap">
{ "imports": {
  "three": "/static/vendor/three.module.js",
  "three/addons/": "/static/vendor/addons/"
} }
</script>
</head>
<body>
<canvas id="canvas"></canvas>

<aside id="sidebar">
  <header><h1>seg-and-mesh</h1><button id="new-job" class="new-btn">+ 새 작업</button></header>
  <div id="joblist"></div>
</aside>

<div id="main">
  <div id="stage-empty" class="panel">왼쪽에서 작업을 고르거나 새로 만드세요.</div>
  <div id="stage-select" class="panel" style="display:none"></div>
  <div id="stage-progress" class="panel" style="display:none"></div>
  <div id="stage-error" class="panel" style="display:none"></div>
  <div id="vpanel" style="display:none">
    <label><input type="checkbox" id="transparent"> 반투명 셸</label>
    <div id="groups"></div>
    <div id="metrics"></div>
  </div>
</div>

<div id="overlay">
  <div id="modal">
    <h3>새 작업 — MRI 업로드</h3>
    <input id="name" type="text" placeholder="작업 이름(선택)">
    <div id="drop" class="drop"><b>DICOM 폴더·zip / NIfTI</b> 끌어놓기 또는 클릭</div>
    <input id="file-input" type="file" webkitdirectory multiple style="display:none">
    <div class="modal-actions">
      <button id="upload-cancel" class="ghost">취소</button>
      <button id="upload-go" class="primary">업로드</button>
    </div>
  </div>
</div>

<script type="module" src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 서빙·가드 확인**

Run: `uv run --frozen pytest tests/web/test_app.py::test_index_serves_viewer tests/web/test_local_only.py -q`
Expected: PASS. (`test_index_serves_viewer`가 특정 문자열을 기대하면 그에 맞게 조정.)

- [ ] **Step 3: 커밋**

```bash
git add seg_and_mesh/web/static/index.html
git commit -m "feat(web): 워크벤치 셸 — 사이드바·업로드 모달·상태 스테이지·캔버스"
```

---

## Task 6: api.js — fetch 래퍼

**Files:**
- Create: `seg_and_mesh/web/static/api.js`

**Interfaces:**
- Produces (ES module export):
  - `listJobs() -> Promise<Array>`
  - `upload(files: File[], name: string) -> Promise<{jobId}>`
  - `getStatus(jobId) -> Promise<Object>`
  - `selectSeries(jobId, index: number) -> Promise<Object>`
  - `glbUrl(jobId, variantId) -> string`
  - `metaUrl(jobId, variantId) -> string`

- [ ] **Step 1: api.js 작성**

같은 오리진 상대경로만 사용(외부 참조 가드 통과):

```javascript
// 엔드포인트 fetch 래퍼. 전부 same-origin 상대경로 — 외부 통신 0(전역 제약).
async function j(res) {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function listJobs() {
  return j(await fetch('/api/jobs'));
}

export async function upload(files, name) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  if (name) fd.append('name', name);
  return j(await fetch('/api/jobs', { method: 'POST', body: fd }));
}

export async function getStatus(jobId) {
  return j(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`));
}

export async function selectSeries(jobId, index) {
  return j(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/series`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ seriesIndex: index }),
  }));
}

export function glbUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions.glb`;
}

export function metaUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions-meta.json`;
}
```

- [ ] **Step 2: 가드 확인**

Run: `uv run --frozen pytest tests/web/test_local_only.py -q`
Expected: PASS.

- [ ] **Step 3: 커밋**

```bash
git add seg_and_mesh/web/static/api.js
git commit -m "feat(web): api.js — same-origin fetch 래퍼"
```

---

## Task 7: viewer.js — 단일 슬롯 리팩터

**Files:**
- Modify: `seg_and_mesh/web/static/viewer.js`

**Interfaces:**
- Consumes: `three`, `three/addons/...`(importmap), `metaUrl`/`glbUrl`는 app.js가 넘김.
- Produces (ES module export): `class Viewer`
  - `constructor(canvas: HTMLCanvasElement)`
  - `async showVariant(jobId, variantId)` — 기존 슬롯 비우고 변형 하나 로드·프레이밍.
  - `clear()`
  - `setTransparent(bool)`
  - `groupsEl`, `metricsEl` 연결은 app.js가 DOM 참조를 넘겨서 콜백으로 처리하거나,
    Viewer가 `#groups`/`#metrics`/`#transparent`를 직접 잡아도 된다(현행 방식 유지).

**배경:** 현행 `viewer.js`는 모듈 최상단에서 DOM(`#load` 버튼 등)을 직접 잡고 즉시
실행한다. 이걸 **클래스로 감싸** app.js가 생성·제어하게 바꾼다. 색(B안)·group/side
토글·반투명·metrics 로직은 그대로 옮긴다. P2에서 이 클래스에 슬롯 배열을 더한다.

- [ ] **Step 1: Viewer 클래스로 리팩터**

현행 로직(scene/camera/controls/lights/resize/frame/applyColors/updateVisibility/
renderToggleList/renderGroups/renderMetrics/animate)을 `class Viewer`의 메서드로 옮긴다.
`load(jobId, variantId)` → `showVariant(jobId, variantId)`로 개명하고, `#load` 버튼·
`#jobId`/`#variantId` 입력 의존(모듈 최상단 즉시 바인딩)을 제거한다 — 진입은 app.js가
`viewer.showVariant(...)`로 호출한다. `#transparent`.onchange, `#groups` 렌더는
Viewer 내부에 유지하되, 없을 수도 있는 DOM은 옵셔널 체이닝으로 방어한다.

fetch는 상대경로 유지(`/api/jobs/${jobId}/variants/${variantId}/...`) — 외부참조 가드 통과.

- [ ] **Step 2: 가드 확인**

Run: `uv run --frozen pytest tests/web/test_local_only.py -q`
Expected: PASS.

- [ ] **Step 3: 커밋**

```bash
git add seg_and_mesh/web/static/viewer.js
git commit -m "refactor(web): viewer를 Viewer 클래스로 — app.js가 생성·제어"
```

---

## Task 8: app.js — 컨트롤러 + 수동 E2E

**Files:**
- Create: `seg_and_mesh/web/static/app.js`

**Interfaces:**
- Consumes: `api.js`(전부), `Viewer`(viewer.js), index.html의 DOM id 컨트랙트.
- Produces: 앱 진입점. 사이드바 렌더·모달·폴링·스테이지 스위칭.

- [ ] **Step 1: app.js 작성**

```javascript
import * as api from './api.js';
import { Viewer } from './viewer.js';

const viewer = new Viewer(document.getElementById('canvas'));
let selectedJob = null;
let pollTimer = null;

// ---------- 사이드바 ----------
async function refreshJobs() {
  const rows = await api.listJobs();
  const el = document.getElementById('joblist');
  el.innerHTML = '';
  for (const r of rows) {
    const div = document.createElement('div');
    div.className = 'job' + (r.jobId === selectedJob ? ' active' : '');
    div.onclick = () => selectJob(r.jobId);
    div.innerHTML =
      `<div class="name">${r.name}</div>` +
      `<div class="row"><span class="chip ${chipClass(r)}">${chipText(r)}</span></div>`;
    el.append(div);
  }
  // 진행 중인 잡이 있으면 목록도 계속 갱신
  if (rows.some(r => r.state === 'running')) scheduleList();
}
function chipClass(r){ return r.state==='done'?'done':r.state==='error'?'err':r.state==='awaiting_series'?'await':'run'; }
function chipText(r){ return r.state==='done'?'완료':r.state==='error'?'실패':r.state==='awaiting_series'?'시리즈 선택':`${r.step} 중`; }

let listTimer = null;
function scheduleList(){ clearTimeout(listTimer); listTimer = setTimeout(refreshJobs, 2000); }

// ---------- 잡 선택 + 폴링 ----------
async function selectJob(jobId) {
  selectedJob = jobId;
  clearTimeout(pollTimer);
  await refreshJobs();
  await renderStage();
}

async function renderStage() {
  const s = await api.getStatus(selectedJob);
  showStage(s.state);
  if (s.state === 'awaiting_series') renderSelect(s);
  else if (s.state === 'running') { renderProgress(s); poll(); }
  else if (s.state === 'error') renderError(s);
  else if (s.state === 'done') showViewer(s);
}
function poll(){ clearTimeout(pollTimer); pollTimer = setTimeout(renderStage, 1500); }

function showStage(state) {
  const map = { awaiting_series:'stage-select', running:'stage-progress', error:'stage-error' };
  for (const id of ['stage-empty','stage-select','stage-progress','stage-error'])
    document.getElementById(id).style.display = 'none';
  document.getElementById('vpanel').style.display = state==='done' ? 'block' : 'none';
  const show = map[state];
  if (show) document.getElementById(show).style.display = 'block';
}

// ---------- 시리즈 선택 스테이지 ----------
function renderSelect(s) {
  const el = document.getElementById('stage-select');
  el.innerHTML = '<h2>세그할 시리즈 선택</h2><div class="sub">자동선택 안 함 — 하나 고르세요.</div>';
  s.series.forEach((c, i) => {
    const d = document.createElement('div');
    d.className = 'series' + (i===0?' sel':'');
    d.innerHTML =
      `<input type="radio" name="s" ${i===0?'checked':''}>` +
      `<div class="meta"><div class="title">${c.description ?? '(설명 없음)'}</div>` +
      `<div class="facts">${c.slices}슬 · ${(c.voxelSizeMm||[]).join('×')}mm · ${c.acquisitionType??''}</div>` +
      `<div class="reasons">${(c.reasons||[]).join(' · ')}</div></div>` +
      `<span class="rank">${i+1}순위 · ${c.score}</span>`;
    d.onclick = () => { el.querySelectorAll('.series').forEach(x=>x.classList.remove('sel'));
                        d.classList.add('sel'); d.querySelector('input').checked = true;
                        el.dataset.pick = i; };
    el.append(d);
  });
  el.dataset.pick = 0;
  const go = document.createElement('button');
  go.className = 'primary'; go.textContent = '세그 시작 →';
  go.onclick = async () => { await api.selectSeries(selectedJob, Number(el.dataset.pick));
                             await refreshJobs(); renderStage(); };
  el.append(go);
}

// ---------- 진행률 ----------
function renderProgress(s) {
  const order = ['io','segment','remap','mesh'];
  const cur = order.indexOf(s.step);
  const el = document.getElementById('stage-progress');
  el.innerHTML = `<h2>처리 중</h2><div class="steps">` +
    ['업로드·dcm2niix','세그멘테이션','라벨 리맵','메시 생성'].map((label, i) => {
      const cls = i<cur?'ok':i===cur?'now':'wait';
      const mark = i<cur?'✓':i===cur?'●':'○';
      return `<div class="step"><span class="dot ${cls}">${mark}</span> ${label}</div>`;
    }).join('') + `</div>`;
}

// ---------- 에러 ----------
function renderError(s) {
  const el = document.getElementById('stage-error');
  el.innerHTML = `<h2>실패 · ${s.step}</h2><div class="sub">${(s.error&&s.error.message)||''}</div>`;
}

// ---------- 뷰어 ----------
function showViewer(s) {
  const v = s.variants && s.variants[0];
  if (v) viewer.showVariant(selectedJob, v.variantId);
}

// ---------- 업로드 모달 ----------
const overlay = document.getElementById('overlay');
document.getElementById('new-job').onclick = () => overlay.classList.add('on');
document.getElementById('upload-cancel').onclick = () => overlay.classList.remove('on');
document.getElementById('drop').onclick = () => document.getElementById('file-input').click();
let picked = [];
document.getElementById('file-input').onchange = (e) => { picked = [...e.target.files]; };
setupDrop(document.getElementById('drop'), fs => { picked = fs; });
document.getElementById('upload-go').onclick = async () => {
  if (!picked.length) return;
  const name = document.getElementById('name').value.trim();
  const { jobId } = await api.upload(picked, name);
  overlay.classList.remove('on'); picked = []; document.getElementById('name').value = '';
  document.getElementById('file-input').value = '';
  await refreshJobs(); selectJob(jobId);
};

// 폴더 드롭: DataTransfer 항목을 재귀로 훑어 파일만 모은다.
function setupDrop(el, cb) {
  el.ondragover = (e) => { e.preventDefault(); };
  el.ondrop = async (e) => {
    e.preventDefault();
    const items = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
    const files = [];
    for (const entry of items) await walkEntry(entry, files);
    if (files.length) cb(files);
  };
}
function walkEntry(entry, out) {
  return new Promise((resolve) => {
    if (entry.isFile) entry.file(f => { out.push(f); resolve(); });
    else if (entry.isDirectory) {
      const rd = entry.createReader();
      rd.readEntries(async es => { for (const e of es) await walkEntry(e, out); resolve(); });
    } else resolve();
  });
}

// ---------- 부트 ----------
refreshJobs();
```

**주의:** app.js는 index.html의 CSS 클래스(`.job .chip .series .steps .dot` 등)를
쓴다 — Task 5의 스타일과 이름을 맞춘다. 셸 스타일에 `#overlay.on{display:grid}` 등
와이어프레임 규칙이 있어야 모달이 뜬다.

- [ ] **Step 2: 외부참조 가드**

Run: `uv run --frozen pytest tests/web/test_local_only.py -q`
Expected: PASS.

- [ ] **Step 3: 수동 E2E (실제 앱 기동)**

이 흐름은 헤드리스 단위테스트가 불가하다(WebGL·브라우저 파일 API). 수동 검증:

```bash
# 호스트 직접 실행(도커 없이 UI 흐름만): FastSurfer는 시리즈 선택까지 안 탄다.
cp .env.example .env    # FASTSURFER_IMAGE 등 채움
uv run --frozen uvicorn seg_and_mesh.web.server:app --host 127.0.0.1 --port 8000
```

브라우저 `http://127.0.0.1:8000`:
1. `+ 새 작업` → 모달 → 이름 입력 → NIfTI 파일 또는 DICOM 폴더 선택 → 업로드.
2. 사이드바에 잡이 뜨고 칩이 "시리즈 선택"이 되는지.
3. 시리즈 목록이 뜨고 1순위가 하이라이트되는지. 하나 골라 "세그 시작".
4. (도커+GPU 구성 시) 진행률 단계가 진행되고, done에서 뷰어에 메시가 뜨는지.
5. 브라우저 개발자도구 Network 탭: **외부 도메인 요청이 하나도 없는지**(전부 127.0.0.1).

- [ ] **Step 4: 전체 스위트**

Run: `uv run --frozen pytest -q`
Expected: PASS (기존 + 신규). 경고 0(`-W error`는 CI 설정에 따름).

- [ ] **Step 5: 커밋**

```bash
git add seg_and_mesh/web/static/app.js
git commit -m "feat(web): app.js 컨트롤러 — 사이드바·모달·폴링·스테이지 전환"
```

---

## Self-Review 메모(계획 작성자)

- **스펙 커버리지:** GET /api/jobs(T1), 다중업로드·이름·평탄저장(T2), index 계약·서빙
  벗기기(T3), 외부참조 가드(T4), 셸·모달·폴더드롭(T5·T8), api 계층(T6), 뷰어 리팩터(T7),
  캔버스 안정성(T5 고정 캔버스+T8 오버레이 토글). P2/P3(변형생성·비교)는 별도 계획.
- **niftiPath:** 디스크 유지·서빙 제거 일관(T3). 기존 테스트 전량 index로 이전(T2 Step5, T3 Step1).
- **프론트 TDD 한계:** JS 러너 없음 — 계약은 백엔드·가드로 커버, UI 흐름은 수동 E2E로
  명시(설계와 일치). 정직하게 표기.
- **의존 순서:** T5(DOM id) → T8(app.js가 그 id 사용). T6(api)·T7(viewer)는 T8 이전.
  T1~T4(백엔드)는 프론트와 독립, 먼저 간다.
