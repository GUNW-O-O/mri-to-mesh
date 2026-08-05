# 메쉬 생성옵션 품질 비교 워크벤치 설계

> 2026-08-04. 앞선 워크벤치 P1(셸+플로우)이 실사용에서 부실함이 드러나, 이
> 툴의 본래 목적 — **메쉬 생성옵션별 산출물 품질 비교** — 을 실제로 할 수
> 있게 뷰어와 생성 흐름을 개편한다.

## 1. 목적과 범위

이 툴의 본질은 **어떤 메쉬 생성옵션이 더 나은 3D 메쉬를 뽑는가를 눈으로
비교**하는 것이다. 사용자가 옵션(전처리·추출기·스무딩·데시메이션·min_voxel)을
골라 변형을 생성하고, **같은 잡에서 나온 변형들을 한 캔버스에 나란히 놓고**
품질을 비교한다. 다른 잡의 메쉬와 비교하는 것은 **부가기능**이다.

**핵심 목적(주):** 한 잡 안에서 생성옵션별 변형을 나란히 품질 비교.
**부가 목적(종):** 다른 잡·레퍼런스 변형과 나란히 비교.

### 비목표 (명시적으로 안 만드는 것)
- 캔버스에서 **영역 클릭·선택·발광 상호작용** — 불필요. orbit로 돌려보기만 한다.
- 메쉬 오버레이(포개기) — 항상 좌/우 나란히(juxtapose)다.
- 세그먼트 재실행 — 변형 생성은 캐시된 `seg.nii.gz`에서 메쉬만 다시 만든다.

## 2. 전역 제약

- **완전 로컬 · 외부 통신 0.** `127.0.0.1` 바인드, 정적 에셋에 외부 참조 금지
  (`tests/web/test_local_only.py` 가드 유지).
- **PHI (스펙 §12).** `status.json.error`에 파일명·경로·DICOM 태그 금지.
- **전역 canonical 색.** 같은 해부영역은 모든 잡·변형에서 같은 색
  (`labels/canonical-v1.tsv`). 뷰어는 GLB가 아니라 `regions-meta.json`에서 색을
  읽는다(B안).
- **world-mm 공유 스케일.** 한 잡 안 변형 비교는 정규화하지 않는다 — 같은
  해부구조라 world-mm 그대로 놓아야 크기·품질이 직접 비교된다.
- **STATES** = `("awaiting_series","running","done","error")` (기존 유지).

## 3. 현재 상태와 바뀌는 것

| 영역 | 현재 | 바뀜 |
|------|------|------|
| 변형 생성 | 파이프라인이 기본 파라미터로 v01 하나만 | **옵션 폼 + 재생성 엔드포인트**로 여러 변형 |
| 뷰어 | 단일 메쉬 `Viewer` | **N-슬롯 나란히 뷰어**(scissor) |
| 상호작용 | orbit(버그 수정됨 PR#4) + group/side/반투명 토글 | orbit 동시회전 + **조건 프리셋** |
| 비교 | 없음 | 잡 내 나란히(주) + 잡 간(부가) |

기존 `group/side` 토글과 단순 반투명 셸 토글은 **조건 프리셋**으로 대체한다.

## 4. 아키텍처

세 조각. 각각 독립 인터페이스.

```
[메쉬 옵션 폼] --params--> [POST /variants] --generate_variant--> status.variants[]
                                                                        |
[N-슬롯 뷰어] <--regions.glb + regions-meta.json + params.json-- 슬롯별 로드
     |
[조건 프리셋] 전 슬롯 material 규칙 동시 적용
```

### 4.1 재생성 엔드포인트 (신규)

`POST /api/jobs/{job_id}/variants`

- 전제: 잡이 `state == "done"`(seg.nii.gz 존재). 아니면 409.
- 요청 본문(JSON) — `MeshParams` 축을 camelCase로:
  ```json
  {
    "preprocess": {"method": "gaussian", "sigmaVox": 0.6},
    "extractor":  {"name": "skimage_mc", "options": {}},
    "smoothing":  {"method": "taubin", "iterations": 20, "passBand": 0.1, "featureAngle": 60},
    "decimation": {"method": "quadric", "targetRatio": 0.35},
    "minVoxel": 100
  }
  ```
  누락 축은 서버가 baseline으로 채운다(§4.1a `baseline_params()` 기준).
- 검증: method는 화이트리스트(아래 §4.2), 수치는 범위. 위반 시 400(PHI 없는
  메시지).
- 처리: 다음 순번 index를 잡아 `generate_variant(seg_path, variant_dir, params, index)`
  호출 → `regions.glb`·`metrics.json`·`params.json` 기록 + `regions-meta.json`
  갱신. `status.variants[]`에 `{variantId, params 요약, createdAt}` append.
- 동일 파라미터 재요청: `variant_id`가 `v<N>-<hash>`라 해시가 같으면 **이미 있는
  변형**이다. 중복 생성 대신 기존 variantId를 반환(200, `"deduped": true`).
- 응답: `{"variantId": "...", "deduped": false}`.
- 잡별 `Lock`으로 순번 경쟁 방지(기존 series 선택 lock과 동일 패턴).

### 4.1a 생성옵션 baseline (현재 프로덕션 기본값)

폼 기본값과 엔드포인트의 누락축 채움은 **현재 실제로 쓰이는 값**을 기준으로
한다 — 사용자가 "지금 이거"에서 다른 옵션을 얼마나 벗어나는지 바로 알게. 이
값은 `brainds/nifti_pipeline/mesh_export.py`(프로덕션 메쉬 산출 로직)를 참조해
정한다. 그 로직과 mri2mesh 파라미터의 등가는 확인됨:

| 축 | baseline 값 | 근거(mesh_export.py) |
|----|-------------|----------------------|
| preprocess | `none` (isolevel 0.5) | 이진 마스크에 `contour([0.5])` |
| extractor | `vtk_contour_perlabel` | pyvista `grid.contour([0.5])` = vtkContourFilter |
| smoothing | `laplacian`, iterations 30, relaxation 0.1 | `surface.smooth(n_iter=30, relaxation_factor=0.1)` = vtkSmoothPolyDataFilter |
| decimation | `none` | 데시메이션 없음 |
| minVoxel | 100 | `MIN_VOXEL_COUNT = 100` |

`mri2mesh.mesh.params`에 `baseline_params()`로 추가한다(기존 `default_params()`는
skimage_mc·smoothing none이라 프로덕션과 다르므로 폼 기본값으로 쓰지 않는다).

**파이프라인 초기 변형(v01)도 이 baseline으로 생성**한다 — 잡을 열면 처음 보이는
메쉬가 곧 프로덕션 기준선이 되고, 사용자는 그 옆에 대안 변형을 세워 비교한다.
(현재 파이프라인은 `default_params()`를 쓰므로 이 부분이 바뀐다.)

### 4.2 메쉬 옵션 폼 (프론트)

`status.state == "done"`인 잡에서 뷰어 위 패널로 연다. **폼은 §4.1a baseline으로
미리 채워져 열린다** — 거기서 축 하나씩 바꿔 대안을 탐색한다. `MeshParams` 축:

| 축 | 선택지 | 딸린 수치 |
|----|--------|-----------|
| preprocess | none / gaussian / distance | gaussian: sigmaVox(0.1~2.0) |
| extractor | skimage_mc / pymcubes / vtk_flyingedges / vtk_surfacenets / vtk_contour_perlabel | 없음 |
| smoothing | none / laplacian / taubin / humphrey | iterations(0~100), taubin: passBand·featureAngle, laplacian: relaxation, humphrey: alpha·beta |
| decimation | none / quadric | quadric: targetRatio(0.05~1.0) |
| min_voxel | 정수(0~5000) | |

"변형 생성" 누르면 `POST /variants` → 성공 시 새 변형을 슬롯에 추가하고 뷰어
갱신. 폼 검증은 서버 화이트리스트와 일치.

### 4.3 N-슬롯 나란히 뷰어

DualBrain3D(brain-educate) 패턴 이식. **바닐라 JS**로 포팅(React 아님).

- **renderer 1개 + slot마다 scene·camera** — `scissor`로 캔버스를 슬롯 수만큼
  좌→우 등분. WebGL 컨텍스트 하나만 소비.
- **동시 회전** — OrbitControls를 슬롯0 카메라에 바인딩, 매 프레임 나머지
  카메라에 `position`·`quaternion`·`zoom` 복사. 한 드래그로 전 슬롯 동시 회전.
  `enableDamping=true, dampingFactor=0.05`.
- **world-mm 공유 스케일** — 각 슬롯 GLB를 자기 bbox 중심으로 원점 정렬만 하고
  **스케일 정규화는 하지 않는다**(잡 내 비교). 카메라 거리는 슬롯 전체 최대
  반지름 기준으로 한 번 잡아 크기차가 그대로 보이게 한다.
- **슬롯 = GLB 하나.** 슬롯별 GLB/메타를 독립 로드·스왑(바뀐 슬롯만 dispose→
  재로드, 반대편 무영향). `loadGeneration`으로 잡 전환 중 옛 로드 무시.
- **슬롯 배지** — 그 변형의 파라미터 요약(예: `taubin·quadric0.35·mc`) + 슬롯별
  메트릭(삼각형·정점 수·용량). 배지 wrapper는 `pointer-events:none`로 orbit
  통과.
- **조명** — ambient 0.6 + key dir(2,3,4) 1.2 + fill(-3,-1,-2) 0.5 (기존 viewer와
  동일, brain-educate `addLights`와 일치).
- **정리(dispose)** — 슬롯 스왑·언마운트 시 geometry·material dispose, resize
  observer·controls·renderer dispose. 현재 viewer.js의 누수(dispose 없음)를 고친다.

슬롯 채우기: 선택한 잡의 `variants[]`를 슬롯으로 로드한다(최대 N, 기본 4). 변형이
많으면 사용자가 표시할 것을 고른다(체크박스). 새 변형 생성 시 슬롯 추가.

### 4.4 조건 프리셋 (표시 모드)

전 슬롯에 동시 적용하는 material 규칙. brain-educate ch4/5의 영역 세트를
그대로 쓴다(canonical-v1 이름 공유 확인됨).

- **전체(기본):** 모든 영역 불투명 + 고유색.
- **노화 / 치매 / 알콜:** 해당 세트 영역만 불투명+고유색, **대뇌백질은 5%
  무채색 셸**(윤곽 힌트), 나머지 비강조는 숨김(렌더 안 함). (DualBrain3D
  `applyMaterials` 규칙.)

영역 세트(ch4 `AGING_REGION_NAMES`, ch5 `DISEASE_REGION_SETS` 그대로):
- **노화:** 해마, 측뇌실·3·4뇌실, 대뇌백질·WM-hypointensities, 전두엽 상·중·
  안와 ctx, 소뇌피질.
- **치매:** 해마·편도, 하측뇌실(측두각), 내후각·해마곁·중측두·하측두·방추 ctx.
- **알콜:** 해마, 소뇌피질·백질, 측뇌실·3뇌실, 시상·VentralDC, 전두엽 상·
  rostralmiddle·안와 ctx.

정확한 이름 목록은 구현 시 브랜치의 상수 파일에 그대로 옮긴다.

## 5. 상호작용 규칙

- **orbit만** — 회전·줌·(팬 끔). 한 입력으로 전 슬롯 동시.
- 영역 클릭·선택·hover 없음.
- 프리셋 전환은 패널 버튼(전체/노화/치매/알콜).
- 표시할 변형 선택은 체크박스.

## 6. 구현 단계

한 설계, 세 단계. 각 단계가 독립 테스트 가능한 산출물.

- **A. 생성옵션 흐름** — 재생성 엔드포인트(§4.1) + 옵션 폼(§4.2). 산출물:
  같은 잡에서 파라미터를 바꿔 변형을 여러 개 만들 수 있다.
- **B. 나란히 비교 뷰어(핵심)** — N-슬롯 scissor 뷰어(§4.3) + 동시회전 +
  조건 프리셋(§4.4) + 배지·메트릭. 산출물: 잡 내 변형들을 나란히 품질 비교.
- **C. 잡 간 비교(부가)** — 아무 잡의 변형을 슬롯에 담기. 잡 간은 정규화 토글
  (실제 크기 world-mm / RMS 정규화)을 추가한다. 산출물: 다른 잡·레퍼런스와
  나란히.

A와 B가 이 툴의 목적을 완성한다. C는 여유 될 때.

## 7. 파일 구조(예정)

- `mri2mesh/web/app.py` — `POST /variants` 라우트 + `VariantRequest` 모델·검증.
- `mri2mesh/web/static/options.js` (신규) — 옵션 폼 렌더·검증·POST.
- `mri2mesh/web/static/viewer.js` — `Viewer`를 N-슬롯으로 재작성(scissor·동시
  회전·슬롯 스왑·dispose).
- `mri2mesh/web/static/presets.js` (신규) — 조건별 영역 세트 상수 + material 규칙.
- `mri2mesh/web/static/index.html` — 옵션 폼·프리셋 버튼·슬롯 배지 자리.
- `mri2mesh/web/static/app.js` — 컨트롤러에 옵션 폼·슬롯 선택 배선.
- `tests/web/test_app.py` — `POST /variants` 계약(검증·중복·PHI·done 전제) 테스트.

---

## 부록: 구현 갱신 (2026-08-05)

Phase B를 구현하며 사용자 피드백으로 방향을 조정:

- **캔버스 가로분할(scissor) 폐기 → pan-row.** 렌더러·scene·카메라 하나로,
  변형들을 world-mm 그대로 **한 scene에 일렬 배치**하고 OrbitControls **pan을
  켜서** 옆 변형으로 이동하며 본다. 슬롯별 별도 viewport/카메라 동기화 불필요.
- **하단 변형 토글 바(1 2 3 …).** 각 변형 on/off. 보이는 것만 좌→우로 촘촘히
  재배치(꺼진 건 자리 안 차지), 매번 카메라 리프레임.
- **내부 확인 = 빅뱅(explode).** clipping 안 함("메쉬 외부만 완벽하면 됨").
  각 영역을 브레인 중심에서 상대거리 비례로 바깥 이동(vpanel 슬라이더).
  영역별 외부면을 분리해 확인.
- 조건 프리셋(노화/치매/알콜)·잡 간 비교(Phase C)는 여전히 deferred.
