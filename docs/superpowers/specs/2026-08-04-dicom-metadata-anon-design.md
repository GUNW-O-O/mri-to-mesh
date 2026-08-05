# DICOM 메타데이터 관리 + 익명화 감사 설계

> 2026-08-04. 업로드된 DICOM의 원본 메타데이터(before)와 익명화 후 상태(after)를
> 잡별 JSON으로 관리하고, 사이드바 잡 info 패널에서 확인한다. 익명화 기준은
> "brain-educate의 MPR·3D 렌더가 깨지지 않는 선까지 전부 제거".

## 1. 목적과 범위

DICOM 업로드 시 **무엇이 있었고(before) 익명화 후 무엇이 남았나(after)** 를
잡별로 기록·확인한다. 목적은 익명화 감사 — "이 식별자들이 있었고, 실제로 쓰는
데이터에서는 사라졌다"를 눈으로 확인.

**핵심 통찰:** DICOM→NIfTI 변환 자체가 익명화다. NIfTI에는 환자 태그 필드가
없고, 기하 정보는 affine으로 보존된다(브레인에듀케이트 MPR·3D가 필요한 건 이
기하뿐). dcm2niix는 BIDS 사이드카를 기본 익명화(`-ba y`)한다. 따라서 별도
익명화 DICOM 사본을 만들지 않는다 — 파이프라인이 이미 쓰는 NIfTI가 익명 산출물.
이 기능이 더하는 것은 **before/after 메타 기록 + 원본 파일명 기록 + 확인 UI**,
그리고 사이드카 익명화를 버전 기본값에 기대지 않고 명시(`-ba y`)하는 하드닝.

### 비목표
- 익명화된 DICOM 파일 사본 생성(불필요 — NIfTI가 익명 산출물).
- NIfTI 직접 입력의 DICOM 메타(존재하지 않음 — `source:"nifti"`로 표시).
- before 값의 git/외부 반출(로컬 jobs/ · localhost UI에만).

## 2. 전역 제약

- **PHI 금지선 = git/공유 산출물.** before 원본값·원본 파일명은 로컬
  `jobs/<id>/dicom-meta.json`과 localhost UI에만 존재. 저장소(코드·테스트·docs·
  커밋)에는 절대 실제 환자 데이터를 넣지 않는다 — 테스트 픽스처는 가짜
  메타(`Hong_Gil_Dong` 등)로 만든 DICOM을 쓴다.
- `status.json`의 `input.filename`은 계속 `<file>`로 서빙한다(스펙 §12) —
  원본 파일명은 별도 `dicom-meta.json`에만 두고, 전용 엔드포인트로만 나간다.
- `status.json.error` PHI 규칙 유지(파일명·경로·DICOM 태그 금지).
- 완전 로컬 · 외부 통신 0.

## 3. 데이터 계약

`jobs/<id>/dicom-meta.json` (로컬 · gitignore):

```json
{
  "source": "dicom",
  "originalFilenames": ["study/ser1/IM0001", "study/ser1/IM0002", "..."],
  "before": {
    "PatientName": "Hong^Gil^Dong",
    "PatientID": "12345",
    "StudyDate": "20240101",
    "InstitutionName": "…",
    "Modality": "MR",
    "PixelSpacing": [0.89, 0.89],
    "…": "…대표 인스턴스 헤더 전체(PixelData 제외)…"
  },
  "after": {
    "nifti": { "dims": [170, 288, 288], "voxelSizeMm": [1.2, 0.89, 0.89],
               "affine": [[…],[…],[…],[…]], "dtype": "int16" },
    "sidecar": { "…dcm2niix가 -ba y로 익명화한 BIDS json…" }
  },
  "removed": ["InstitutionName", "PatientBirthDate", "ReferringPhysicianName", "…"]
}
```

- `source`: `"dicom"` | `"nifti"`. `"nifti"`면 `before`=`null`, `after.nifti`만 채움.
- `originalFilenames`: 업로드 클라이언트가 준 이름/상대경로 원본(평탄 저장 전).
- `before`: 대표 DICOM 인스턴스의 헤더 전체(pydicom `to_json_dict`, PixelData 제외,
  키는 태그 keyword). 값은 그대로(option 2, 로컬 전용).
- `after.nifti`: 변환 결과 대표 NIfTI의 기하(dims·voxel·affine·dtype).
- `after.sidecar`: 그 NIfTI의 익명화된 BIDS 사이드카(없으면 `{}`).
- `removed`: `before` 키워드 중 `after.sidecar`에 없는 것(정렬). "무엇이 떨어졌나"
  감사 목록.

## 4. 아키텍처

### 4.1 `mri2mesh/io/dicom_meta.py` (신규)

- `read_dicom_header(path: Path) -> dict` — pydicom `dcmread(stop_before_pixels=True)`
  후 `to_json_dict`를 keyword 라벨 dict로 변환(PixelData·bulk 제외). 읽기 실패 시
  `DicomMetaError`.
- `build_meta(source, original_filenames, dicom_file, nifti_path, sidecar) -> dict`
  — §3 계약 dict를 만든다. `source=="nifti"`면 `before=None`, `removed=[]`.
  `removed`는 `sorted(set(before) - set(sidecar))`.
- `write_meta(path, meta)` / `read_meta(path)` — atomic write / read.

`DicomMetaError(RuntimeError)`.

### 4.2 `mri2mesh/jobs/layout.py`

`JobPaths.dicom_meta_file` 속성 추가 → `root / "dicom-meta.json"`.

### 4.3 `mri2mesh/io/dcm2niix.py`

`run_dcm2niix`의 cmd에 `"-ba", "y"` 명시 추가(사이드카 익명화를 버전 기본값에
의존하지 않는다). 나머지 동작 불변.

### 4.4 `mri2mesh/jobs/pipeline.py` — `ingest_job`

시그니처에 `original_filenames: list[str] | None = None` 추가(기존 호출 호환:
None이면 `[]`).

`prepare_input` 후, 시리즈 랭킹까지 끝나 `awaiting_series`로 쓰기 **전에**
dicom-meta를 만들어 쓴다:
- `prepared.kind == DICOM`: `dicom_file = prepared.dicom_files[0]`(대표),
  `before = read_dicom_header(dicom_file)`. 대표 NIfTI = 랭킹 1위(또는 첫)
  시리즈의 `nifti_path`·`sidecar_path`. `source="dicom"`.
- `prepared.kind == NIFTI`: `source="nifti"`, `before=None`, 대표 NIfTI =
  `prepared.nifti_file`(사이드카 없음).
- `build_meta(...)` → `write_meta(paths.dicom_meta_file, meta)`.

메타 생성 실패는 파이프라인을 죽이지 않는다 — `try/except DicomMetaError`로
감싸고 실패 시 `dicom-meta.json`을 쓰지 않고 진행(감사 부가기능이 본 파이프라인을
막으면 안 된다). io 단계 record_error 규칙은 그대로.

### 4.5 `mri2mesh/web/app.py`

- `upload`: `original_filenames = [f.filename or "" for f in files]` 수집 →
  `ingest_job(paths, src, "<file>", original_filenames=original_filenames)`.
  `status.json`에는 여전히 `<file>`만(원본 이름은 dicom-meta.json에만).
- 신규 라우트 `GET /api/jobs/{job_id}/dicom-meta` → `dicom-meta.json`을 그대로
  반환. 파일 없으면 404. (localhost 전용, 값 표시 OK — 로컬 도구.) 경로 확인은
  `_checked_job_paths`.

### 4.6 프론트 (info 패널)

- `api.js`: `getDicomMeta(jobId)` 추가(같은 스타일).
- `index.html`: 사이드바 잡 행에 `ⓘ` info 버튼 + 우측/하단 메타 패널 자리
  (`#dicom-info` 오버레이 또는 패널).
- `app.js`: info 버튼 클릭(행 선택과 분리, `stopPropagation`) → `getDicomMeta` →
  패널에 원본 파일명 + before(식별자/기술 구분) + after(nifti 기하 + 사이드카) +
  removed 목록 렌더. 값은 `esc()`로 이스케이프. `source:"nifti"`면 "NIfTI 입력 —
  DICOM 메타 없음"만.

## 5. 흐름 요약

```
업로드(파일명 수집) → prepare_input(매직바이트 분기)
  DICOM: 대표 헤더 → before / dcm2niix(-ba y) → 대표 NIfTI+사이드카 → after
  NIfTI: before=null / NIfTI 기하 → after
  → dicom-meta.json 기록 → awaiting_series
info 버튼 → GET /dicom-meta → 패널에 before/after/removed 표시
```

## 6. 파일 구조(예정)

- `mri2mesh/io/dicom_meta.py` (신규) — 헤더 읽기·메타 빌드·read/write.
- `mri2mesh/jobs/layout.py` — `dicom_meta_file` 속성.
- `mri2mesh/io/dcm2niix.py` — `-ba y` 명시.
- `mri2mesh/jobs/pipeline.py` — `ingest_job`에 메타 생성·`original_filenames`.
- `mri2mesh/web/app.py` — upload 파일명 수집 + `GET /dicom-meta`.
- `mri2mesh/web/static/{api.js,index.html,app.js}` — info 버튼·패널.
- 테스트: `tests/io/test_dicom_meta.py`, `tests/jobs/test_pipeline.py`,
  `tests/web/test_app.py` — 전부 가짜 DICOM(`Hong_Gil_Dong`)로.
