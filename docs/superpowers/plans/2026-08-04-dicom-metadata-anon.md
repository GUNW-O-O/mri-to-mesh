# DICOM 메타데이터 관리 + 익명화 감사 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DICOM 업로드의 원본 메타(before)와 익명화 후 상태(after)를 잡별 `dicom-meta.json`으로 기록하고 사이드바 info 패널에서 확인한다.

**Architecture:** DICOM→NIfTI 변환이 곧 익명화(NIfTI엔 환자 태그 없음, 기하는 affine). 신규 `io/dicom_meta.py`가 대표 DICOM 헤더(before)와 NIfTI 기하+익명 사이드카(after)로 메타 dict를 만든다. `ingest_job`이 랭킹 후 이를 써 둔다. dcm2niix는 `-ba y` 명시. 업로드가 원본 파일명을 수집해 넘기고, `GET /dicom-meta`로 서빙, 프론트 info 버튼이 표시.

**Tech Stack:** pydicom, nibabel, FastAPI, 바닐라 ES 모듈, pytest.

## Global Constraints

- PHI 금지선 = git/공유 산출물. before 값·원본 파일명은 로컬 `jobs/<id>/dicom-meta.json`·localhost UI에만. **테스트/코드/docs엔 실제 환자 데이터 금지** — 가짜 메타(`Hong_Gil_Dong`)로 만든 DICOM만 쓴다.
- `status.json`의 `input.filename`은 계속 `<file>`로 서빙(스펙 §12). 원본 이름은 `dicom-meta.json`에만, 전용 엔드포인트로만.
- 완전 로컬 · 외부 통신 0(`tests/web/test_local_only.py` 가드 유지).
- `dicom-meta.json` 계약(§3): `{source, originalFilenames, before, after:{nifti,sidecar}, removed}`. `source`=`"dicom"`|`"nifti"`; nifti면 `before=null`,`removed=[]`.
- `removed` = `sorted(set(before keyword) - set(after.sidecar key))`.

---

## File Structure

- `mri2mesh/io/dicom_meta.py` (신규) — `DicomMetaError`, `read_dicom_header`, `build_meta`, `write_meta`, `read_meta`.
- `mri2mesh/jobs/layout.py` — `JobPaths.dicom_meta_file`.
- `mri2mesh/io/dcm2niix.py` — cmd에 `-ba y`.
- `mri2mesh/jobs/pipeline.py` — `ingest_job` 메타 생성 + `original_filenames`.
- `mri2mesh/web/app.py` — upload 파일명 수집 + `GET /api/jobs/{id}/dicom-meta`.
- `mri2mesh/web/static/{api.js,index.html,app.js}` — info 버튼·패널.
- 테스트: `tests/io/test_dicom_meta.py`, `tests/jobs/test_pipeline.py`, `tests/web/test_app.py`.

---

### Task 1: `read_dicom_header` + `DicomMetaError`

**Files:**
- Create: `mri2mesh/io/dicom_meta.py`
- Test: `tests/io/test_dicom_meta.py`

**Interfaces:**
- Produces: `DicomMetaError(RuntimeError)`; `read_dicom_header(path) -> dict` — 대표 DICOM 헤더를 `{keyword: json-safe value}`로. PixelData·keyword 없는(private) element 제외. 읽기 실패 시 `DicomMetaError`.

- [ ] **Step 1: 실패 테스트 작성**

```python
from pathlib import Path
import pytest
from pydicom.dataset import Dataset
from mri2mesh.io.dicom_meta import read_dicom_header, DicomMetaError


def _fake_dicom(path: Path, **tags) -> Path:
    ds = Dataset()
    ds.PatientName = tags.get("PatientName", "Hong^Gil^Dong")
    ds.PatientID = tags.get("PatientID", "FAKE-001")
    ds.StudyDate = tags.get("StudyDate", "20240101")
    ds.Modality = tags.get("Modality", "MR")
    ds.PixelSpacing = tags.get("PixelSpacing", [0.89, 0.89])
    ds.PixelData = b"\x00\x00" * 8   # 제외돼야 함
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, implicit_vr=True, little_endian=True, enforce_file_format=False)
    return path


def test_read_header_keeps_tags_drops_pixeldata(tmp_path):
    p = _fake_dicom(tmp_path / "IM0001")
    h = read_dicom_header(p)
    assert h["PatientName"] == "Hong^Gil^Dong"
    assert h["PatientID"] == "FAKE-001"
    assert h["Modality"] == "MR"
    assert "PixelData" not in h
    # json 직렬화 가능해야 한다(값이 pydicom 타입으로 남으면 안 됨)
    import json; json.dumps(h)


def test_read_header_bad_file_raises(tmp_path):
    bad = tmp_path / "notdicom.bin"
    bad.write_bytes(b"not a dicom at all")
    with pytest.raises(DicomMetaError):
        read_dicom_header(bad)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/io/test_dicom_meta.py -v`
Expected: FAIL — `ModuleNotFoundError: mri2mesh.io.dicom_meta`

- [ ] **Step 3: 구현**

`mri2mesh/io/dicom_meta.py`:

```python
"""DICOM 메타데이터 before/after 관리 (익명화 감사).

before는 로컬 전용이다 — 원본 환자 식별자를 담으므로 git·HTTP status로 절대
내보내지 않는다(전용 dicom-meta 엔드포인트로만). 테스트는 가짜 메타로만.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pydicom


class DicomMetaError(RuntimeError):
    """DICOM 헤더를 읽지 못했다."""


def _json_safe(value):
    """pydicom element 값을 json 직렬화 가능한 형태로. 못 담을 값은 str."""
    from pydicom.multival import MultiValue
    from pydicom.valuerep import PersonName
    if isinstance(value, PersonName):
        return str(value)
    if isinstance(value, MultiValue):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)


def read_dicom_header(path) -> dict:
    """대표 DICOM 헤더를 {keyword: json-safe value}로. PixelData·private 제외.

    Raises:
        DicomMetaError: 파일을 DICOM으로 읽지 못했을 때.
    """
    try:
        ds = pydicom.dcmread(Path(path), stop_before_pixels=True, force=True)
    except Exception as exc:  # noqa: BLE001 — pydicom이 내는 예외 종류가 넓다
        raise DicomMetaError("DICOM 헤더를 읽지 못했다") from exc
    # force=True는 비-DICOM도 빈 Dataset으로 읽을 수 있다 — 식별 element가 하나도
    # 없으면 DICOM이 아니라고 본다.
    out: dict = {}
    for elem in ds:
        if elem.keyword in ("", "PixelData"):
            continue
        out[elem.keyword] = _json_safe(elem.value)
    if not out:
        raise DicomMetaError("DICOM 헤더에서 읽을 element가 없다")
    return out
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/io/test_dicom_meta.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/io/dicom_meta.py tests/io/test_dicom_meta.py
git commit -m "feat(io): read_dicom_header — DICOM 헤더를 json-safe dict로(PixelData 제외)"
```

---

### Task 2: `build_meta` + `write_meta`/`read_meta` + `JobPaths.dicom_meta_file`

**Files:**
- Modify: `mri2mesh/io/dicom_meta.py`, `mri2mesh/jobs/layout.py`
- Test: `tests/io/test_dicom_meta.py`

**Interfaces:**
- Consumes: `read_dicom_header` (Task 1), nibabel.
- Produces:
  - `JobPaths.dicom_meta_file -> Path` = `root / "dicom-meta.json"`.
  - `build_meta(source, original_filenames, dicom_file, nifti_path, sidecar) -> dict` — §3 계약. `source=="nifti"`면 `before=None`,`removed=[]`,`dicom_file`은 무시. `after.nifti`={dims,voxelSizeMm,affine,dtype}, `after.sidecar`=sidecar dict(없으면 `{}`). `removed`=`sorted(set(before)-set(sidecar))`.
  - `write_meta(path, meta)` (원자적) / `read_meta(path) -> dict`.

- [ ] **Step 1: 실패 테스트 작성**

```python
import numpy as np
import nibabel as nib
from mri2mesh.io.dicom_meta import build_meta, write_meta, read_meta


def _fake_nifti(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.zeros((4, 5, 6), np.int16), np.eye(4)), path)
    return path


def test_build_meta_dicom(tmp_path):
    dcm = _fake_dicom(tmp_path / "IM0001")          # Task 1 헬퍼 재사용
    nii = _fake_nifti(tmp_path / "s.nii.gz")
    meta = build_meta(
        source="dicom", original_filenames=["A/IM0001"],
        dicom_file=dcm, nifti_path=nii,
        sidecar={"Modality": "MR", "SeriesDescription": "T1"},
    )
    assert meta["source"] == "dicom"
    assert meta["originalFilenames"] == ["A/IM0001"]
    assert meta["before"]["PatientName"] == "Hong^Gil^Dong"
    assert meta["after"]["nifti"]["dims"] == [4, 5, 6]
    assert meta["after"]["sidecar"]["Modality"] == "MR"
    # PatientName은 사이드카에 없으니 removed에 들어간다
    assert "PatientName" in meta["removed"]
    assert "Modality" not in meta["removed"]   # before·sidecar 양쪽에 있음


def test_build_meta_nifti_has_no_before(tmp_path):
    nii = _fake_nifti(tmp_path / "s.nii.gz")
    meta = build_meta(source="nifti", original_filenames=["scan.nii.gz"],
                      dicom_file=None, nifti_path=nii, sidecar=None)
    assert meta["source"] == "nifti"
    assert meta["before"] is None
    assert meta["removed"] == []
    assert meta["after"]["sidecar"] == {}


def test_write_read_roundtrip(tmp_path):
    meta = {"source": "nifti", "before": None, "after": {}, "removed": [],
            "originalFilenames": []}
    p = tmp_path / "dicom-meta.json"
    write_meta(p, meta)
    assert read_meta(p) == meta
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/io/test_dicom_meta.py -k "build_meta or roundtrip" -v`
Expected: FAIL — `cannot import name 'build_meta'`

- [ ] **Step 3: 구현**

`mri2mesh/io/dicom_meta.py`에 추가:

```python
def _nifti_geom(nifti_path) -> dict:
    import nibabel as nib
    img = nib.load(Path(nifti_path))
    zooms = [float(z) for z in img.header.get_zooms()[:3]]
    return {
        "dims": [int(d) for d in img.shape[:3]],
        "voxelSizeMm": zooms,
        "affine": [[float(x) for x in row] for row in img.affine],
        "dtype": str(img.header.get_data_dtype()),
    }


def build_meta(source, original_filenames, dicom_file, nifti_path, sidecar) -> dict:
    """§3 dicom-meta 계약 dict를 만든다. source: 'dicom' | 'nifti'."""
    sidecar = sidecar or {}
    before = read_dicom_header(dicom_file) if source == "dicom" else None
    removed = sorted(set(before) - set(sidecar)) if before is not None else []
    return {
        "source": source,
        "originalFilenames": list(original_filenames or []),
        "before": before,
        "after": {"nifti": _nifti_geom(nifti_path), "sidecar": dict(sidecar)},
        "removed": removed,
    }


def write_meta(path, meta) -> None:
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_meta(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
```

`mri2mesh/jobs/layout.py`의 `JobPaths`에 속성 추가(다른 `@property` 옆):

```python
    @property
    def dicom_meta_file(self) -> Path:
        return self.root / "dicom-meta.json"
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/io/test_dicom_meta.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/io/dicom_meta.py mri2mesh/jobs/layout.py tests/io/test_dicom_meta.py
git commit -m "feat(io): build_meta/write/read + JobPaths.dicom_meta_file"
```

---

### Task 3: dcm2niix `-ba y` 명시

**Files:**
- Modify: `mri2mesh/io/dcm2niix.py`
- Test: `tests/io/test_dcm2niix.py`

**Interfaces:**
- Produces: `run_dcm2niix`가 만드는 cmd에 `-ba y`(사이드카 익명화)가 항상 들어간다. cmd 조립을 `_dcm2niix_cmd(exe, depth, out_dir, dicom_dir) -> list[str]` 헬퍼로 빼서 바이너리 없이도 테스트한다.

- [ ] **Step 1: 실패 테스트 작성**

```python
from mri2mesh.io.dcm2niix import _dcm2niix_cmd


def test_cmd_includes_anonymize_bids():
    cmd = _dcm2niix_cmd("dcm2niix", 5, "/out", "/in")
    # -ba y 가 인접 쌍으로 있어야 한다
    assert "-ba" in cmd
    assert cmd[cmd.index("-ba") + 1] == "y"
    # 기존 플래그도 유지
    assert "-z" in cmd and "-b" in cmd
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/io/test_dcm2niix.py::test_cmd_includes_anonymize_bids -v`
Expected: FAIL — `cannot import name '_dcm2niix_cmd'`

- [ ] **Step 3: 구현**

`mri2mesh/io/dcm2niix.py`의 `run_dcm2niix` 안 cmd 조립을 헬퍼로 추출하고 `-ba y`를 넣는다:

```python
def _dcm2niix_cmd(exe: str, depth: int, out_dir, dicom_dir) -> list[str]:
    return [
        exe,
        "-d", str(depth),
        "-z", "y",
        "-b", "y",
        "-ba", "y",   # BIDS 사이드카 익명화 — 버전 기본값에 의존하지 않는다
        "-f", _FILENAME_PATTERN,
        "-o", str(out_dir),
        str(dicom_dir),
    ]
```

그리고 `run_dcm2niix` 안의 기존 `cmd = [ ... ]` 리터럴을 `cmd = _dcm2niix_cmd(exe, depth, out_dir, dicom_dir)`로 교체.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/io/test_dcm2niix.py -v`
Expected: PASS (신규 + 기존)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/io/dcm2niix.py tests/io/test_dcm2niix.py
git commit -m "feat(io): dcm2niix -ba y 명시 + cmd 헬퍼 추출"
```

---

### Task 4: `ingest_job` 메타 생성 + `original_filenames`

**Files:**
- Modify: `mri2mesh/jobs/pipeline.py`
- Test: `tests/jobs/test_pipeline.py`

**Interfaces:**
- Consumes: `build_meta`, `write_meta`, `DicomMetaError` (dicom_meta), `JobPaths.dicom_meta_file`, `SourceKind`(io), `PreparedInput`(`.kind`,`.dicom_files`,`.nifti_file`), `SeriesCandidate.series`(`.nifti_path`,`.sidecar_path`).
- Produces: `ingest_job(paths, src, filename, *, dcm2niix_runner=None, original_filenames=None)` — 랭킹 후 `awaiting_series`로 쓰기 전 `dicom-meta.json`을 만든다. 실패는 파이프라인을 죽이지 않는다.

- [ ] **Step 1: 실패 테스트 작성**

```python
import json
from mri2mesh.io.dicom_meta import read_meta


def test_ingest_nifti_writes_meta_without_before(tmp_path):
    p = job_paths(tmp_path / "jobs", "jobM").create()
    ingest_job(p, _t1_upload(tmp_path), "input.nii.gz",
               original_filenames=["scan.nii.gz"])
    meta = read_meta(p.dicom_meta_file)
    assert meta["source"] == "nifti"
    assert meta["before"] is None
    assert meta["originalFilenames"] == ["scan.nii.gz"]
    assert meta["after"]["nifti"]["dims"][0] > 0
```

> 실행자 주의: DICOM 경로(before 있는) 테스트는 dcm2niix 바이너리가 필요해
> 호스트에서 skip된다. 이 태스크의 테스트는 **NIfTI 직접 입력**(바이너리 불필요)
> 으로 메타 생성 경로를 검증한다. DICOM 분기의 실제 실행 검증은 컨테이너/실데이터
> 테스트(기존 `realdata` 마커)에 맡긴다 — 여기서 가짜 DICOM+dcm2niix 목까지
> 만들지 말 것(과설계).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py::test_ingest_nifti_writes_meta_without_before -v`
Expected: FAIL — `ingest_job() got an unexpected keyword argument 'original_filenames'`

- [ ] **Step 3: 구현**

`mri2mesh/jobs/pipeline.py`:
- import 추가: `from mri2mesh.io import SourceKind`(이미 io에서 다른 것들 import 중 — 같은 줄에 추가), `from mri2mesh.io.dicom_meta import build_meta, write_meta, DicomMetaError`.
- `ingest_job` 시그니처: `def ingest_job(paths, src, filename, *, dcm2niix_runner=None, original_filenames=None):`
- `prepared = prepare_input(...)` 결과와 `ranked`를 얻은 뒤(기존 `ranked = rank_series(series)` 다음), `awaiting_series` 상태를 쓰기 전에 메타를 만든다:

```python
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
    except (DicomMetaError, OSError, IndexError):
        pass  # 감사 메타 없이 진행
```

`_load_sidecar_dict`는 pipeline.py에 작은 헬퍼로 추가(사이드카 JSON을 dict로, 없으면 `{}`):

```python
def _load_sidecar_dict(sidecar_path) -> dict:
    if sidecar_path is None:
        return {}
    try:
        import json
        return json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/jobs/test_pipeline.py -v`
Expected: PASS (신규 + 기존 전부)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/jobs/pipeline.py tests/jobs/test_pipeline.py
git commit -m "feat(pipeline): ingest_job이 dicom-meta 생성 + original_filenames"
```

---

### Task 5: upload 파일명 수집 + `GET /api/jobs/{id}/dicom-meta`

**Files:**
- Modify: `mri2mesh/web/app.py`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `ingest_job(..., original_filenames=...)`(Task 4), `_checked_job_paths`, `JobPaths.dicom_meta_file`.
- Produces: `upload`이 `[f.filename or "" for f in files]`를 수집해 `ingest_job`에 넘긴다. `GET /api/jobs/{job_id}/dicom-meta` → `dicom-meta.json` 반환(없으면 404).

- [ ] **Step 1: 실패 테스트 작성**

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/web/test_app.py -k dicom_meta -v`
Expected: FAIL — 404/405로 assert 실패(첫 테스트).

- [ ] **Step 3: 구현**

`mri2mesh/web/app.py`:
- `upload` 안 파일 저장 루프에서 원본 이름 수집:

```python
            orig_names = [f.filename or "" for f in files]
            saved = []
            for i, f in enumerate(files, start=1):
                dst = paths.input_dir / f"{i:04d}"
                dst.write_bytes(await f.read())
                saved.append(dst)
            src = saved[0] if len(saved) == 1 else paths.input_dir
            ingest_job(paths, src, "<file>", original_filenames=orig_names)
```

- `meta`(regions-meta) 라우트 근처에 추가:

```python
    @app.get("/api/jobs/{job_id}/dicom-meta")
    def dicom_meta(job_id: str) -> FileResponse:
        paths = _checked_job_paths(config.jobs_root, job_id)
        p = paths.dicom_meta_file
        if not p.is_file():
            raise HTTPException(404, "dicom-meta 없음")
        return FileResponse(p, media_type="application/json")
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: PASS (신규 + 기존 전부)

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/web/app.py tests/web/test_app.py
git commit -m "feat(web): upload 원본 파일명 수집 + GET /dicom-meta"
```

---

### Task 6: 프론트 info 패널

**Files:**
- Create: (없음 — 기존 파일 수정)
- Modify: `mri2mesh/web/static/api.js`, `mri2mesh/web/static/index.html`, `mri2mesh/web/static/app.js`
- Test: `tests/web/test_app.py`(서빙 HTML 앵커), `tests/web/test_local_only.py`(외부참조 0)

**Interfaces:**
- Consumes: `getDicomMeta(jobId)`(아래), `GET /dicom-meta`(Task 5).
- Produces: 사이드바 각 잡의 `ⓘ` 버튼 → 오버레이 패널에 원본 파일명 + before/after/removed 표시.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_index_html_has_dicom_info_anchors(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get("/").text
    assert 'id="dicom-info"' in html
    assert 'class="job-info"' in html or 'job-info' in html
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/web/test_app.py::test_index_html_has_dicom_info_anchors -v`
Expected: FAIL — 앵커 없음.

- [ ] **Step 3: 구현**

`mri2mesh/web/static/api.js`에 추가:

```js
export async function getDicomMeta(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/dicom-meta`);
  return j(res);
}
```

`mri2mesh/web/static/index.html`:
- `<body>` 안(모달 오버레이 근처)에 info 오버레이 추가:

```html
<div id="dicom-info-overlay" style="display:none">
  <div id="dicom-info"></div>
</div>
```
- `<style>`에 최소 스타일:

```css
  #dicom-info-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6);
                        display: none; place-items: center; z-index: 20; }
  #dicom-info-overlay.on { display: grid; }
  #dicom-info { background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
                width: 620px; max-width: 92%; max-height: 82vh; overflow: auto;
                padding: 20px; font-size: 12px; }
  #dicom-info h3 { margin: 0 0 10px; font-size: 15px; }
  #dicom-info .kv { font-family: monospace; font-size: 11px; white-space: pre-wrap;
                    color: #bcd; }
  #dicom-info .removed { color: #f7a; }
  .job-info { float: right; background: transparent; border: 0; color: #789;
              cursor: pointer; font-size: 12px; padding: 0 4px; }
  .job-info:hover { color: #38bdf8; }
```

`mri2mesh/web/static/app.js`:
- import에 `getDicomMeta` 추가.
- 잡 행 렌더(`refreshJobs`)에 `ⓘ` 버튼 추가(삭제 `×` 버튼과 같은 방식, `stopPropagation`):

```js
const info = document.createElement('button');
info.className = 'job-info'; info.textContent = 'ⓘ'; info.title = '메타 정보';
info.onclick = async (e) => {
  e.stopPropagation();
  try { showDicomInfo(await getDicomMeta(r.jobId)); }
  catch (err) { console.error('[dicomMeta]', err); }
};
// 잡 행 요소에 append(× 버튼과 나란히)
```

- 모듈 최상위에 렌더 함수 + 오버레이 닫기:

```js
const dicomOverlay = document.getElementById('dicom-info-overlay');
dicomOverlay.onclick = (e) => { if (e.target === dicomOverlay) dicomOverlay.classList.remove('on'); };
function showDicomInfo(m) {
  const box = document.getElementById('dicom-info');
  const names = (m.originalFilenames || []).map(esc).join('\n') || '(없음)';
  let html = `<h3>DICOM 메타 · ${esc(m.source)}</h3>`;
  html += `<div class="sub">원본 파일명</div><div class="kv">${names}</div>`;
  if (m.source === 'nifti' || !m.before) {
    html += `<div class="sub" style="margin-top:10px">NIfTI 입력 — DICOM 메타 없음</div>`;
  } else {
    html += `<div class="sub" style="margin-top:10px">before (원본 헤더)</div>`;
    html += `<div class="kv">${esc(JSON.stringify(m.before, null, 1))}</div>`;
    html += `<div class="sub" style="margin-top:10px">removed (제거됨)</div>`;
    html += `<div class="kv removed">${(m.removed||[]).map(esc).join('\n')}</div>`;
  }
  html += `<div class="sub" style="margin-top:10px">after (NIfTI 기하)</div>`;
  html += `<div class="kv">${esc(JSON.stringify(m.after && m.after.nifti, null, 1))}</div>`;
  box.innerHTML = html;
  dicomOverlay.classList.add('on');
}
```

> 실행자 주의: `refreshJobs`의 잡 행 변수는 `r`(=`for (const r of rows)`), 삭제
> 버튼(`.job-del`)이 이미 같은 자리에 붙는다 — 그 옆에 `ⓘ`를 붙여라. `esc`는
> app.js에 이미 있다. 값은 전부 `esc`로 이스케이프(before에 임의 문자열이 온다).

- [ ] **Step 4: 통과 확인 + 로컬전용 가드**

Run: `uv run pytest tests/web/test_app.py::test_index_html_has_dicom_info_anchors tests/web/test_local_only.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add mri2mesh/web/static/api.js mri2mesh/web/static/index.html mri2mesh/web/static/app.js tests/web/test_app.py
git commit -m "feat(web): 사이드바 ⓘ info 버튼 — DICOM before/after/removed 패널"
```

---

## 검증(전체)

- [ ] `uv run pytest -q` — 전체 초록(기존 276 + 신규).
- [ ] 브라우저 수동: DICOM 폴더 업로드 후 잡 ⓘ → before(원본 태그)·removed·after(기하) 표시. NIfTI 업로드 시 "DICOM 메타 없음". 원본 파일명 표시.

## 이 기능의 산출물

DICOM 업로드의 원본/익명화 메타를 로컬에서 감사할 수 있다. before 값·원본 파일명은 로컬 전용, git·status 서빙엔 안 나간다.
