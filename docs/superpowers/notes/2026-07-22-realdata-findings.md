# 실데이터 검증 결과 (io 서브시스템)

- 확인일: 2026-07-22
- 데이터: `test-asset/<스터디>` (폴더) 및 동일 스터디의 `.zip` (157MB)
- 장비: Philips Achieva 3T, dcm2niix v1.0.20211006으로 일부 시리즈가 이미 변환돼 있음

## 0. 개인정보 — 즉시 조치함

`test-asset/`가 `.gitignore`에 없었다. 무시 규칙을 추가했다.

**파일명 자체에 환자 실명이 들어 있다**: `MRS/<환자실명>_WIP_SV_PRESS_...SDAT`.
(실명 자체는 여기에 적지 않는다 — 이 저장소는 공개다.)
DICOM 헤더에도 PHI가 있다고 봐야 한다. 따라오는 요구사항:

- `test-asset/`를 커밋하지 않는다 (규칙 추가 완료)
- 스펙 §12 `status.json.error`가 원본 파일명이나 DICOM 태그를 그대로 싣지
  않게 한다 — 에러 메시지로 PHI가 샌다
- 뷰어/로그도 동일

## 1. 세 입력 경로 모두 실데이터에서 동작한다

| 경로 | 결과 | 시간 |
|---|---|---|
| 디렉터리 | `dicom-dir`, DICOM 36개 | 6.2s |
| ZIP (157MB) | `dicom-zip`, DICOM 36개 | 8.6s |
| 기존 `.nii.gz` | `nifti`, `input.nii.gz`로 복사 | 즉시 |

ZIP 경로와 디렉터리 경로의 **파일 이름 집합이 완전히 동일**하다. `safe_extract`는
157MB / 84엔트리를 거부 없이 통과했다.

`detect_format`이 UNKNOWN으로 분류한 26개는 전부 실제로 비DICOM이다 (JSON
사이드카, JPG, MRS 분광 SDAT/SPAR, LCModel 출력). **오분류 0건.**

## 2. Philips enhanced multi-frame DICOM — 시리즈당 파일 1개

```
MPRAGE/DICOM/IM_0001   12.6MB   ← 시리즈 전체가 이 파일 하나
MPRAGE/DICOM/XX_0002            ← Philips 사설 (비영상)
MPRAGE/DICOMDIR
T1 SE SAG/DICOM/PS_0002         ← Presentation State (비영상)
```

"파일 수 = 슬라이스 수"라는 흔한 가정이 여기선 틀린다. 우리 코드는 파일 수를
쓰지 않으므로 지금은 무해하지만, 앞으로 슬라이스 수는 **반드시 dcm2niix 출력이나
NIfTI 헤더에서** 얻어야 한다. `DICOMDIR`도 DICOM으로 분류된다 — 맞는 분류이고
dcm2niix가 알아서 무시한다.

## 3. 시리즈 순위 — 실전 함정이 그대로 들어 있다

이 스터디는 `series.py` 순위를 시험하기 좋다.

| 시리즈 | 성격 |
|---|---|
| `MPRAGE_SENSE2` | **정답.** `MRAcquisitionType: 3D`, `ImageType: [...,"T1",...]` |
| `T1 SE SAG` | **함정.** 설명이 `t1` 패턴에 걸리지만 2D SE 시상면 |
| `T2 TSE AX/COR/SAG`, `DTI`, `Resting State` | 탈락 |

주의: MPRAGE 사이드카가 `SliceThickness: 1.2`, `SpacingBetweenSlices: 1.2`다.
**1mm 등방이 아니다.** 스펙 §6.3의 "1mm 근접 +2" 가점 허용 오차가 1.2mm를
포함하는지 실제 순위로 확인해야 한다 (dcm2niix 필요).

## 4. 사이드카 명명 가정 — 확인됨

`<타임스탬프>_WIP_MPRAGE_SENSE2_SENSE_901.nii.gz` 옆에 같은 stem의 `.json`이
있다. `<stem>.json` 가정이 실제 Philips 출력에서 성립한다.

덤으로: `ProtocolName`이 `"WIP MPRAGE_SENSE2 SENSE"`인데 파일명에서는 공백이
언더스코어가 됐다. **dcm2niix가 파일명을 정규화한다.** 다만 우리 설정은
`-f %s_%d`(SeriesNumber_SeriesDescription)라 이 파일과 패턴이 다르므로,
슬래시·점·비ASCII가 든 `SeriesDescription`은 여전히 미확인이다.

## 5. 발견한 결함 2건 (io 후속 브랜치에서 고칠 것)

### 5.1 `detect_format` 성능 — 비DICOM 파일당 0.15~0.5초

매직바이트로 걸러지는 DICOM은 빠르다. 느린 쪽은 **UNKNOWN으로 끝나는 파일**이다.
pydicom 폴백이 파일을 통째로 파싱하려 들기 때문이다.

```
0.49s  MRS/<환자실명>_raw_act.SDAT
0.43s  MRS/output2/coord
0.34s  MRS/output/spreadsheet.csv
```

64개 스터디에서 6.2초. 잡동사니 파일이 수천 개인 스터디면 수 분이 된다.
폴백에 크기 상한이나 `stop_before_pixels` 같은 제동이 필요하다.

### 5.2 pydicom `UserWarning`이 호출자에게 샌다

7개 파일에서 `UserWarning: Expected implicit VR, but found explicit VR`가
`detect_format` 밖으로 올라온다.

`-W error`(테스트 스위트 설정)에서는 이 경고가 예외가 되고, `detect_format`의
넓은 except가 그걸 삼켜 **UNKNOWN을 돌려준다.** 이번 데이터에선 해당 파일들이
어차피 비DICOM이라 결과가 같았다(양쪽 다 DICOM 36개). 하지만 이 경고를 내는
**진짜 DICOM**이 오면 테스트에선 UNKNOWN, 운영에선 DICOM으로 갈린다.
폴백 안에서 경고를 눌러야 한다.

## 6. 아직 미확인 (dcm2niix 바이너리 필요)

- 실제 변환: `run_dcm2niix`가 이 스터디에서 몇 개 시리즈를 뽑는지
- `describe_nifti`가 multi-frame에서 슬라이스 수·복셀 크기를 제대로 읽는지
- `rank_series`에서 `MPRAGE_SENSE2`가 1위이고 `T1 SE SAG`이 아닌지
- `-f %s_%d` 파일명과 사이드카 짝짓기
- FastSurfer `--seg_only` 실행 (스펙 §6.4)
