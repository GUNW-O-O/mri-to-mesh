# mri2mesh

뇌 T1 MRI를 입력받아 영역별 세그멘테이션과 3D 메시를 산출하는 **로컬 단독** 도구.
소비 측 소프트웨어(brain-educate 등)가 쓸 에셋을 만드는 게 목적이다 — 상시 백엔드가 아니다.

잡 하나가 GLB 변형마다 4개 파일을 낸다:

| 파일 | 내용 |
|------|------|
| `regions.glb` | 라벨별 메시(월드 mm, 영역 색상 canonical-v1) |
| `regions-meta.json` | 영역 메타(이름·색·group·side·부피·중심) |
| `metrics.json` | 삼각형 수·정점 수·단계별 소요 |
| `params.json` | 변형 파라미터 + `variantId` |

- 설계: [docs/superpowers/specs/2026-07-22-seg-and-mesh-design.md](docs/superpowers/specs/2026-07-22-seg-and-mesh-design.md)
- 워크벤치 설계: [docs/superpowers/specs/2026-08-03-workbench-series-compare-design.md](docs/superpowers/specs/2026-08-03-workbench-series-compare-design.md)
- 계획: [docs/superpowers/plans/](docs/superpowers/plans/)

## 파이프라인

```
업로드(DICOM 폴더·ZIP·NIfTI)
  → dcm2niix 변환 · 시리즈 추천
  → [시리즈 선택 게이트]  ← 사용자가 직접 하나 고른다(자동선택 안 함)
  → FastSurfer 세그멘테이션(GPU)
  → 라벨 리맵(canonical-v1)
  → 메시 생성 → regions.glb + 메타·메트릭·파라미터
```

세그는 `seg.nii.gz`로 캐시되어, 파라미터만 바꾼 메시 재생성은 FastSurfer를 다시 돌리지 않는다(초 단위).

## 실행 (워크벤치)

브라우저에서 업로드 → 시리즈 선택 → 진행률 → 3D 뷰어까지 도는 로컬 웹 UI. 외부 통신 0, `127.0.0.1`에만 바인드한다.

```bash
cp .env.example .env      # OUTPUT_DIR, FASTSURFER_IMAGE 채운다(둘 다 필수)
docker compose up --build
# http://127.0.0.1:8000
```

`.env` 필수 값:
- `OUTPUT_DIR` — 잡 작업 폴더(호스트 경로). compose가 바인드 마운트한다.
- `FASTSURFER_IMAGE` — 버전 고정 태그(`deepmi/fastsurfer:cuda-v2.5.4` 등). `latest` 금지.

GPU는 api 컨테이너가 아니라, 그가 docker socket으로 띄우는 형제 FastSurfer 컨테이너가 `--gpus`로 잡는다.

### API

| 메서드 | 경로 | 용도 |
|--------|------|------|
| `POST` | `/api/jobs` | 업로드(멀티파트, 이름 선택) → `jobId` |
| `GET`  | `/api/jobs` | 잡 목록(PHI 없음) |
| `GET`  | `/api/jobs/{id}` | 상태 폴링(`status.json`) |
| `POST` | `/api/jobs/{id}/series` | 시리즈 선택(index) → 세그 시작 |
| `GET`  | `/api/jobs/{id}/variants/{vid}/regions.glb` | 메시 |
| `GET`  | `/api/jobs/{id}/variants/{vid}/regions-meta.json` | 영역 메타 |

## 개발

```bash
uv sync
uv run pytest
```

컨테이너 밖 테스트는 `.env.example`의 개발용 변수 참조(`DCM2NIIX_BIN`, `MRI2MESH_TEST_DATA_DIR`).

## 상태

파이프라인(io → segment → labels → mesh → jobs) + 로컬 웹 워크벤치(업로드·시리즈 게이트·백그라운드 파이프라인·three.js 뷰어)까지 구현·머지 완료. 다음: 변형 생성 + 품질 비교(P2).
