# 워크벤치: 시리즈 선택 + 변형/잡 비교 설계

**날짜:** 2026-08-03
**상태:** 설계 확정 (구현 계획 대기)
**선행:** `feat/web`(머지됨) — 업로드→게이트→백그라운드 파이프라인→단일 뷰어 뼈대.

## 목표

브라우저 하나로 MRI 업로드부터 메시 뷰어까지 완주하고, 산출된 메시들을 나란히
비교한다. 지금은 업로드 뒤 시리즈를 `curl`로 골라야 하고(선택 UI 없음), 뷰어는
job/variant id를 손으로 쳐 결과 하나만 본다. 그 빠진 다리(시리즈 선택 UI)를 채우고,
그 위에 변형/잡 비교를 얹는다.

## 아키텍처

FastAPI가 정적 `index.html`을 한 번 뱉고(**SSR 없음**), 이후 상태 변화는 전부
클라이언트 ES 모듈이 DOM을 조작해 처리한다. three.js 캔버스는 세션 내내 한 번
만들어 유지하는 고정 레이어이고, 사이드바·모달·스테이지는 그 위에 뜨는 오버레이다.
서버는 잡별 `status.json`을 단일 진실원으로 두고, 브라우저는 그것을 폴링한다.
비교는 same-origin GLB 여러 개를 한 three.js 씬의 슬롯에 나란히(좌/우) 로드하는
순수 클라이언트 기능이다.

## 기술 스택

Python/FastAPI, 순수 ES 모듈(프레임워크·번들러 없음), vendored three.js
r0.160.0, dcm2niix, FastSurfer(형제 컨테이너), pyvista/trimesh/PyMCubes 메시.

## 전역 제약 (모든 태스크가 암묵적으로 포함)

- **완전 로컬 · 외부 통신 0.** 런타임에 로컬호스트 밖과 통신하지 않는다. 프론트
  에셋에 CDN·외부 폰트·외부 이미지·analytics 금지. importmap은 `/static/vendor`만
  가리킨다. `api.js`의 모든 fetch는 same-origin 상대경로. **검증 테스트**가 static
  트리를 grep해 `http://`·`https://`·`//`(스킴상대)·`unpkg`·`esm.sh`·`jsdelivr`·
  `googleapis`·`cdn` 외부 참조가 있으면 실패한다.
- **PHI (스펙 §12).** 브라우저로 나가는 어떤 JSON에도 원본 파일명·경로·DICOM
  식별자를 담지 않는다. 디스크 `status.json`은 재현용으로 `niftiPath`를 유지하되
  **서빙 시에만** 벗긴다(현행 filename 마스킹과 동일 규율).
- **시리즈 자동선택 금지 (스펙 §6.3).** `ingest_job`은 랭킹까지만 하고
  `awaiting_series`에서 멈춘다. 사용자가 고른 뒤에만 세그가 시작된다.
- **`--no_cc` 금지.** FastSurfer 호출에 절대 넘기지 않는다.
- **정준 색은 전역.** 영역 색은 `labels/canonical-v1.tsv`가 라벨 id별로 정의한다.
  같은 해부 영역은 모든 잡·모든 변형에서 같은 색이다. 런타임은 FreeSurfer/FastSurfer
  LUT를 읽지 않는다(canonical-v1.tsv만).
- **산출 4파일 유지.** 이 툴은 다른 소프트웨어(brain-educate)의 에셋 산출용이다.
  MSA·상시 백엔드가 아니다. 과설계 금지.
- **저장소:** 리포에 `.env.example`만, `.env` 금지. test-asset/ 실데이터 절대 커밋 금지.

## 단계 (한 설계, 순차 구현)

- **P1 — 워크벤치 뼈대 + 실사용 흐름.** 사이드바 + 잡목록 API + 업로드 모달(이름
  지정, 폴더 드롭) + 시리즈 선택 게이트 + 진행률 + 단일 뷰어. niftiPath index 계약.
  *완료 시 브라우저로 업로드→선택→뷰어 완주 가능.*
- **P2 — 변형 생성 · 품질 비교.** 완료 잡에서 파라미터 조정 → 메시-only 재생성.
  한 캔버스 좌/우 나란히(공유 스케일·공유 카메라), 캡션=파라미터+정점수, 와이어프레임 토글.
- **P3 — 잡 간 / 레퍼런스 비교.** 사이드바에서 다른 잡·레퍼런스 GLB를 골라 나란히.
  크기(월등히 크/작지 않은지) · 형태(일반인이 질병 차이를 알아볼 좋은 메시인지) 검수.

각 단계가 독립적으로 굴러가고 테스트된다. P1이 GLB를 만들어야 P2/P3가 비교할 게 생긴다.

---

## 백엔드

디스크 `status.json`은 `niftiPath`를 유지(파이프라인이 씀). **서빙 시에만** 벗긴다.

### 서빙 JSON 계약

`GET /api/jobs` (신규) — 사이드바 목록:
```json
[{"jobId": "...", "name": "...", "state": "...", "step": "...",
  "createdAt": "...", "variantCount": 1}]
```
PHI 없음. createdAt 역순 정렬. `jobs_root` 순회, 각 `status.json` 읽음.

`GET /api/jobs/{id}` (서빙 status) — 변경:
- `series[]` → `{index, description, slices, voxelSizeMm, acquisitionType, score, reasons}`.
  **niftiPath 제거**, index = 배열 위치.
- `selectedSeries` → `{index, description, slices, voxelSizeMm}` (경로 없음, 스펙 §9.1).
- `variants[]` → `{variantId, bytes, createdAt, paramLabel}`.
- `pendingVariant` → `{state: "building"|"error", paramLabel, error?}` (재생성 중/실패).
  없으면 필드 부재.
- `input.filename` → 여전히 `"<file>"`.

`POST /api/jobs` (multipart) — `files: list[UploadFile]`, `name: str = Form(None)` → `{jobId}`.

`POST /api/jobs/{id}/series` — `{seriesIndex: int}` → `{state: "running"}`.
범위 밖 index → 400. 서버가 자기 신뢰 데이터 `status.series[index].niftiPath`로 매핑.

`POST /api/jobs/{id}/variants` (P2) — `{preprocess?, extract?, postprocess?, minVoxel?}`
(화이트리스트+범위 검증) → `{variantId}`. 백그라운드 메시-only 재생성.

GLB/meta 엔드포인트 불변. 잡 간 비교는 프론트가 기존
`GET /api/jobs/{otherId}/variants/{vid}/regions.glb`를 다른 jobId로 호출 — 백엔드 신규 없음.
레퍼런스 GLB = 지정된 잡 하나로 취급(별도 레지스트리 없음, YAGNI).

### 업로드 견고성 (NAS 긴 경로)

NAS에서 드래그한 DICOM 폴더는 상대경로가 길다(윈도우 260자 MAX_PATH·이름 충돌·PHI).
`prepare_input`은 폴더를 매직바이트로 검사해 DICOM만 추리고 파일명은 안 본다
(순서는 dcm2niix가 DICOM 태그로 잡음). 따라서:

- `POST /api/jobs`가 다중 파일을 받는다.
- 들어오는 각 파일을 `input_dir`에 **`0001, 0002, …` 번호로 평탄 저장**. 클라가 준
  상대경로·원본 파일명은 통째 버린다 → 긴 경로/충돌/파일명 PHI/MAX_PATH 전부 해소.
- 분기: 파일 1개 + zip/nifti → `prepare_input(file)`(기존). 그 외(DICOM 폴더) →
  `prepare_input(input_dir)` 디렉터리 모드(`_prepare_dir`, 이미 구현됨).
- `.env.example`에 짧은 `OUTPUT_DIR` 권고 주석(루트 자체가 깊으면 여전히 한계).

### 서버 파일

- `web/app.py` — 라우트 신규(list·variants), 업로드 다중+이름+평탄화, series index 계약,
  `_status_json`서 niftiPath 벗기고 series/selectedSeries 재모양.
- `jobs/pipeline.py` — `ingest_job`(index=배열위치), 신규
  `regenerate_variant(paths, params, index, table)` 메시-only(seg.nii.gz 캐시 로드→
  `generate_variant`→meta→append). FastSurfer 안 탐.
- `jobs/status.py` — variants에 paramLabel, selectedSeries 모양, pendingVariant.
- `mesh/params.py` — dict 오버라이드→`MeshParams` 파싱+검증, `paramLabel` 문자열.

### 에러 처리

- 업로드 무효(DICOM/nifti 없음) → `record_error` → state=error, 서빙 마스킹 → 프론트 에러 패널.
- **변형 재생성 실패 시 done 잡을 error로 뒤엎지 않는다** — `pendingVariant.state="error"`
  (마스킹 메시지)만 세팅, `job.state`는 done 유지. 성공 시 variants append + pendingVariant 클리어.
- 백그라운드 catch-all(현행) 유지 — 예상 못 한 예외도 잡을 running에 남기지 않는다.

---

## 프론트엔드

프레임워크·번들러 없음. 순수 ES 모듈, same-origin fetch만, vendored three.js.

```
web/static/
  index.html    — 셸 마크업(사이드바·메인 스테이지·모달·캔버스) + importmap
  api.js        — 엔드포인트 fetch 래퍼 (네트워크 계층)
  app.js        — 컨트롤러: 상태머신·폴링·사이드바 렌더·모달·스테이지 전환
  viewer.js     — three.js: 씬·변형 로드·색·group/side 토글·나란히 슬롯·공유 카메라
```

- **api.js**: `listJobs()`, `upload(files, name)`, `getStatus(id)`, `selectSeries(id, index)`,
  `addVariant(id, params)`, `glbUrl/metaUrl(id, vid)`. 다른 파일은 URL 직접 안 만짐.
- **app.js**: 선택 잡 `status.state`로 메인 스테이지 스위칭 —
  미선택→안내, `awaiting_series`→시리즈 패널, `running`→진행률(5단계), `done`→뷰어,
  `error`→단계+마스킹 사유 패널.
- **viewer.js**: 슬롯 리스트. 슬롯 = `{jobId, variantId, group, caption}`. X축 오프셋,
  공유 world 스케일, OrbitControls 하나가 전부 회전.

### 폴링

SSE·WebSocket 안 씀(로컬 단독, 과함). 선택 잡이 `running`인 동안 `getStatus` ~1.5s.
`awaiting_series`/`done`/`error`는 정적, 폴링 중단. 사이드바 목록은 뭔가 `running`인
동안 `listJobs` ~2s + 액션 후 즉시 1회.

### 캔버스 안정성 (SSR 없음)

폴링은 `fetch`로 JSON만 받아 **오버레이 DOM만** 바꾼다. 페이지 리로드·서버 재렌더
없음. 캔버스는 항상 마운트된 고정 레이어(`position: fixed; inset: 0`)이고, 씬은
명시적 `viewer.load()`(사용자 액션/done 전환) 때만 바뀐다. `requestAnimationFrame`
루프는 독립·연속, `fetch`가 안 막음 → 끊김·WebGL 컨텍스트 손실 없음.

### 모달 (업로드)

폴더 드롭 지원: `<input webkitdirectory>` + DataTransfer 디렉터리 순회로 폴더 내 전
파일 수집. zip·단일 nifti도 같은 드롭존. 이름 입력칸. → `api.upload(files, name)`.

---

## 비교 렌더링 규칙 (P2/P3)

한 three.js 씬, 슬롯 N개 가로 배치.

1. **공유 스케일 (최상위 철칙).** 모든 슬롯 메시를 true world mm 그대로. 메시별
   정규화·슬롯맞춤 리스케일 **금지** — 큰 뇌는 크게 나와야 "월등히 크냐"가 성립.
   각 메시는 자기 bbox 중심을 자기 슬롯 위치로 **평행이동만**(회전·스케일 불변).
2. **슬롯 배치.** 로드된 슬롯 중 최대 bbox 폭으로 간격(pitch) 통일 → 겹침 없음,
   등간격. 슬롯 추가/제거 시 재계산. 슬롯 상한 **4**(가독성·성능).
3. **공유 카메라.** OrbitControls 하나가 전체 행 중심 공전 → 모든 슬롯 같은 각도.
   카메라는 전체 bbox에 프레이밍.
4. **전역 색·토글 일괄.** 정준 색이 전 슬롯 동일. group/side/반투명 토글은 모든
   슬롯 동시 적용(한 구조 끄면 전부 꺼짐).
5. **캡션 (HTML 오버레이, 슬롯 중심 화면투영 추적).**
   - P2 품질: 파라미터 요약 + 정점/삼각형 수 + **와이어프레임 토글**(밀도·품질 가시화).
   - P3 교육/크기: 잡 이름 + 총 부피(mm³).
6. **크기 앵커 (P3, 옵션).** 바닥 mm 스케일바 또는 고정 크기 참조 토글 — 일반인 절대감.
7. **정합 안 함(명시).** 서로 다른 환자는 머리 위치·기울기 제각각(MNI 정합 안 함).
   world/RAS라 방향은 똑바로 일치, 위치만 슬롯 중심 맞춤. 나란히(포개기 아님)라 충분.
8. **로딩·제거.** 슬롯마다 glb+meta 독립 async 로드, 로딩 중 플레이스홀더, X로 제거.

---

## 테스트

**백엔드(TestClient/httpx2) — 두껍게:**
- list: 빈/다수, **응답에 filename·경로·series-path 부재 단언**.
- 업로드 다중파일 폴더 → 번호 평탄저장·dir모드·awaiting_series. 단일 zip/nifti 유지.
- **긴 상대경로 회귀**: 깊은 이름 다발 업로드 → 평탄저장으로 성공(NAS 케이스).
- series index 선택 → 올바른 nifti 매핑, 범위 밖 400.
- 서빙 status: **niftiPath 부재 단언**, series index 존재, filename 마스킹.
- name 저장·표시, 제어문자 제거, 길이 상한, 경로로 안 씀.
- 변형추가 → variantId 생김, variants 증가, **FastSurfer runner 호출 안 됨**(호출되면
  raise하는 runner 주입), paramLabel 존재. 파라미터 화이트리스트/범위 위반 거부.
  재생성 실패 → pendingVariant.error, job.state 여전히 done.
- **외부참조 없음**: static 트리 grep — 외부 URL/스킴상대 참조 발견 시 실패.

**프론트:** 리포에 JS 테스트 러너 없음(vanilla). 뷰어 비교는 헤드리스 WebGL 없이
단위테스트 불가 — **수동 검증**. 계약은 백엔드로 최대한 커버. 정직하게 명시.

---

## 범위 밖 (안 넣음)

잡 삭제·저장관리 UI(별도 deferred), 업로드 크기 상한(§11 MAX_UPLOAD_MB), MNI305/MNI152
정합(비교가 나란히라 불필요), 정식 레퍼런스 아틀라스 레지스트리, 4개 초과 그리드 비교,
canonical 설정 UI(§6.6), 잡 취소.
