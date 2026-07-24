# seg-and-mesh 설계

- 작성일: 2026-07-22
- 저장소: https://github.com/GUNW-O-O/seg-and-mesh
- 상태: 설계 확정 대기

## 1. 무엇을 하는 도구인가

뇌 MRI를 입력받아 **영역별 세그멘테이션 볼륨과 3D 메시를 산출하는 로컬 도구**다.

- 입력: 뇌 T1 MRI. DICOM(ZIP) 또는 NIfTI.
- 처리: DICOM→NIfTI 변환 → FastSurfer 세그멘테이션 → 라벨 리맵 → 영역별 3D 메시 생성.
- 출력: 케이스 폴더 하나에 담긴 4개 파일(볼륨 2개, 메시 1개, 메타데이터 1개).

로컬 웹 UI에서 파일을 올리면 도커 컨테이너가 처리하고, 결과를 호스트 폴더로 내보낸다.

메시 생성 방법과 파라미터(추출 라이브러리, 스무딩 강도·반복 수 등)는 아직 확정되지 않았다. 어느 조합이 가장 좋은지 실측해서 정해야 한다. 그래서 이 도구는 먼저 **여러 조합을 만들어 비교하는 워크벤치**로 동작하고, 확정 후에는 그 설정으로 정본을 뽑는 생성기로 굳는다. 두 역할을 한 도구가 겸하되 산출물은 명확히 구분한다(6.6).

## 2. 산출물

케이스 하나당 다음 4개 파일을 한 폴더에 만든다.

| 파일 | 내용 | 자료형 |
|---|---|---|
| `orig.nii.gz` | FastSurfer conform 공간 T1 (256³, 1mm) | uint8 |
| `seg.nii.gz` | 리맵된 라벨 볼륨, `orig`와 동일 공간 | uint8 |
| `regions.glb` | 영역별 메시, 노드명 `label_<id>` | GLB |
| `regions-meta.json` | 라벨 메타데이터 | JSON |

`orig`와 `seg`는 둘 다 FastSurfer conform 공간에서 나오므로 affine과 차원이 일치한다. 두 볼륨을 겹쳐 볼 때(MPR 오버레이) 정합이 보장된다.

### 2.1 볼륨 자료형

두 볼륨 모두 uint8로 무손실 저장한다.

- `orig`: FastSurfer conform 출력은 0–255 uchar이므로 uint8이 원본 그대로다.
- `seg`: 라벨 번호 상한이 256 미만이므로 uint8이 무손실이다.

float32로 저장하면 파일과 메모리를 4배 쓰면서 정보 이득이 없다. NIfTI 저장 시 원본 헤더를 복사한 뒤 dtype만 교체한다. 빈 헤더를 새로 만들면 qform/sform code, `xyzt_units` 같은 필드가 기본값으로 리셋된다.

### 2.2 `regions-meta.json`

```json
{
  "version": 2,
  "labelTable": "canonical-v1",
  "space": {
    "affine": [[-1,0,0,134.7],[0,0,1,-115.2],[0,-1,0,129.25],[0,0,0,1]],
    "shape": [256, 256, 256],
    "voxelSize": [1.0, 1.0, 1.0],
    "toMNI152": [[0.98,0.01,-0.03,-2.1],[-0.01,1.02,0.02,18.7],[0.03,-0.02,0.97,-14.3],[0,0,0,1]]
  },
  "source": {
    "engine": "fastsurfer",
    "engineVersion": "2.4.2",
    "segFile": "aparc.DKTatlas+aseg.deep.withCC.mgz"
  },
  "meshConfig": "canonical-mesh-v1",
  "meshVariantId": "v07-3c81",
  "regions": [
    {
      "labelId": 1,
      "name": "Left-Hippocampus",
      "color": [220, 216, 20],
      "nodeName": "label_1",
      "fsId": 17,
      "group": "subcortical",
      "side": "L",
      "volumeMm3": 4123.5,
      "centroid": [-26.1, -21.4, -14.8],
      "triangleCount": 8420
    }
  ]
}
```

모든 필드는 소비 측이 온전히 사용하는 것을 전제로 낸다.

- `labelId` — 리맵 후 라벨 번호. `seg.nii.gz`의 voxel 값이자 GLB 노드명(`label_<labelId>`)이다.
- `name`, `color`, `fsId` — 구조 이름, RGB(0–255), FreeSurfer 원본 라벨 번호.
- `group` (`cortex` / `subcortical` / `ventricle` / `wm` / `cerebellum` / `brainstem` / `cc` / `other`), `side` (`L` / `R` / `M`) — 레이어·좌우 필터용.
- `volumeMm3` — 리맵 전 voxel count × voxel 부피. stats 파일 파싱 없이 계산한다.
- `centroid` — 영역 카메라 포커스, 라벨 텍스트 배치용.
- `nodeName` — GLB mesh 노드 이름. `label_<labelId>`로 확정한다. 소비 측은 노드명으로만 매칭한다(순서 의존 없음).
- `space.affine` — voxel→native RAS(FastSurfer conform 공간). `orig`/`seg`의 sform과 동일. MPR 오버레이·mesh 좌표의 기준.
- `space.toMNI152` — **voxel→MNI152 어파인(4×4).** 서로 다른 피험자의 볼륨을 공통 공간에서 정렬하기 위한 것이다. 소비 측(brain-educate)이 정상 vs 질병·나이대별 케이스를 2행 MPR로 비교할 때, 공통 mm 좌표로 슬라이더를 움직여 각 케이스가 자기 어파인으로 자기 voxel 인덱스를 구하면 "같은 슬라이더 = 같은 해부면"이 성립한다(conform만으론 격자만 같고 각 뇌가 제자리에 없어 안 맞는다). **선형(12-DOF 어파인)만이다** — 상대 크기를 보존해 위축·뇌실확장 같은 병증 차이가 그대로 보인다. 비선형 워프는 그 차이를 지우므로 쓰지 않는다. **라이선스-프리로 산출한다**: FastSurfer talairach(`--tal_reg`)는 FreeSurfer 라이선스를 요구하므로(§6.4) 쓰지 않고, 세그 뒤 별도 단계에서 conform된 `orig`를 `mask.mgz`로 뇌 추출한 뒤 MNI152 템플릿에 SimpleITK 어파인 등록해 얻는다. 값은 voxel→MNI152 완성 4×4이며 native sform과 별개다 — `orig`/`seg`의 NIfTI 헤더는 native RAS를 유지한다(MPR 오버레이·mesh 좌표 기준).
- `labelTable`, `source`, `meshConfig`, `meshVariantId` — 출처. 서로 다른 시점·설정으로 만든 에셋이 섞였을 때 판별한다.

## 3. 라벨 테이블

리맵의 목표는 FastSurfer가 낸 FreeSurfer 라벨 번호(불연속, 최대 2000번대)를 1부터 시작하는 조밀한 번호로 바꾸는 것이다. **번호는 입력 볼륨 내용과 무관하게 고정한다.**

`labels/canonical-v1.tsv`를 저장소에 버전 고정으로 둔다.

```
id	fs_id	name	group	side	r	g	b
1	17	Left-Hippocampus	subcortical	L	220	216	20
2	53	Right-Hippocampus	subcortical	R	220	216	20
...
```

- `id`는 고정이다. 같은 구조는 어느 입력에서든 같은 번호를 갖는다.
- 매칭은 `fs_id`(숫자)로 한다. 이름은 표시용이다. 이름 문자열로 매칭하면 LUT 버전이 바뀔 때(예: `Left-Thalamus-Proper` → `Left-Thalamus`) 조용히 실패한다.
- 리맵은 이 표에서 만든 정적 LUT 하나로 수행한다. 표에 없는 라벨은 0(배경)이 된다.
- 케이스에 없는 구조는 메시가 안 생기고 `regions` 배열에서 빠질 뿐, 다른 구조의 번호에 영향을 주지 않는다.

**입력 세그 파일은 `aparc.DKTatlas+aseg.deep.withCC.mgz`를 쓴다.** `withCC`가 없는 판에는 뇌량(`CC_Anterior`~`CC_Posterior` 5개 라벨)이 빠진다.

라벨 집합은 **FastSurfer DKT 전체**를 포함한다. `vessel`, `WM-hypointensities`, `5th-Ventricle` 등도 표에 넣되 `group` 태그로 소비 측에서 걸러낸다. 파이프라인에서 버리지 않는다.

표는 FastSurfer 이미지의 `FreeSurferColorLUT.txt`에서 생성하는 스크립트(`labels/build_from_lut.py`)를 함께 두되, 생성 결과물을 저장소에 커밋해 런타임에 LUT를 읽지 않는다.

## 4. 사용 방법

```
# 1. 설정
cp .env.example .env
#   .env에서 OUTPUT_DIR, FASTSURFER_IMAGE 지정

# 2. 기동
docker compose up

# 3. 브라우저에서 http://localhost:8000
#    - MRI 파일(DICOM ZIP 또는 .nii/.nii.gz) 업로드
#    - 시리즈가 여러 개면 T1 시리즈 선택 (자동 추천됨)
#    - 세그멘테이션 실행 (수 분)
#    - 메시 변형을 만들고 뷰어로 비교
#    - 채택한 변형을 내보내기 → OUTPUT_DIR/<case>/ 에 4개 파일 생성
```

## 5. 아키텍처

```
┌─────────────────────────────┐
│ 브라우저 localhost:8000     │
│ - 업로드 / 시리즈 선택      │
│ - 메시 변형 생성 · 비교     │
│ - 3D 뷰어                   │
│ - 스토리지 정리             │
└───────────┬─────────────────┘
            │ HTTP
┌───────────▼─────────────────┐   컨테이너 실행
│ api (FastAPI)               ├──────────────┐
│ - 잡 큐, 상태 JSON          │              │
│ - 입력 판별, 안전 해제      │        ┌─────▼──────────┐
│ - dcm2niix 변환             │        │ fastsurfer     │
│ - 라벨 리맵                 │        │ (deepmi, GPU)  │
│ - 메시 변형 생성 · 지표     │        │ --seg_only     │
│ - 스토리지 관리             │        └─────┬──────────┘
└───────────┬─────────────────┘              │
      ┌─────▼──────────────────────────────────▼─────┐
      │ 공유 볼륨 /work                              │
      │ jobs/<jobId>/...                             │
      └─────┬────────────────────────────────────────┘
            │ 채택 변형만 복사
      ┌─────▼──────────────────────────┐
      │ 호스트 출력 폴더 (OUTPUT_DIR)  │
      │ <case>/ 4개 파일               │
      └────────────────────────────────┘
```

- compose 서비스는 `api`, `fastsurfer` 둘이다. GPU는 `fastsurfer`에만 할당한다.
- `api`는 잡마다 `fastsurfer`를 1회성으로 실행한다. 상시 구동이 아니므로 유휴 상태에서 GPU 메모리를 점유하지 않는다.
- 잡 상태는 파일시스템에 둔다(`status.json`). DB를 쓰지 않는다.

### 5.1 FastSurfer 실행 트리거

**docker socket 마운트로 실행한다.** `api` 컨테이너에 호스트 docker socket을 바인드하고, 잡마다 형제 컨테이너로 FastSurfer를 띄운다.

소켓 접근 권한은 호스트 root와 동등하다(소켓이 있으면 `-v /:/host --privileged` 같은 컨테이너를 데몬에 요청할 수 있다). 그럼에도 택하는 근거는 위협 모델이다. 이 도구는 단일 사용자가 로컬에서 자기 파일로만 실행하고 외부에 노출되지 않는다. 소켓 권한이 실제 공격면으로 전환되는 경로가 없다. 반면 얻는 것은 표준적 구현과 docker API가 주는 정확한 종료 코드·로그 스트림·`docker kill` 취소다.

전제가 바뀌면(여러 사용자 접근, 신뢰할 수 없는 입력, 외부 노출) 큐 감시 방식으로 옮긴다. 전환 비용을 낮추기 위해 실행 트리거를 `segment/` 안의 인터페이스 하나로 격리하고 socket 구현체를 그 뒤에 둔다. 호출부는 "잡을 실행하고 종료 코드와 로그를 돌려받는다"만 안다.

`api`에는 socket과 `OUTPUT_DIR` 외에 호스트 경로를 추가로 마운트하지 않는다.

## 6. 파이프라인

| 단계 | 구현 | 산출 |
|---|---|---|
| 1. 입력 판별 | 매직바이트 + pydicom 폴백 | 입력 종류 |
| 2. 안전 해제 | ZIP 검증 후 전개 | DICOM 파일 목록 |
| 3. NIfTI 변환 | `dcm2niix -d 5` 재귀 | 시리즈별 `.nii.gz` |
| 4. 시리즈 선택 | 자동 추천 + 사용자 확인 | T1 볼륨 1개 |
| 5. 세그멘테이션 | FastSurfer `--seg_only` | `orig.mgz`, `aparc.DKTatlas+aseg.deep.withCC.mgz` |
| 6. 리맵 | 정적 LUT | `seg.nii.gz` (uint8) |
| 7. 메시 변형 | 추출 → 스무딩 → 데시메이션 | 변형별 GLB + 메타 + 지표 |
| 8. 내보내기 | 채택 변형 복사 | `<case>/` 4개 파일 |

**1~6단계는 잡당 1회, 7단계는 N회 반복한다.** FastSurfer는 수 분, 메시 생성은 수십 초다. 세그 결과를 재사용해 변형을 계속 추가하는 것이 이 도구의 핵심이다.

### 6.1 입력 판별

확장자를 신뢰하지 않는다. DICOM 파일명은 `IM000001`, `I10`, SOP Instance UID 문자열, 확장자 없음, `.ima`, `.img` 등 제각각이다. 매직바이트로 판별한다.

| 대상 | 판별 |
|---|---|
| ZIP | 첫 4바이트 `PK\x03\x04` |
| gzip | `\x1f\x8b` → 전개 후 아래 검사 |
| NIfTI-1 | 첫 4바이트 int32 == 348 (양쪽 엔디안) + offset 344에 `n+1\0` |
| NIfTI-2 | sizeof_hdr == 540 + `n+2` |
| DICOM | offset 128에 `DICM` |

preamble 없는 DICOM(메타 헤더 없이 raw dataset으로 내보낸 파일)은 `DICM`이 없다. 2단계로 판별한다.

1. offset 128의 `DICM` 확인. 있으면 확정.
2. 없으면 `pydicom.dcmread(force=True, stop_before_pixels=True)` 시도. `SOPClassUID`(0008,0016) 또는 `Rows`/`Columns`가 있으면 DICOM으로 인정.
3. 둘 다 실패하면 무시.

ZIP 내부 파일은 전부 이 검사를 거쳐 DICOM만 추린다. 파일명은 보지 않는다.

### 6.2 안전 해제

ZIP 전개 시 다음을 강제한다.

1. **경로 순회 차단.** 엔트리 이름에 `..`나 절대 경로가 있으면 임의 위치에 파일이 기록된다. 각 엔트리의 정규화 경로가 목적지 하위인지 검사하고, 벗어나면 잡 전체를 거부한다. 심볼릭 링크 엔트리도 거부한다.
2. **파일명 인코딩.** Windows 기본 압축은 CP949로 저장하면서 UTF-8 플래그(비트 0x800)를 켜지 않는다. Python `zipfile`은 이를 CP437로 해석해 이름이 깨진다. 플래그가 없으면 CP949로 재디코딩한다. 판별에는 파일명을 안 쓰지만 로그·오류 메시지 가독성에 필요하다.
3. **용량 상한.** 전개 총 용량(기본 20GB)과 엔트리 수 상한을 두고 초과 시 중단한다.
4. `__MACOSX/`, `.DS_Store`, `Thumbs.db`, `DICOMDIR` 등은 매직바이트 검사에서 자동 탈락하므로 별도 처리하지 않는다.

### 6.3 시리즈 선택

`dcm2niix`는 입력 디렉터리를 재귀 탐색하고(기본 깊이 5, `-d`로 조절) SeriesInstanceUID로 그룹핑한다. 따라서 `study/series1/*`, `study/series2/*` 같은 중첩 폴더 구조가 그대로 처리된다.

변환 결과 시리즈가 2개 이상이면 웹에서 목록을 제시한다. 각 항목에 SeriesDescription, 슬라이스 수, voxel size, 3D 여부를 표시한다.

T1 후보 자동 추천 기준: 등방성에 가까운 voxel(약 1mm), 슬라이스 128장 이상, description이 `mprage|t1|bravo|spgr|tfl` 매칭. 해당 항목을 상단 정렬하고 기본 선택한다.

**자동 선택만으로 진행하지 않고 사용자 확인을 받는다.** FastSurfer는 1mm T1w를 전제하므로 잘못된 시리즈를 넣으면 결과가 조용히 망가진다.

### 6.4 세그멘테이션

FastSurfer 스크립트 인자(고정):

```
--t1 <input.nii.gz> --sd <job>/fs --sid case --seg_only --vox_size 1.0 --threads 8 --allow_root
```

docker 실행 플래그로 `--user root`를 준다. 이 둘은 **별개이고 둘 다 필요하다.**

- `--user root`(docker `-u` 플래그)는 컨테이너 프로세스를 root(UID 0)로 실행한다. FastSurfer 이미지 기본 사용자 `nonroot`(UID 1000)는 호스트 UID 매핑 없이 실행 시 `run_fastsurfer.sh`가 거부한다(출력 소유권·내부 경로 쓰기 문제). 새 프로그램을 실행하는 게 아니라 엔트리포인트를 누가 실행하느냐만 바꾼다.
- `--allow_root`(FastSurfer 인자)는 그 스크립트가 root 실행을 막는 **두 번째** 가드를 푼다. `--user root`만 주면 root 가드에, `--allow_root`만 주면 nonroot 가드에 걸린다.
- 바인드 마운트한 `OUTPUT_DIR`에 쓰인 파일은 호스트에서 root:root 소유가 된다. 스펙 §5.1의 위협 모델(로컬 단독·비노출, docker socket = 호스트 root) 안이라 수용한다. 정공법 `-u $(id -u):$(id -g)`는 Windows/Docker Desktop에서 매핑이 지저분하고 단일 사용자 로컬 툴엔 과하다.
- **공통공간 어파인(§2.2 `space.toMNI152`)은 FastSurfer로 얻지 않는다.** talairach를 뽑는 `--tal_reg`가 **FreeSurfer 라이선스를 요구**하기 때문이다(실측: `ERROR: The talairach-registration ... require(s) a FreeSurfer License`). 라이선스 없는 자족성이 이 도구의 이점(아래 `--seg_only` 항목)이라 그걸 깨지 않는다. `--seg_only` 기본은 talairach를 안 만든다(실측: transforms에 `cc_up.lta`·`orient_volume.lta`뿐, stats에 eTIV 없음). 따라서 **세그 뒤 별도 라이선스-프리 단계**로 conform된 `orig`를 (FastSurfer `mask.mgz`로 뇌 추출 후) MNI152 템플릿에 **어파인 등록(SimpleITK)** 해 voxel→MNI152 4×4를 만든다. `--tal_reg`·`--3T`는 쓰지 않는다(`--3T`는 talairach atlas 선택일 뿐이라 tal_reg 없이는 무의미).
- **`--no_cc`는 절대 주지 않는다.** 뇌량 5개 라벨(251~255)이 조용히 빠진다(§14 확정).
- `--seg_only`는 surface 파이프라인을 건너뛴다. 메시 생성에 surface 결과를 안 쓰므로 불필요하고, **FreeSurfer 라이선스도 필요 없다.**
- 이미지 태그는 고정한다. `latest`를 쓰지 않는다.
- 실측(RTX 3070 8GB, `deepmi/fastsurfer:cuda-v2.5.4`): 총 2분28초, 피크 2.3GB, 출력 라벨 정확히 100개(§3). VRAM 부족 시 오류에서 식별해 CPU 폴백을 안내한다. GPU가 없으면 CPU 이미지로 폴백하고 소요 시간 경고를 표시한다.

### 6.5 메시 생성 — 비교 축

"어떤 라이브러리가 좋은가"보다 **3개 독립 축**으로 분해하는 편이 비교에 유용하다. 라이브러리는 각 축의 구현체일 뿐이다.

**축 1. 마스크 전처리 (안티에일리어싱)**

이진 마스크에 곧바로 등고면을 추출하면 voxel 계단이 남는다. 스무딩을 세게 걸어 지우려 하면 부피가 수축한다. 전처리로 계단을 줄이면 약한 스무딩으로 끝나므로 이 축이 최종 품질에 크게 작용한다.

| 값 | 설명 |
|---|---|
| `none` | 이진 마스크 그대로 |
| `gaussian` | 마스크에 가우시안 블러 후 0.5 등고면. `sigmaVox` 파라미터 |
| `distance` | 부호거리장 변환 후 0 등고면 |

**축 2. 표면 추출기**

| 값 | 구현 | 특성 |
|---|---|---|
| `vtk_surfacenets` | `vtkSurfaceNets3D` (VTK 9.3+) | 다중 라벨 1패스, 인접 영역 경계 공유, 빠름 |
| `vtk_flyingedges` | `vtkDiscreteFlyingEdges3D` | 다중 라벨 1패스, marching cubes 계열 |
| `vtk_contour_perlabel` | `pyvista.contour` 라벨별 반복 | 라벨마다 전체 볼륨 순회 |
| `skimage_mc` | `skimage.measure.marching_cubes` | Lewiner, numpy 친화 |
| `pymcubes` | PyMCubes | `mcubes.smooth()` 내장(레벨셋 전처리) |

**축 3. 후처리**

스무딩:

| 값 | 파라미터 | 특성 |
|---|---|---|
| `none` | — | |
| `laplacian` | `iterations`, `relaxation` | 부피 수축이 큼 |
| `taubin` | `iterations`, `passBand`, `featureAngle` | 수축 억제. VTK `vtkWindowedSincPolyDataFilter` |
| `humphrey` | `iterations`, `alpha`, `beta` | trimesh |
| `hc_laplacian` | `iterations` | pymeshlab |

데시메이션: `none` 또는 `quadric`(`targetRatio`). 구현은 VTK `vtkQuadricDecimation`, Open3D, `fast-simplification` 중 선택 가능하게 둔다.

세 축의 조합 하나가 하나의 **변형(variant)** 이다.

**축 구현체는 큐레이션하지 않고 전부 노출한다.** 어떤 조합이 최선인지는 도구가 정하지 않는다 — 사용자가 축을 골라 변형을 만들고, 지표(§7.2)와 뷰어(§8)로 "이 조합이면 이렇게 나온다"를 확인해 스스로 찾는다. 조합 수가 75든 300이든 상관없다. UI는 각 축 값의 **특징**(위 표의 설명: `taubin`=수축 억제, `surfacenets`=경계 공유·빠름 등)을 툴팁으로 노출해 판단 근거만 제공한다. 도구가 프리셋을 고르거나 지표로 자동 합격/불합격을 매기지 않는다.

**라벨 필터 (min-voxel 노브).** 세그멘테이션은 진짜 구조 외에 voxel 몇 개짜리 노이즈 반점도 남긴다. 라벨별 voxel 수가 임계 미만이면 그 라벨은 GLB 노드도 `regions-meta.json` 항목도 만들지 않는다. 기본값 **100**(1mm 등방에서 100mm³ ≈ 반경 4.6mm 구) — DKT 피질·피하 구조 최소치(편도·측좌핵 수백~수천, 뇌량 조각 수백 voxel)보다 훨씬 아래라 진짜 구조는 죽이지 않고 반점만 거른다. 노브로 노출한다(0 = 전부). 스킵된 라벨 수는 `metrics.json`에 남긴다.

### 6.6 변형 관리와 정본 확정

**변형은 실험 산출물이고, 내보내기는 커밋된 설정에서만 나온다.** 이 구분을 지키지 않으면 두 가지가 깨진다.

1. **동일 파일명, 다른 내용.** 파라미터가 다른 두 `regions.glb`가 파일명이 같다. 에셋 폴더만 건네받으면 어느 쪽인지 알 수 없다.
2. **재현 불가.** 반년 뒤 "이 에셋 어떤 설정으로 뽑았나"에 답할 수 없다.

절차:

1. **탐색 단계.** UI에서 축을 조합해 변형을 만든다. 각 변형은 `jobs/<jobId>/mesh/<variantId>/`에 파라미터·산출물·지표와 함께 저장된다. 뷰어로 비교한다. 이 산출물은 **내보내기 폴더로 복사되지 않는다.**
2. **확정.** 채택한 변형의 `params.json`을 `configs/canonical-mesh-v1.json`으로 저장소에 커밋한다.
3. **정본 생성.** 커밋된 설정 파일만 참조해 생성한다. 결과 메타에 `meshConfig: "canonical-mesh-v1"`이 기록된다. 파라미터를 바꾸려면 설정 파일 버전을 올려 커밋한다. 런타임에 임의 조정한 값은 내보내기 대상이 될 수 없다.

압축(meshopt/draco)은 파이프라인에 넣지 않는다. 세그·메시 품질과 무관한 배포 정책이므로 정본 GLB에 `gltf-transform` 계열로 변환하는 별도 스크립트로 분리한다. 이렇게 두면 압축본이 필요해졌을 때 이미 만든 정본에 변환만 돌리면 되고, 파이프라인 플래그로 두면 압축본이 필요할 때마다 FastSurfer부터 다시 돌려야 한다.

### 6.7 GLB 작성

렌더러 씬을 경유하는 export(예: `pyvista.Plotter.export_gltf`)를 쓰지 않는다. 조명·카메라가 함께 실리고 압축이 없어 파일이 불필요하게 커진다. trimesh/pygltflib로 직접 작성한다.

노드명을 `label_<id>`로 확정한다. 소비 측은 이 노드명으로만 영역을 매칭한다. mesh 순서에 의존하는 폴백은 두지 않는다.

**GLB는 형상 + 노드명만 싣는다. 색을 material에 굽지 않는다.** 색·`group`·`side`·부피·centroid·triangle 수는 `regions-meta.json`이 운반하고(소비측 계약), 뷰어가 노드명으로 매칭해 칠한다. 이유: GLB가 가벼워지고, group/side 토글·선택 하이라이트에서 색을 자유롭게 바꿀 수 있으며, 색 정책이 바뀌어도 GLB를 재생성할 필요가 없다. 색 출처는 `canonical-v1.tsv`의 rgb다(§3).

**좌표는 mm 단위 world 좌표.** voxel→world 변환은 **정점에 affine을 한 번만** 적용한다. 추출기 격자에 spacing을 넣고 다시 affine을 곱하는 **이중 스케일링을 하지 않는다** — 등방 1mm에선 안 드러나고 비등방 볼륨에서 축별로 늘어나 조용히 틀린다(brainds `mesh_export.py`에서 확인된 함정, `brain-educate/docs/todo.md §6.1`에 기록). 원점 재배치는 하지 않는다. 뷰어가 GLB scene bbox 중심을 원점으로 잡고 최대 extent로 스케일한다(§8).

## 7. 잡 디렉터리 구조

```
/work/jobs/<jobId>/
  status.json              잡 상태·산출물·용량
  input/                   업로드 원본
  nifti/                   dcm2niix 결과, 선택된 input.nii.gz
  fs/                      FastSurfer subject 디렉터리
  seg/                     seg.nii.gz, orig.nii.gz, 라벨표 스냅샷
  mesh/
    <variantId>/
      params.json
      regions.glb
      regions-meta.json
      metrics.json
      log.txt
  out/                     내보내기용으로 채택된 변형 사본
```

`variantId`는 `v<순번>-<파라미터 해시 앞 4자리>` 형식이다(예: `v07-3c81`). 해시로 동일 파라미터 재실행을 감지해 중복 생성을 막는다.

### 7.1 `params.json`

```json
{
  "variantId": "v07-3c81",
  "createdAt": "2026-07-22T14:03:11Z",
  "preprocess": { "method": "gaussian", "sigmaVox": 0.6 },
  "extractor": { "name": "vtk_surfacenets", "library": "vtk", "libraryVersion": "9.3.1", "options": {} },
  "smoothing": { "method": "taubin", "iterations": 20, "passBand": 0.1, "featureAngle": 60 },
  "decimation": { "method": "quadric", "targetRatio": 0.35, "library": "vtk" },
  "labelTable": "canonical-v1",
  "segSource": "aparc.DKTatlas+aseg.deep.withCC.mgz"
}
```

### 7.2 `metrics.json`

파라미터를 눈으로만 고르면 근거가 남지 않는다. 변형마다 다음을 자동 계산해 기록한다.

```json
{
  "durationSec": { "extract": 12.4, "smooth": 3.1, "decimate": 5.0, "write": 2.2 },
  "glbBytes": 11230000,
  "total": { "vertices": 812345, "triangles": 1604882 },
  "perRegion": [
    { "labelId": 1, "triangles": 8420,
      "meshVolumeMm3": 4090.2, "voxelVolumeMm3": 4123.5, "volumeErrorPct": -0.81,
      "surfaceAreaMm2": 2210.4, "boundaryEdges": 0, "nonManifoldEdges": 0 }
  ],
  "summary": {
    "volumeErrorPctMedian": -0.9, "volumeErrorPctMax": -4.2,
    "regionsWithBoundaryEdges": 0, "regionsWithNonManifold": 0,
    "labelsSkippedByMinVoxel": 3
  }
}
```

지표는 **판단 근거일 뿐 자동 합격/불합격 게이트가 아니다.** 도구가 `volumeErrorPct` 임계로 변형을 거부하지 않는다 — 해마는 2%도 문제, 백질은 5%도 무관하듯 허용치는 도메인·용도별이라 사용자 몫이다. 뷰어가 숫자를 오버레이로 보여주고(§8) 사람이 판단한다. "확정"의 강제는 임계 검사가 아니라 채택한 `params.json`을 `configs/canonical-mesh-v1.json`으로 커밋해 **출처를 고정**하는 것이다(§6.6).

각 지표의 쓸모:

- **`volumeErrorPct`** — 스무딩 강도의 객관적 상한. 메시 부피를 voxel 실측 부피와 비교한다. Laplacian 계열은 반복할수록 수축하는데, 눈으로는 "매끄러워졌다"로만 보이고 해마가 5% 줄어든 것은 안 보인다. 스무딩 파라미터 결정의 1차 기준이다.
- **`boundaryEdges`** — 구멍. 닫힌 표면이면 0이어야 한다.
- **`nonManifoldEdges`** — 위상 결함. 8절 반투명 렌더에서 밝기 얼룩으로 드러난다.
- **`triangles`, `glbBytes`, `durationSec`** — 성능·용량 비용.

## 8. 뷰어

메시 품질은 실제로 쓰일 렌더 조건에서 봐야 한다. 다른 조명·재질로 보면 잘못된 근거로 파라미터를 확정하게 된다. 뷰어는 three.js로 구현하며 다음 두 재질 상태를 모두 제공한다.

- **불투명** (`opacity 1.0`, `depthWrite true`, `MeshStandardMaterial`, `DoubleSide`) — **기하 품질 판단의 기본.** voxel 계단, 스무딩 과다로 뭉개진 표면, 데시메이션 흔적이 여기서 보인다.
- **반투명 셸** (`opacity 0.05`, `transparent true`, `depthWrite false`, `DoubleSide`) — 법선 반전, 중복 표면, 자기교차가 밝기 얼룩으로 드러난다. 불투명 렌더에서는 안 보이는 결함이므로 둘 다 필요하다.

기본 렌더 설정:

| 항목 | 값 |
|---|---|
| 렌더러 | `WebGLRenderer({ antialias: true, alpha: true })`, `setPixelRatio(devicePixelRatio)` |
| 카메라 | `PerspectiveCamera(35, aspect, 0.01, 1000)` + `OrbitControls` |
| 조명 | `AmbientLight(0xffffff, 0.6)` / `DirectionalLight(0xffffff, 1.2)` at (2,3,4) / `DirectionalLight(0x7799cc, 0.5)` at (-3,-1,-2) |
| 배치 | GLB scene bbox 중심을 원점으로, 최대 extent를 목표 반경으로 스케일 |
| 톤매핑·색공간 | 오버라이드 없음(three 기본) |

기능:

- 변형 2개 좌우 분할, 카메라 동기화
- 영역 선택, `group`/`side` 토글
- 시점 프리셋(좌측면·상면·관상면) — 변형 간 스크린샷을 같은 각도로 비교
- 현재 변형의 `params.json`·`metrics.json` 요약 오버레이

## 9. 스토리지 관리

세그 산출물과 변형이 쌓이면 볼륨이 빠르게 커진다. 잡 하나에서 `fs/`는 수백 MB, `input/`은 DICOM 원본 크기, `mesh/`는 변형 수에 비례한다. 조회와 정리를 UI에서 할 수 있어야 한다.

### 9.1 `status.json`

```json
{
  "jobId": "2026-07-22-143011-a3f9",
  "caseName": "case01",
  "createdAt": "2026-07-22T14:30:11Z",
  "updatedAt": "2026-07-22T14:41:52Z",
  "state": "done",
  "step": "mesh",
  "input": { "filename": "study.zip", "kind": "dicom-zip", "bytes": 412000000 },
  "selectedSeries": { "description": "MPRAGE", "slices": 176, "voxelSizeMm": [1,1,1] },
  "engine": { "name": "fastsurfer", "version": "2.4.2", "device": "cuda" },
  "variants": [
    { "variantId": "v07-3c81", "bytes": 11230000, "createdAt": "..." }
  ],
  "exported": { "variantId": "v07-3c81", "path": "D:/out/case01", "at": "..." },
  "workspace": {
    "retention": "full",
    "dirs": [
      { "name": "input", "bytes": 412000000, "present": true, "purgeable": true },
      { "name": "nifti", "bytes": 15000000, "present": true, "purgeable": true },
      { "name": "fs",    "bytes": 650000000, "present": true, "purgeable": true },
      { "name": "seg",   "bytes": 4000000, "present": true, "purgeable": false },
      { "name": "mesh",  "bytes": 78000000, "present": true, "purgeable": true },
      { "name": "out",   "bytes": 11000000, "present": true, "purgeable": false }
    ],
    "totalBytes": 1170000000
  },
  "error": null
}
```

### 9.2 정리 범위

정리 단위를 "무엇을 다시 할 수 있게 남길 것인가"로 정의한다.

| scope | 삭제 | 남김 | 재실행 가능 지점 |
|---|---|---|---|
| `raw` | `input/` | 나머지 전부 | 시리즈 재선택 불가, 그 외 전부 |
| `slim` | `input/`, `fs/` | `nifti/`, `seg/`, `mesh/`, `out/` | **메시 변형 생성** |
| `variants` | `mesh/` 중 채택본 제외 | 채택 변형 | 메시 변형 생성 |
| `intermediate` | `input/`, `nifti/`, `fs/`, `mesh/` | `seg/`, `out/` | 메시 변형 생성 |
| `all` | 잡 디렉터리 전체 | — | 없음 |

`slim`이 기본 권장이다. 용량 대부분을 차지하는 `input/`과 `fs/`를 지우면서 `seg/`를 남기므로, FastSurfer를 다시 돌리지 않고 변형 생성을 계속할 수 있다.

`seg/`와 `out/`은 `purgeable: false`로 표시해 개별 삭제에서 제외한다. 지우려면 `all`을 써야 한다.

### 9.3 API와 안전장치

- `GET /api/storage` — 볼륨 총 사용량, 남은 디스크, 잡별 요약
- `POST /api/jobs/{id}/purge` — `{ "scope": "slim", "dryRun": true }`
- `POST /api/storage/purge` — 일괄. `{ "scope": "slim", "olderThanDays": 30, "states": ["done"], "dryRun": true }`

안전장치:

1. **dry-run 우선.** 모든 정리 요청은 dry-run으로 삭제 대상 목록과 회수 예상 용량을 먼저 반환한다. UI는 이를 표시하고 확인받은 뒤 실제 실행한다.
2. **실행 중 잡 거부.** `state`가 `running`이면 거부한다. 먼저 취소해야 한다.
3. **미내보내기 경고.** `exported`가 없는 잡에 `all`을 요청하면 거부하고, 명시적 `force`가 있을 때만 진행한다. 결과 회수 전 삭제 사고를 막는다.
4. **경로 제한.** 삭제 대상 경로를 정규화해 `/work/jobs/<jobId>` 하위인지 확인한다. 심볼릭 링크는 따라가지 않는다.
5. **자동 삭제 없음.** 보관 기간 만료 자동 삭제는 두지 않는다. 일괄 정리도 사용자가 `olderThanDays`를 지정해 명시적으로 실행한다.

## 10. 모듈 구조

```
seg_and_mesh/
  io/          입력 판별, ZIP 안전 해제, dcm2niix 래핑
  segment/     FastSurfer 컨테이너 실행 (engine 인터페이스 1개)
  labels/      canonical 표 로딩, 정적 LUT, 리맵
  mesh/
    preprocess.py   축 1
    extract.py      축 2 (추출기별 구현)
    postprocess.py  축 3 (스무딩·데시메이션)
    glb.py          GLB 작성
    metrics.py      지표 계산
  jobs/        큐, status.json, 변형 관리, 스토리지 정리, 내보내기
  web/         FastAPI 라우트 + 뷰어 페이지
labels/
  canonical-v1.tsv
  build_from_lut.py
configs/
  canonical-mesh-v1.json   (탐색 확정 후 생성)
```

각 모듈은 파일 경로를 입출력으로 주고받는다. 특히 `mesh/`는 `seg.nii.gz` 하나만 있으면 도커·FastSurfer 없이 로컬에서 전 구간 검증된다. 탐색 작업이 이 모듈에 집중되므로 이 분리가 실질적 이득이다.

`segment/`는 엔진 인터페이스를 하나 두고 FastSurfer를 그 구현체로 둔다. 현시점 구현체는 하나뿐이다. 비-T1 입력이 필요해지면 SynthSeg(contrast/resolution 비의존) 같은 엔진을 후보로 검토하되, 라벨 수·피질 디테일이 약해 현재 라벨 계약을 만족하지 못하므로 지금은 도입하지 않는다.

## 11. 설정

`.env`로 관리한다. 저장소에는 `.env.example`만 둔다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `OUTPUT_DIR` | 없음, 필수 지정 | 호스트 출력 폴더 |
| `FASTSURFER_IMAGE` | 없음, 필수 지정 | FastSurfer 이미지 태그 |
| `FASTSURFER_THREADS` | 8 | CPU 스레드 |
| `MAX_UPLOAD_MB` | 4096 | 업로드 상한 |
| `MAX_EXTRACT_GB` | 20 | ZIP 전개 상한 |
| `WEB_PORT` | 8000 | 로컬 포트 |

`OUTPUT_DIR`은 compose 바인드 마운트 경로가 되므로 호스트마다 다르다. `FASTSURFER_IMAGE`는 GPU 이미지의 특정 버전 태그로 고정하며 실제 태그는 구현 시점에 레지스트리에서 확인한다. `latest`는 쓰지 않는다.

## 12. 에러 처리

- 단계별 실패 시 `status.json`의 `error`에 실패 단계, 명령줄, 표준에러 마지막 부분을 기록하고 웹에 표시한다.
- 각 단계는 선행 산출물의 존재와 크기를 확인한 뒤 시작한다.
- 잡 취소는 실행 중인 컨테이너 종료로 처리한다.
- 변형 생성 실패는 잡 전체를 실패시키지 않는다. 해당 변형 디렉터리에 `log.txt`와 함께 실패를 기록하고 다른 변형은 유지한다.

## 13. 테스트

- **단위**: 입력 판별(각 매직바이트, preamble 없는 DICOM, 깨진 파일), ZIP 안전 해제(경로 순회, 심링크, CP949 이름, 용량 초과), 라벨 LUT(표에 없는 라벨 → 0, 없는 구조가 다른 번호에 영향 없음).
- **메시**: 작은 합성 라벨 볼륨(예: 20³ 안에 구·큐브·얇은 판 각 1개)으로 전 축 조합을 검증한다. 노드명이 `label_<id>`인지, `metrics.json`의 `voxelVolumeMm3`가 voxel count와 일치하는지, 해석적으로 부피를 아는 구에 대해 `meshVolumeMm3` 오차가 허용 범위인지 확인한다.
- **회귀**: 동일 입력에 대해 `labelId` ↔ `name` 매핑이 케이스 간 동일한지 검사한다. 라벨 번호 밀림이 재발하지 않도록 한다.
- **스토리지**: 각 `scope`가 남겨야 할 디렉터리를 남기는지, `slim` 후 변형 생성이 실제로 가능한지 확인한다.
- FastSurfer는 목으로 대체한다. 실행이 무거워 CI에 넣지 않는다. 실제 실행을 포함한 수동 스모크 절차를 문서화한다.

## 14. 확정된 설계 판단 요약

- **라벨 번호는 정적 표로 고정.** 입력별 `np.unique` 재번호는 케이스마다 번호가 밀려 조용히 틀린다.
- **라벨 매칭은 숫자 `fs_id`.** 이름 문자열은 LUT 버전에 깨진다.
- **세그 입력은 `withCC` 판.** 뇌량 5개 라벨 포함.
- **볼륨은 uint8.** 무손실이면서 float32의 1/4.
- **NIfTI 헤더는 원본 복사 후 dtype만 교체.**
- **메시 파라미터는 실측 비교 후 확정.** 지표(특히 `volumeErrorPct`)를 근거로 남긴다.
- **현행 방식(라벨별 marching cubes)을 비교 기준선으로 포함.**
- **GLB 직접 작성, 노드명 확정.**
- **압축은 파이프라인 밖 별도 스텝.**
- **FastSurfer 트리거는 docker socket**(로컬 단독·비노출 전제).

## 15. 추후 과제

- 온라인 배포용 meshopt/draco 압축 패키징 스크립트 (6.6)
- CerebNet 소뇌 세분할, HypVINN 시상하부 subfields 옵션
- 비-T1 입력이 필요해질 경우 SynthSeg 엔진 추가
