# 메쉬 생성옵션 비교 — A단계(생성옵션 흐름) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 메쉬 생성옵션을 골라 같은 잡에서 변형을 여러 개 생성할 수 있게 한다(재생성 엔드포인트 + 옵션 폼). 프로덕션 baseline이 기준선.

**Architecture:** 세그 캐시(`seg.nii.gz`)에서 메쉬만 다시 만드는 `generate_variant`는 이미 있다. (1) brainds 프로덕션 값을 `baseline_params()`로 추가하고 파이프라인 초기 v01·엔드포인트 기본값을 이걸로 통일, (2) 요청 dict→`MeshParams` 파싱·검증, (3) done 잡에 변형을 추가(중복은 파라미터 해시로 걸러 기존 반환)하는 함수, (4) `POST /api/jobs/{id}/variants` 라우트, (5) baseline으로 미리 채워지는 옵션 폼. 뷰어는 이 단계에서 단일 유지 — 새 변형 생성 시 그 변형으로 갱신만 한다(나란히 비교는 B단계).

**Tech Stack:** FastAPI, pydantic, three.js(바닐라 ES 모듈), pytest, TestClient.

## Global Constraints

- 완전 로컬 · 외부 통신 0. 정적 에셋에 외부 참조 금지(`tests/web/test_local_only.py` 가드 유지).
- PHI(스펙 §12): `status.json.error`·HTTP 응답에 파일명·경로·DICOM 태그 금지. 검증 실패 메시지도 입력값을 그대로 되쏘지 않는다.
- 전역 canonical 색: 뷰어는 `regions-meta.json`에서 색을 읽는다(B안). 이 단계에선 색 규칙 안 바꾼다.
- STATES = `("awaiting_series","running","done","error")`.
- 생성옵션 baseline(brainds `nifti_pipeline/mesh_export.py` 기준, 확인된 등가):
  preprocess `none` / extractor `vtk_contour_perlabel` / smoothing `laplacian(iterations=30, relaxation=0.1)` / decimation `none` / minVoxel `100`.
- 변형 중복 판정은 **파라미터 해시**로 한다 — `variantId`는 `v<순번>-<hash>`라 순번이 달라도 같은 파라미터면 같은 hash다.
- 변형 생성은 동기 요청(초 단위). 백그라운드/폴링 안 쓴다.

---

## File Structure

- `mri2mesh/mesh/params.py` — `baseline_params()` + `parse_mesh_params(payload)` 추가(검증 포함).
- `mri2mesh/jobs/pipeline.py` — 초기 변형을 `baseline_params()`로. `add_variant(paths, params, *, table)` 추가.
- `mri2mesh/web/app.py` — `POST /api/jobs/{job_id}/variants` + `DELETE /api/jobs/{job_id}` 라우트.
- `mri2mesh/web/static/api.js` — `createVariant(jobId, params)`, `deleteJob(jobId)` 추가.
- `mri2mesh/web/static/options.js` (신규) — baseline로 채운 옵션 폼·수집·POST.
- `mri2mesh/web/static/index.html` — 옵션 폼 패널 자리.
- `mri2mesh/web/static/app.js` — done 잡에서 폼 열기·생성 후 갱신, 사이드바 잡 삭제 배선.
- `tests/mesh/test_params.py`, `tests/jobs/test_pipeline.py`, `tests/web/test_app.py` — 테스트.

---

### Task 1: `baseline_params()`

**Files:**
- Modify: `mri2mesh/mesh/params.py` (파일 끝 `default_params` 아래)
- Test: `tests/mesh/test_params.py`

**Interfaces:**
- Consumes: 기존 `MeshParams`, `Preprocess`, `Extractor`, `Smoothing`, `Decimation`.
- Produces: `baseline_params() -> MeshParams` — 프로덕션 baseline. Task 2·3·4가 쓴다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/mesh/test_params.py`에 추가:

```python
from mri2mesh.mesh.params import baseline_params


def test_baseline_params_matches_production():
    p = baseline_params()
    assert p.preprocess.method == "none"
    assert p.extractor.name == "vtk_contour_perlabel"
    assert p.smoothing.method == "laplacian"
    assert p.smoothing.iterations == 30
    assert p.smoothing.relaxation == 0.1
    assert p.decimation.method == "none"
    assert p.min_voxel == 100
    # 파라미터 해시가 안정적이어야 변형 중복 판정이 일관된다
    assert p.variant_id(1) == f"v01-{p.param_hash()}"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/mesh/test_params.py::test_baseline_params_matches_production -v`
Expected: FAIL — `ImportError: cannot import name 'baseline_params'`

- [ ] **Step 3: 구현**

`mri2mesh/mesh/params.py` 끝에 추가:

```python
def baseline_params() -> MeshParams:
    """현재 프로덕션(brainds nifti_pipeline/mesh_export.py) 기본값.

    이진 마스크에 vtkContourFilter(0.5) → vtkSmoothPolyDataFilter(iter 30,
    relax 0.1). default_params()와 다르다 — 폼·엔드포인트·파이프라인 초기
    변형의 기준선은 이쪽이다.
    """
    return MeshParams(
        preprocess=Preprocess(method="none"),
        extractor=Extractor(name="vtk_contour_perlabel"),
        smoothing=Smoothing(method="laplacian", iterations=30, relaxation=0.1),
        decimation=Decimation(method="none"),
        min_voxel=100,
    )
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/mesh/test_params.py::test_baseline_params_matches_production -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/mesh/params.py tests/mesh/test_params.py
git commit -m "feat(mesh): baseline_params — brainds 프로덕션 기본값"
```

---

### Task 2: 파이프라인 초기 변형을 baseline로

**Files:**
- Modify: `mri2mesh/jobs/pipeline.py` (import + `run_segmentation_and_mesh` 내부 `params = default_params()`)
- Test: `tests/jobs/test_pipeline.py`

**Interfaces:**
- Consumes: `baseline_params()` (Task 1).
- Produces: 초기 v01의 params.json이 baseline을 반영. Task 4의 중복 판정 기준선.

- [ ] **Step 1: 실패 테스트 작성**

`tests/jobs/test_pipeline.py`에 추가. 이 파일엔 이미 `_t1_upload`,
`_fastsurfer_mock(subject_dir_root, sid)`, `job_paths`, `ingest_job`,
`run_segmentation_and_mesh`, `read_status`가 import·정의돼 있다(`test_full_run_produces_four_files`
가 done까지 태우는 패턴). 그걸 재사용해 done 헬퍼를 하나 추가한다:

```python
import json
from pathlib import Path
from mri2mesh.mesh.params import baseline_params


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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py::test_initial_variant_uses_baseline_params -v`
Expected: FAIL — extractor가 `skimage_mc`(default_params)라 assert 실패.

- [ ] **Step 3: 구현**

`mri2mesh/jobs/pipeline.py`:
- import 줄 수정: `from mri2mesh.mesh import GenerateError, default_params, generate_variant` → `from mri2mesh.mesh import GenerateError, baseline_params, generate_variant`
- `run_segmentation_and_mesh`의 `params = default_params()` → `params = baseline_params()`

`mri2mesh/mesh/__init__.py`가 `baseline_params`를 export하도록 추가한다(기존 `default_params` export 옆에).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py -v`
Expected: PASS (신규 + 기존 파이프라인 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/jobs/pipeline.py mri2mesh/mesh/__init__.py tests/jobs/test_pipeline.py
git commit -m "feat(pipeline): 초기 변형을 baseline_params로 생성"
```

---

### Task 3: `parse_mesh_params(payload)` — 요청 dict → MeshParams(검증)

**Files:**
- Modify: `mri2mesh/mesh/params.py`
- Test: `tests/mesh/test_params.py`

**Interfaces:**
- Consumes: `baseline_params()` (Task 1), `EXTRACTOR_NAMES` (from `mri2mesh.mesh.extract`).
- Produces: `parse_mesh_params(payload: dict) -> MeshParams`. 누락 축은 baseline로 채운다. 검증 위반 시 `ValueError`(입력값을 되쏘지 않는 짧은 메시지). Task 4·5가 쓴다.

- [ ] **Step 1: 실패 테스트 작성**

```python
import pytest
from mri2mesh.mesh.params import parse_mesh_params, baseline_params


def test_parse_fills_missing_axes_from_baseline():
    p = parse_mesh_params({"smoothing": {"method": "taubin", "iterations": 10,
                                         "passBand": 0.2, "featureAngle": 45}})
    # 준 축은 반영
    assert p.smoothing.method == "taubin"
    assert p.smoothing.iterations == 10
    # 안 준 축은 baseline
    assert p.extractor.name == baseline_params().extractor.name
    assert p.min_voxel == 100


def test_parse_rejects_unknown_method():
    with pytest.raises(ValueError):
        parse_mesh_params({"smoothing": {"method": "wobble"}})


def test_parse_rejects_unknown_extractor():
    with pytest.raises(ValueError):
        parse_mesh_params({"extractor": {"name": "not_a_real_extractor"}})


def test_parse_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_mesh_params({"minVoxel": 99999})
    with pytest.raises(ValueError):
        parse_mesh_params({"decimation": {"method": "quadric", "targetRatio": 5.0}})
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/mesh/test_params.py -k parse -v`
Expected: FAIL — `cannot import name 'parse_mesh_params'`

- [ ] **Step 3: 구현**

`mri2mesh/mesh/params.py`에 추가(파일 상단 import에 `from mri2mesh.mesh.extract import EXTRACTOR_NAMES` — 순환 import면 함수 안에서 지역 import):

```python
def _num(payload: dict, key: str, default, lo: float, hi: float):
    """camelCase 키에서 수치를 읽고 [lo, hi]로 검증. 없으면 default."""
    if key not in payload:
        return default
    v = payload[key]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError(f"{key}: 숫자가 아니다")
    if not (lo <= v <= hi):
        raise ValueError(f"{key}: 범위를 벗어났다")
    return v


def parse_mesh_params(payload: dict) -> MeshParams:
    """요청 dict(camelCase) → MeshParams. 누락 축은 baseline, 위반은 ValueError.

    메시지에 입력값을 그대로 넣지 않는다(PHI/로깅 안전).
    """
    from mri2mesh.mesh.extract import EXTRACTOR_NAMES

    base = baseline_params()
    payload = payload or {}

    # preprocess
    pre_in = payload.get("preprocess") or {}
    pre_method = pre_in.get("method", base.preprocess.method)
    if pre_method not in ("none", "gaussian", "distance"):
        raise ValueError("preprocess.method 화이트리스트 위반")
    pre = Preprocess(
        method=pre_method,
        sigma_vox=_num(pre_in, "sigmaVox", base.preprocess.sigma_vox, 0.1, 2.0),
    )

    # extractor
    ext_in = payload.get("extractor") or {}
    ext_name = ext_in.get("name", base.extractor.name)
    if ext_name not in EXTRACTOR_NAMES:
        raise ValueError("extractor.name 화이트리스트 위반")
    ext = Extractor(name=ext_name, options=())

    # smoothing
    smo_in = payload.get("smoothing") or {}
    smo_method = smo_in.get("method", base.smoothing.method)
    if smo_method not in ("none", "laplacian", "taubin", "humphrey"):
        raise ValueError("smoothing.method 화이트리스트 위반")
    smo = Smoothing(
        method=smo_method,
        iterations=int(_num(smo_in, "iterations", base.smoothing.iterations, 0, 100)),
        pass_band=_num(smo_in, "passBand", base.smoothing.pass_band, 0.0, 1.0),
        feature_angle=_num(smo_in, "featureAngle", base.smoothing.feature_angle, 0.0, 180.0),
        relaxation=_num(smo_in, "relaxation", base.smoothing.relaxation, 0.0, 1.0),
        alpha=_num(smo_in, "alpha", base.smoothing.alpha, 0.0, 1.0),
        beta=_num(smo_in, "beta", base.smoothing.beta, 0.0, 1.0),
    )

    # decimation
    dec_in = payload.get("decimation") or {}
    dec_method = dec_in.get("method", base.decimation.method)
    if dec_method not in ("none", "quadric"):
        raise ValueError("decimation.method 화이트리스트 위반")
    dec = Decimation(
        method=dec_method,
        target_ratio=_num(dec_in, "targetRatio", base.decimation.target_ratio, 0.05, 1.0),
    )

    return MeshParams(
        preprocess=pre, extractor=ext, smoothing=smo, decimation=dec,
        min_voxel=int(_num(payload, "minVoxel", base.min_voxel, 0, 5000)),
    )
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/mesh/test_params.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/mesh/params.py tests/mesh/test_params.py
git commit -m "feat(mesh): parse_mesh_params — 요청 파싱·검증(baseline 채움)"
```

---

### Task 4: `add_variant(paths, params)` — done 잡에 변형 추가(중복 제거)

**Files:**
- Modify: `mri2mesh/jobs/pipeline.py`
- Test: `tests/jobs/test_pipeline.py`

**Interfaces:**
- Consumes: `generate_variant`, `build_regions_meta`, `write_regions_meta`, `read_status`/`write_status`, `SEG_SOURCE_FILE`(전부 pipeline.py에 이미 import됨), `MeshParams`.
- Produces: `add_variant(paths: JobPaths, params: MeshParams, *, table=None) -> dict` → `{"variantId": str, "deduped": bool}`. Task 5가 쓴다.

동작:
- `read_status`. `state != "done"`이거나 `seg.nii.gz` 없으면 `ValueError`.
- 중복: `h = params.param_hash()`. 기존 `status.variants` 중 `variantId`가 `-{h}`로 끝나는 게 있으면 그 variantId 반환(`deduped=True`), 생성 안 함.
- 아니면 `index = len(status.variants) + 1`, `variant_id = params.variant_id(index)`, `generate_variant(seg_canon, vdir, params, index, table)` → `build_regions_meta` → `write_regions_meta`. `status.variants`에 `{variantId, bytes: variant.metrics["glbBytes"], createdAt: variant.params["createdAt"]}` append 후 `write_status`. `deduped=False`.

- [ ] **Step 1: 실패 테스트 작성**

```python
import pytest
from mri2mesh.jobs.pipeline import add_variant
from mri2mesh.mesh.params import baseline_params, parse_mesh_params


def test_add_variant_appends_new(tmp_path):
    p = _done_paths(tmp_path)               # Task 2에서 추가한 헬퍼
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
    with pytest.raises(ValueError):
        add_variant(p, baseline_params())
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py -k add_variant -v`
Expected: FAIL — `cannot import name 'add_variant'`

- [ ] **Step 3: 구현**

`mri2mesh/jobs/pipeline.py`에 추가(import는 이미 다 있다):

```python
def add_variant(paths: JobPaths, params, *, table=None) -> dict:
    """done 잡의 seg 캐시에서 메쉬만 다시 만들어 변형을 추가한다.

    같은 파라미터(해시 일치)면 생성하지 않고 기존 variantId를 돌려준다.
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
    variant = generate_variant(seg_canon, vdir, params, index=index, table=table)

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
    })
    write_status(paths, status)
    return {"variantId": variant.variant_id, "deduped": False}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/jobs/pipeline.py tests/jobs/test_pipeline.py
git commit -m "feat(pipeline): add_variant — done 잡에 변형 추가(해시 중복 제거)"
```

---

### Task 5: `POST /api/jobs/{job_id}/variants` 라우트

**Files:**
- Modify: `mri2mesh/web/app.py`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `parse_mesh_params`(Task 3), `add_variant`(Task 4), 기존 `_checked_job_paths`, `series_locks` 패턴.
- Produces: `POST /api/jobs/{job_id}/variants` — 본문 JSON(camelCase params) → `{"variantId": str, "deduped": bool}`. 잘못된 파라미터 400, done 아님 409, 잡 없음 404.

- [ ] **Step 1: 실패 테스트 작성**

`tests/web/test_app.py`에 추가(이 파일의 `_client`·`_nifti_bytes`·`_fastsurfer_mock`를 쓰고, `test_glb_and_meta_are_served_after_done`가 하는 방식으로 done까지 몬다):

```python
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


def test_post_variant_rejects_non_done(tmp_path):
    client, _ = _client(tmp_path)
    jid = client.post("/api/jobs", files={"files": ("input.nii.gz", _nifti_bytes())}).json()["jobId"]
    r = client.post(f"/api/jobs/{jid}/variants", json={})
    assert r.status_code == 409
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/web/test_app.py -k variant -v`
Expected: FAIL — 404/405(라우트 없음)로 assert 실패.

- [ ] **Step 3: 구현**

`mri2mesh/web/app.py`:
- import 추가: `from mri2mesh.mesh.params import parse_mesh_params`, `from mri2mesh.jobs.pipeline import add_variant`(기존 `ingest_job, run_segmentation_and_mesh` 옆).
- `glb` 라우트 앞에 추가:

```python
    @app.post("/api/jobs/{job_id}/variants")
    def create_variant(job_id: str, payload: dict) -> dict:
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        try:
            params = parse_mesh_params(payload)
        except ValueError:
            # 메시지에 입력값을 되쏘지 않는다(PHI/안전)
            raise HTTPException(400, "잘못된 메쉬 파라미터")
        # 같은 잡에서 동시 생성이 순번을 겹치지 않게 잡별 lock으로 묶는다
        lock = series_locks.setdefault(job_id, Lock())
        with lock:
            try:
                return add_variant(paths, params)
            except ValueError:
                raise HTTPException(409, "done 잡에서만 변형을 생성할 수 있다")
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: PASS (신규 + 기존 전부)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/web/app.py tests/web/test_app.py
git commit -m "feat(web): POST /variants — 옵션으로 변형 생성(검증·중복·done 게이트)"
```

---

### Task 6: 옵션 폼 UI (baseline로 채움, 생성 후 뷰어 갱신)

**Files:**
- Create: `mri2mesh/web/static/options.js`
- Modify: `mri2mesh/web/static/api.js`, `mri2mesh/web/static/index.html`, `mri2mesh/web/static/app.js`
- Test: `tests/web/test_app.py`(서빙된 HTML에 폼 요소 존재 확인), `tests/web/test_local_only.py`(외부참조 0 유지)

**Interfaces:**
- Consumes: `createVariant`(아래), `POST /variants`(Task 5), 기존 `Viewer.showVariant`.
- Produces: done 잡에서 옵션 폼을 열어 변형을 생성하고, 성공 시 그 변형으로 뷰어를 갱신한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/web/test_app.py`에 추가(서빙 HTML에 폼 앵커가 있는지 — 기존 served-HTML 확인 스타일):

```python
def test_index_html_has_option_form_anchors(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get("/").text
    assert 'id="mesh-options"' in html
    assert 'id="gen-variant"' in html
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/web/test_app.py::test_index_html_has_option_form_anchors -v`
Expected: FAIL — 앵커 없음.

- [ ] **Step 3: 구현**

`mri2mesh/web/static/api.js`에 export 추가:

```js
export async function createVariant(jobId, params) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/variants`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return j(res);   // 기존 j(): 비정상 응답이면 throw
}
```

`mri2mesh/web/static/index.html`의 `#vpanel` 안(metrics 아래)에 폼 앵커를 추가:

```html
    <div id="mesh-options">
      <div class="opt-row">
        <label>preprocess
          <select id="opt-preprocess"><option>none</option><option>gaussian</option><option>distance</option></select>
        </label>
      </div>
      <div class="opt-row">
        <label>extractor
          <select id="opt-extractor">
            <option>vtk_contour_perlabel</option><option>skimage_mc</option><option>pymcubes</option>
            <option>vtk_flyingedges</option><option>vtk_surfacenets</option>
          </select>
        </label>
      </div>
      <div class="opt-row">
        <label>smoothing
          <select id="opt-smoothing"><option>laplacian</option><option>none</option><option>taubin</option><option>humphrey</option></select>
        </label>
        <label>iter <input id="opt-iterations" type="number" value="30" min="0" max="100" style="width:56px"></label>
      </div>
      <div class="opt-row">
        <label>decimation
          <select id="opt-decimation"><option>none</option><option>quadric</option></select>
        </label>
        <label>ratio <input id="opt-ratio" type="number" value="0.35" min="0.05" max="1" step="0.05" style="width:56px"></label>
      </div>
      <div class="opt-row">
        <label>minVoxel <input id="opt-minvoxel" type="number" value="100" min="0" max="5000" style="width:64px"></label>
      </div>
      <button id="gen-variant" class="primary" style="margin-top:0">변형 생성</button>
      <div id="gen-status" class="sub"></div>
    </div>
```

`index.html` `<style>`에 최소 스타일 추가:

```css
  #mesh-options { margin-top: 10px; border-top: 1px solid #333; padding-top: 10px; }
  .opt-row { display: flex; gap: 8px; align-items: center; margin: 4px 0; font-size: 11px; }
  .opt-row select, .opt-row input { background: #111; color: #eee; border: 1px solid #333;
                                     border-radius: 5px; font-size: 11px; }
```

`mri2mesh/web/static/options.js`(신규):

```js
// 옵션 폼 → params(camelCase) 수집. baseline로 미리 채워져 있고(HTML value),
// 사용자가 바꾼 축만 반영된다. 관계없는 하위 수치는 서버가 baseline로 채운다.
export function collectParams() {
  const val = (id) => document.getElementById(id).value;
  const num = (id) => Number(val(id));
  return {
    preprocess: { method: val('opt-preprocess') },
    extractor:  { name: val('opt-extractor') },
    smoothing:  { method: val('opt-smoothing'), iterations: num('opt-iterations') },
    decimation: { method: val('opt-decimation'), targetRatio: num('opt-ratio') },
    minVoxel:   num('opt-minvoxel'),
  };
}
```

`mri2mesh/web/static/app.js`:
- 상단 import에 `createVariant`(api.js), `collectParams`(options.js) 추가.
- `#gen-variant` 버튼에 핸들러 배선(모듈 최상위, `selectedJob`가 이미 컨트롤러에 있다):

```js
document.getElementById('gen-variant').onclick = async () => {
  const btn = document.getElementById('gen-variant');
  const st = document.getElementById('gen-status');
  if (btn.disabled || !selectedJob) return;
  btn.disabled = true; st.textContent = '생성 중…';
  try {
    const { variantId } = await createVariant(selectedJob, collectParams());
    await refreshJobs();
    await viewer.showVariant(selectedJob, variantId);   // 새 변형으로 갱신
    st.textContent = variantId;
  } catch (err) {
    st.textContent = err.message || '생성 실패';
  } finally {
    btn.disabled = false;
  }
};
```

> 실행자 주의: `selectedJob`·`refreshJobs`·`viewer`는 app.js에 이미 있는 이름이다.
> 없으면 이 파일의 기존 컨트롤러에서 정확한 이름을 확인해 맞춰라(새로 만들지 말 것).

- [ ] **Step 4: 통과 확인 + 로컬전용 가드**

Run: `uv run pytest tests/web/test_app.py::test_index_html_has_option_form_anchors tests/web/test_local_only.py -v`
Expected: PASS (폼 앵커 존재 + 정적 에셋 외부참조 0)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/web/static/options.js mri2mesh/web/static/api.js mri2mesh/web/static/index.html mri2mesh/web/static/app.js tests/web/test_app.py
git commit -m "feat(web): 메쉬 옵션 폼 — baseline 채움·생성 후 뷰어 갱신"
```

---

### Task 7: 잡 삭제 (`DELETE /api/jobs/{job_id}` + 사이드바 버튼)

**Files:**
- Modify: `mri2mesh/web/app.py`, `mri2mesh/web/static/api.js`, `mri2mesh/web/static/app.js`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: 기존 `_checked_job_paths`(경로 조작 방지), `refreshJobs`·`selectedJob`(app.js).
- Produces: `DELETE /api/jobs/{job_id}` → `{"deleted": job_id}`. 잡 폴더 전체 삭제. 사이드바 각 잡 행에 삭제 버튼(확인 후).

- [ ] **Step 1: 실패 테스트 작성**

`tests/web/test_app.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/web/test_app.py -k delete -v`
Expected: FAIL — 405/404(라우트 없음).

- [ ] **Step 3: 구현**

`mri2mesh/web/app.py`:
- 상단에 `import shutil` 추가.
- `create_variant` 라우트 옆(또는 `status` 라우트 아래)에 추가:

```python
    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict:
        # _checked_job_paths가 job_id를 jobs_root 밑으로 가둔다(경로 조작 방지).
        paths = _checked_job_paths(config.jobs_root, job_id)
        if not paths.status_file.is_file():
            raise HTTPException(404, "job 없음")
        shutil.rmtree(paths.root)
        return {"deleted": job_id}
```

`mri2mesh/web/static/api.js`에 추가:

```js
export async function deleteJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
  return j(res);
}
```

`mri2mesh/web/static/app.js`:
- import에 `deleteJob` 추가.
- 사이드바 잡 행 렌더에 삭제 버튼을 붙인다(기존 joblist 렌더 함수 안, 잡 행 요소에). 버튼 클릭이 행 선택(selectJob)으로 번지지 않게 `stopPropagation`:

```js
// 잡 행 만들 때(기존 렌더 안):
const del = document.createElement('button');
del.className = 'job-del'; del.textContent = '×'; del.title = '삭제';
del.onclick = async (e) => {
  e.stopPropagation();
  if (!confirm('이 작업과 산출물을 삭제할까요?')) return;
  try {
    await deleteJob(job.jobId);
    if (selectedJob === job.jobId) { selectedJob = null; showStage('empty'); }
    await refreshJobs();
  } catch (err) { console.error('[deleteJob]', err); }
};
// 잡 행 요소(row/div)에 append
```

> 실행자 주의: joblist 렌더 함수·잡 행 변수·`selectedJob`·`showStage`·`refreshJobs`의
> 정확한 이름은 app.js에 이미 있다. 그 이름을 확인해 맞춰라(새로 만들지 말 것).
> 빈 상태로 되돌릴 때 쓰는 함수가 `showStage('empty')`가 아니면 이 파일의 실제
> 빈-상태 전환을 따르라.

`index.html` `<style>`에 삭제 버튼 최소 스타일:

```css
  .job-del { float: right; background: transparent; border: 0; color: #666; cursor: pointer;
             font-size: 14px; line-height: 1; padding: 0 2px; }
  .job-del:hover { color: #f87171; }
```

- [ ] **Step 4: 통과 확인 + 로컬전용 가드**

Run: `uv run pytest tests/web/test_app.py -k delete tests/web/test_local_only.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/web/app.py mri2mesh/web/static/api.js mri2mesh/web/static/app.js mri2mesh/web/static/index.html tests/web/test_app.py
git commit -m "feat(web): 잡 삭제 — DELETE /api/jobs/{id} + 사이드바 버튼"
```

---

## 검증(전체)

- [ ] `uv run pytest -q` — 전체 초록(기존 256 + 신규).
- [ ] 브라우저 수동: done 잡에서 옵션 바꿔 "변형 생성" → 몇 초 후 뷰어가 새 변형으로 갱신. 같은 baseline 재생성 시 새 변형이 안 생김(dedup). (JS 동작은 단위테스트 범위 밖 — 수동 확인.)

## 이 단계의 산출물

같은 잡에서 옵션을 바꿔 변형을 여러 개 만들 수 있다. baseline이 기준선. **나란히 비교(N-슬롯 뷰어)는 B단계.**
