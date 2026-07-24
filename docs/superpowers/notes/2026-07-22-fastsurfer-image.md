# FastSurfer 이미지 확인 결과

- 확인일: 2026-07-22
- 태그: `deepmi/fastsurfer:cuda-v2.5.4` (`latest`는 쓰지 않는다 — 스펙 §6.4)
- 이미지 빌드: `2.5.4+cdfccea (stable)`, torch `2.7.1+cu128`, Python 3.12
- 압축 크기 5.17GB / 로컬 15.4GB
- 호스트: RTX 3070 8GB, 드라이버 596.36 → CUDA 12.8 판 사용 가능

## 1. 태그 명명이 바뀌었다 (계획 정정)

계획 초안은 `gpu-*`로 태그를 걸렀다. 그러면 **3년 묵은 태그를 고르게 된다.**

| 계열 | 최신 | 비고 |
|---|---|---|
| `gpu-v*` | `gpu-v2.2.0` | 사실상 폐기 |
| `cuda-v*` | `cuda-v2.5.4` | 현재 GPU 판. 별칭 `gpu-latest`, `latest` |
| `cu118-` / `cu126-` / `cu128-` | `-v2.5.4` | CUDA 버전 고정 변형. `cuda-`는 `cu128-`과 동일 크기 |
| `rocm-v*` / `rocm6.3-v*` | `-v2.5.4` | AMD |
| `cpu-v*` | `cpu-v2.5.4` | CPU 폴백(스펙 §6.4). 1.73GB |

태그가 61개라 Docker Hub API 페이지네이션이 필요하다. 재현 명령은
`docs/superpowers/plans/2026-07-22-io-subsystem.md`의 Task 8 Step 1에 있다.

## 2. LUT 추출

이미지 안에 `FreeSurferColorLUT.txt`가 두 벌 있다.

| 경로 | 채택 |
|---|---|
| `/fastsurfer/FastSurferCNN/config/FreeSurferColorLUT.txt` | **이쪽** — FastSurfer가 직접 쓰는 사본 |
| `/opt/freesurfer/FreeSurferColorLUT.txt` | 참고. 정렬 후 443줄 차이 |

두 사본은 전체로는 다르지만 **우리가 쓰는 라벨(17, 53, 251, 255, 1001, 2035)은
동일하다.** 차이는 우리 라벨 집합 밖이다.

```powershell
docker create --name sam-lut deepmi/fastsurfer:cuda-v2.5.4
docker cp sam-lut:/fastsurfer/FastSurferCNN/config/FreeSurferColorLUT.txt labels/FreeSurferColorLUT.txt
docker rm sam-lut
```

- 추출 위치: `labels/FreeSurferColorLUT.txt` — **gitignore 대상, 커밋하지 않는다**
- 줄 수: 2006

`/fastsurfer/FastSurferCNN/config/FastSurfer_ColorLUT.tsv`(80줄)도 있으나 aseg
계열만 담고 DKT 피질 라벨이 없다. 스펙 §3이 요구하는 전체 집합을 못 채우므로
쓰지 않는다.

### 라벨 검증

| 항목 | 결과 |
|---|---|
| 해마 `17 Left-Hippocampus` / `53 Right-Hippocampus` | 있음. 색 `220 216 20` — 스펙 §2.2 예시 JSON과 일치 |
| 뇌량 `251 CC_Posterior` ~ `255 CC_Anterior` | 5개 모두 있음 |
| DKT 피질 `1001–1035`, `2001–2035` | 70개 (반구당 35개) |

## 3. 스펙 가정 검증 (이미지가 있는 김에)

### 3.1 seg 파일명 — 스펙이 맞다

스펙 §3은 `aparc.DKTatlas+aseg.deep.withCC.mgz`를 쓴다고 못박았다. 소스에서 이
문자열을 그대로 grep하면 **히트가 0이라 스펙이 틀린 것처럼 보인다.** 아니다.
런타임에 조립된다.

```
run_fastsurfer.sh:46    asegdkt_segfile_default="$SUBJECTS_DIR/$SID/mri/aparc.DKTatlas+aseg.deep.mgz"
run_fastsurfer.sh:1291  asegdkt_withcc_segfile="$(add_file_suffix "$asegdkt_segfile" "withCC")"
recon_surf/functions.sh:344  # add_file_suffix /path/to/file.nii.gz suffix -> /path/to/file.suffix.nii.gz
```

`aparc.DKTatlas+aseg.deep.mgz` + `withCC` → `aparc.DKTatlas+aseg.deep.withCC.mgz`.
**스펙 §3 그대로다.**

### 3.2 뇌량은 이제 별도 모듈이다 — 주의할 플래그가 하나 늘었다

2.5.x에서 CC는 `CorpusCallosum/fastsurfer_cc.py`(FastSurferCC)라는 전용 단계가
맡고, 그 결과를 `paint_cc_into_pred.py`가 asegdkt에 칠해 넣어 withCC 파일을
만든다.

- 기본값 `run_cc_module="true"` (`run_fastsurfer.sh:76`) — 켜져 있다
- **`--no_cc`를 주면 withCC 파일이 아예 안 생긴다** (`run_fastsurfer.sh:230, 501`)

segment 계획에서 `--no_cc`를 절대 붙이지 말 것. 붙이면 뇌량 5개 라벨이 조용히
사라지고, 그건 스펙 §14가 "확정된 판단"으로 못박은 항목이다.

### 3.3 CLI 플래그

스펙 §6.4가 쓰는 플래그는 2.5.4에 전부 있다: `--t1`, `--sd`, `--sid`,
`--seg_only`, `--vox_size`, `--threads`.

### 3.4 FreeSurfer 라이선스

`run_fastsurfer.sh:363` 주석이 라이선스는 **surface 파이프라인에만** 필요하다고
명시한다. `--seg_only`면 불필요하다는 스펙 §6.4 판단이 유지된다.

## 4. 다음 계획에서 쓰는 곳

`labels/build_from_lut.py`가 `labels/FreeSurferColorLUT.txt`를 읽어
`labels/canonical-v1.tsv`를 생성한다. 생성 결과물(tsv)만 저장소에 커밋하고,
런타임에는 LUT를 읽지 않는다 (스펙 §3).

## 5. 미확인 (실데이터 필요)

실제 세그멘테이션은 돌리지 않았다. 다음은 T1 볼륨이 생기면 확인할 것.

- `--seg_only`로 실제 `aparc.DKTatlas+aseg.deep.withCC.mgz`가 생성되는지
- RTX 3070 8GB에서 VRAM이 충분한지 (스펙 §6.4가 부족 시 CPU 폴백 안내를 요구)
- `orig.mgz`가 conform 공간 256³ 1mm uint8로 나오는지 (스펙 §2.1)
