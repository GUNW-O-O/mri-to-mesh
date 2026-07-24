# io 실데이터 보강 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 Philips Achieva 스터디로 io 서브시스템을 돌려 드러난 결함 3건을 고치고, 그 스터디를 회귀 테스트로 고정한다.

**Architecture:** 새 모듈을 만들지 않는다. `detect.py`의 pydicom 폴백, `dcm2niix.py`의 슬라이스 축 계산과 사이드카 짝짓기를 고치고, 실데이터 회귀 테스트 파일 하나를 추가한다. 마지막으로 dcm2niix가 든 컨테이너 이미지를 두어 `dcm2niix` 표시 테스트가 실제로 돌 수 있게 한다.

**Tech Stack:** Python 3.11, pydicom 3.x, nibabel 5.x, pytest, Docker (debian:bookworm-slim + dcm2niix 1.0.20220720)

## 근거 — 실측 결과

전부 `docs/superpowers/notes/2026-07-22-realdata-findings.md`와 이 계획을 쓰며 직접 측정한 값이다. 추정이 아니다.

| 관측 | 값 |
|---|---|
| 폴백 도달 파일 26개 분류 시간 | **6.22s** (파일당 0.15~0.5s, 파일은 전부 0.3MB 이하) |
| 같은 26개, `specific_tags` 적용 | **0.01s**, 판정 전부 동일 |
| MPRAGE NIfTI 실제 shape | `(170, 288, 288)`, zooms `(1.2, 0.89, 0.89)` |
| 같은 볼륨에 대해 현재 코드가 보고하는 슬라이스 수 | **288** (정답 170) |
| 사이드카 없는 출력 | `1101_32DIR_3mm_1NSA_ADC.nii.gz` (짝 `.json` 없음) |
| pydicom `UserWarning`을 새는 파일 | 7개 |

## Global Constraints

- 스펙 §6.1: 확장자를 신뢰하지 않는다. 매직바이트로 판별한다.
- 스펙 §6.3: T1 후보는 **자동 추천만** 한다. 자동 확정하지 않는다. `series.py`에 자동 선택 헬퍼를 만들지 않는다.
- 스펙 §6.3 자동 추천 기준 문구 그대로: "등방성에 가까운 voxel(약 1mm), 슬라이스 128장 이상, description이 `mprage|t1|bravo|spgr|tfl` 매칭".
- 스펙 §6.3 dcm2niix 호출 플래그는 바꾸지 않는다: `-d 5 -z y -b y -f %s_%d -o`.
- `seg_and_mesh/io/__init__.py`의 `__all__` 18개 이름과 `SourceKind` 값(`"dicom-zip"`, `"dicom-dir"`, `"nifti"`)은 스펙 §9.1 `status.json` 계약이다. 이 계획에서 바꾸지 않는다.
- `test-asset/`는 `.gitignore` 대상이다. 파일명에 환자 실명이 들어 있다. 테스트 코드·픽스처·커밋 메시지 어디에도 실제 파일명이나 환자 식별자를 적지 않는다.
- 테스트는 `uv run pytest -W error`로 통과해야 한다. `pyproject.toml`의 `addopts`는 빈 문자열이다 — `-q`를 다시 넣지 않는다.
- 새 런타임 의존성을 추가하지 않는다. 현재 의존성은 `pydicom>=3.0,<4`, `nibabel>=5.2,<6`, `numpy>=2.4.6,<3`뿐이다.

## 범위 밖 (의도적으로 제외)

- **PHI 마스킹.** 예외 메시지에 원본 경로가 그대로 들어간다. 진짜 누출 지점은 스펙 §12의 `status.json.error`와 웹 표시인데 둘 다 아직 없다. `segment + jobs` 계획에서 `status.json`을 만들 때 함께 처리한다.
- `prepare_input`이 디렉터리 입력에서 `workdir`를 쓰지 않는 문제 (스펙 §9.2 용량 집계). `segment + jobs` 계획 소관.
- FastAPI·compose·뷰어. `web` 계획 소관.

---

## File Structure

| 파일 | 책임 | 이 계획에서 |
|---|---|---|
| `seg_and_mesh/io/detect.py` | 매직바이트 판별 + pydicom 폴백 | 폴백을 `specific_tags`로 제동, 경고 차단 (Task 1) |
| `seg_and_mesh/io/dcm2niix.py` | dcm2niix 래핑, NIfTI 메타 추출 | 슬라이스 축 계산 (Task 2), 사이드카 없는 출력 보정 (Task 3) |
| `tests/io/test_detect.py` | 판별 테스트 | Task 1 테스트 추가 |
| `tests/io/test_dcm2niix.py` | 변환·메타 테스트 | Task 2, 3 테스트 추가 |
| `tests/io/test_realdata.py` | **신규.** 실제 스터디 회귀 | Task 4 |
| `docker/api.Dockerfile` | **신규.** dcm2niix가 든 실행/테스트 이미지 | Task 5 |
| `docker/README.md` | **신규.** 컨테이너에서 테스트 돌리는 법 | Task 5 |

---

## Task 1: pydicom 폴백 제동 — 성능과 경고 누출

DICOM이 아닌 파일 하나를 판별하는 데 0.15~0.5초가 걸린다. 파일이 작아도 그렇다. `force=True`로 쓰레기 바이트를 읽으면 pydicom이 엉뚱한 값을 element 길이로 해석해 거대한 값을 통째로 물화하기 때문이다. 필요한 태그는 `SOPClassUID`, `Rows`, `Columns` 셋뿐이므로 `specific_tags`로 그 셋만 읽게 하면 값 물화가 사라진다.

동시에 pydicom `UserWarning`이 호출자에게 샌다. `-W error`에서는 이 경고가 예외가 되고 `except Exception`이 그걸 삼켜 UNKNOWN을 돌려준다. 즉 **같은 파일이 테스트에서는 UNKNOWN, 운영에서는 DICOM**이 될 수 있다.

**Files:**
- Modify: `seg_and_mesh/io/detect.py:91-103` (`_is_dicom_fallback`)
- Test: `tests/io/test_detect.py`

**Interfaces:**
- Consumes: 없음 (기존 모듈 내부 수정)
- Produces: `detect_format(path) -> InputKind` 동작은 그대로. 공개 시그니처 변경 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/io/test_detect.py` 맨 아래에 추가한다.

```python
def test_fallback_does_not_leak_pydicom_warnings(tmp_path, monkeypatch, recwarn):
    """폴백이 pydicom 경고를 호출자에게 흘리면 안 된다.

    -W error에서는 이 경고가 예외가 되고 폴백의 except Exception이 그것을
    삼켜 UNKNOWN을 돌려준다. 그러면 같은 파일이 테스트에서는 UNKNOWN,
    운영에서는 DICOM으로 갈린다. 실제 Philips 스터디에서 이 경고를 내는
    파일이 7개 나왔다.

    경고를 내는 실물 파일을 픽스처로 만들려고 하지 말 것 — pydicom이 어떤
    바이트에서 이 경고를 내는지는 내부 휴리스틱에 달려 있어 재현이 불안정하다.
    계획 작성 중 Dataset(implicit_vr=False)로 만들어 봤으나 경고가 나지
    않았다. dcmread를 감싸서 경고를 확실히 발생시키고, 그것이 detect_format
    밖으로 나오는지만 본다.
    """
    import warnings

    import pydicom

    real_dcmread = pydicom.dcmread

    def warning_dcmread(*args, **kwargs):
        warnings.warn("Expected implicit VR, but found explicit VR", UserWarning)
        return real_dcmread(*args, **kwargs)

    monkeypatch.setattr(pydicom, "dcmread", warning_dcmread)

    path = tmp_path / "garbage"
    path.write_bytes(b"\xde\xad\xbe\xef" * 500)

    detect_format(path)

    assert [str(w.message) for w in recwarn.list] == [], (
        "폴백 밖으로 경고가 샜다"
    )


def test_fallback_reads_only_the_three_decision_tags(tmp_path, monkeypatch):
    """폴백은 SOPClassUID / Rows / Columns만 읽어야 한다.

    이 셋만 읽으면 pydicom이 나머지 element의 값을 물화하지 않는다.
    쓰레기 바이트를 거대한 element 길이로 오해해 통째로 읽어들이는 것이
    파일당 0.15~0.5초를 먹던 원인이었다(실측: 26개 파일 6.22s → 0.01s).
    """
    import pydicom
    from pydicom.tag import Tag

    seen: dict = {}
    real = pydicom.dcmread

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(pydicom, "dcmread", spy)

    path = tmp_path / "garbage"
    path.write_bytes(b"\xde\xad\xbe\xef" * 500)
    detect_format(path)

    assert seen.get("specific_tags") == [
        Tag(0x0008, 0x0016),
        Tag(0x0028, 0x0010),
        Tag(0x0028, 0x0011),
    ], f"specific_tags가 지정되지 않았다: {seen}"
    assert seen.get("stop_before_pixels") is True
```

- [ ] **Step 2: 실패를 확인한다**

```
uv run pytest tests/io/test_detect.py::test_fallback_does_not_leak_pydicom_warnings tests/io/test_detect.py::test_fallback_reads_only_the_three_decision_tags -v
```

기대: 둘 다 FAIL. 첫 번째는 `폴백 밖으로 경고가 샜다`, 두 번째는 `specific_tags가 지정되지 않았다: {'force': True, 'stop_before_pixels': True}`.

(첫 번째 FAIL은 계획 작성 중 실제로 재현했다 — 현재 코드에서 `kind=UNKNOWN`, 샌 경고 `['Expected implicit VR, but found explicit VR']`.)

- [ ] **Step 3: 구현한다**

`seg_and_mesh/io/detect.py`의 import 블록에 두 줄을 더한다.

```python
import gzip
import struct
import warnings
import zlib
from enum import Enum
from pathlib import Path

import pydicom
from pydicom.tag import Tag
```

`_DICOM_MAGIC = b"DICM"` 아래에 상수를 더한다.

```python
#: 폴백 판정에 필요한 태그. 이 셋만 읽어 pydicom이 나머지 element의 값을
#: 물화하지 않게 한다. force=True로 쓰레기 바이트를 읽으면 pydicom이 엉뚱한
#: 값을 element 길이로 해석해 거대한 값을 통째로 읽어들이는데, 그게 파일당
#: 0.15~0.5초를 먹던 원인이었다(실측: 26개 파일 6.22s → 0.01s, 판정 동일).
_FALLBACK_TAGS = [
    Tag(0x0008, 0x0016),  # SOPClassUID
    Tag(0x0028, 0x0010),  # Rows
    Tag(0x0028, 0x0011),  # Columns
]
```

`_is_dicom_fallback`을 통째로 바꾼다.

```python
def _is_dicom_fallback(path: Path) -> bool:
    """preamble 없는 DICOM을 pydicom으로 판별한다.

    force=True는 임의 바이너리에도 빈 Dataset을 돌려주므로, 실제 DICOM 태그가
    있는지를 반드시 확인해야 한다.

    pydicom이 내는 경고는 여기서 삼킨다. 밖으로 흘리면 -W error 환경에서
    경고가 예외가 되고 아래 except Exception이 그것을 삼켜, 같은 파일이
    테스트에서는 UNKNOWN, 운영에서는 DICOM으로 갈린다.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = pydicom.dcmread(
                path,
                force=True,
                stop_before_pixels=True,
                specific_tags=_FALLBACK_TAGS,
            )
    except Exception:
        return False
    if "SOPClassUID" in ds:
        return True
    return "Rows" in ds and "Columns" in ds
```

- [ ] **Step 4: 통과를 확인한다**

```
uv run pytest tests/io/test_detect.py -v
```

기대: 전부 PASS. 특히 기존 `test_dicom_without_preamble`이 계속 PASS해야 한다 — `specific_tags`가 preamble 없는 진짜 DICOM 판별을 깨지 않는다는 증거다. 이 테스트가 깨지면 `specific_tags` 접근 자체가 틀린 것이므로 되돌리고 보고한다.

- [ ] **Step 5: 전체 스위트로 회귀를 확인한다**

```
uv run pytest -W error
```

기대: 이전과 같은 통과 수, 실패 0.

- [ ] **Step 6: 커밋한다**

```bash
git add seg_and_mesh/io/detect.py tests/io/test_detect.py
git commit -m "fix(io): pydicom 폴백을 specific_tags로 제동하고 경고 누출을 막는다"
```

---

## Task 2: 슬라이스 축을 voxel 간격으로 고른다

`describe_nifti`는 `slices = shape[2]`로 슬라이스 수를 센다. 3번째 축이 슬라이스 축이라는 가정인데, 실제 MPRAGE에서 틀렸다.

```
901_MPRAGE_SENSE2.nii.gz   shape=(170, 288, 288)   zooms=(1.2, 0.89, 0.89)
```

슬라이스 축은 인덱스 **0**(간격 1.2mm, 170장)인데 현재 코드는 288을 보고한다. 스펙 §6.3은 시리즈 목록에 슬라이스 수를 표시하라고 요구하므로 사용자가 틀린 숫자를 본다.

올바른 기준은 **관통면(through-plane) 방향**, 즉 표본 간격이 가장 성긴 축이다. 등방성이면 관통면 축이라는 개념 자체가 없으므로 어느 축을 골라도 같은 뜻이고, 그때는 인덱스 2로 정한다.

실제 데이터 9개 볼륨 전부에서 이 규칙이 맞는 답을 낸다.

| 볼륨 | zooms | 고르는 축 | 슬라이스 |
|---|---|---|---|
| MPRAGE | (1.2, 0.89, 0.89) | 0 | 170 |
| T1W SE SAG | (0.45, 0.45, 5.0) | 2 | 24 |
| T2 TSE AX/COR/SAG | (0.4x, 0.4x, 5.0) | 2 | 30/36/36 |
| DTI | (1.0, 1.0, 3.0) | 2 | 50 |
| Resting fMRI | (3.31, 3.31, 3.31) 등방 | 2 (동점) | 48 |

**Files:**
- Modify: `seg_and_mesh/io/dcm2niix.py:66-112` (`describe_nifti`)
- Test: `tests/io/test_dcm2niix.py`

**Interfaces:**
- Consumes: 없음
- Produces: `SeriesOutput.slices`의 의미가 "3번째 축 길이"에서 "관통면 축 길이"로 바뀐다. 필드 이름·타입은 그대로 `slices: int`. `series.py`의 `_MIN_SLICES = 128` 비교는 수정하지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/io/test_dcm2niix.py` 맨 아래에 추가한다. 이 파일에는 이미 NIfTI를 만드는 헬퍼가 있을 수 있으나, 이 테스트는 자기 완결적으로 쓴다.

```python
def _write_nifti(path, shape, zooms):
    """주어진 shape·voxel 간격을 갖는 최소 NIfTI를 만든다."""
    import nibabel as nib
    import numpy as np

    data = np.zeros(shape, dtype=np.int16)
    affine = np.diag([zooms[0], zooms[1], zooms[2], 1.0])
    img = nib.Nifti1Image(data, affine)
    img.header.set_zooms(zooms)
    nib.save(img, path)
    return path


def test_slices_uses_coarsest_axis_not_third_axis(tmp_path):
    """슬라이스 축은 표본 간격이 가장 성긴 축이다.

    실제 Philips MPRAGE가 shape=(170, 288, 288), zooms=(1.2, 0.89, 0.89)로
    나온다. 슬라이스 축은 인덱스 0이고 정답은 170장인데, shape[2]를 쓰면
    288을 보고한다. 스펙 §6.3은 이 숫자를 사용자에게 표시하라고 요구한다.
    """
    path = _write_nifti(tmp_path / "mprage.nii.gz", (170, 288, 288), (1.2, 0.89, 0.89))

    out = describe_nifti(path, None)

    assert out.slices == 170


def test_slices_still_uses_third_axis_for_conventional_stack(tmp_path):
    """관통면이 3번째 축인 흔한 2D 스택에서는 동작이 그대로여야 한다.

    실제 T1W SE SAG: shape=(512, 512, 24), zooms=(0.45, 0.45, 5.0).
    """
    path = _write_nifti(tmp_path / "se.nii.gz", (512, 512, 24), (0.45, 0.45, 5.0))

    out = describe_nifti(path, None)

    assert out.slices == 24


def test_slices_falls_back_to_third_axis_when_isotropic(tmp_path):
    """등방성이면 관통면 축이라는 개념이 없다. 인덱스 2로 정한다.

    실제 Resting state fMRI: shape=(64, 64, 48), zooms=(3.31, 3.31, 3.31).
    """
    path = _write_nifti(tmp_path / "iso.nii.gz", (64, 64, 48), (3.31, 3.31, 3.31))

    out = describe_nifti(path, None)

    assert out.slices == 48
```

- [ ] **Step 2: 실패를 확인한다**

```
uv run pytest tests/io/test_dcm2niix.py::test_slices_uses_coarsest_axis_not_third_axis -v
```

기대: FAIL, `assert 288 == 170`.

나머지 둘은 현재 코드에서도 PASS한다. 회귀 방지용이므로 정상이다.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/io/dcm2niix.py`의 `_FILENAME_PATTERN` 아래에 상수를 더한다.

```python
#: 등방성 판정 — 최대/최소 voxel 변 길이 비가 이 값 이하면 관통면 축이 없다고 본다.
#: series._MAX_ISOTROPY_RATIO와 같은 값이지만 의도적으로 따로 둔다. 이쪽은
#: "슬라이스 축을 못 고르는 경우"를 판정하고, 저쪽은 "T1 후보 가점"을 판정한다.
_ISOTROPY_RATIO = 1.2
```

`describe_nifti` 위에 헬퍼를 추가한다.

```python
def _slice_axis(zooms: tuple[float, float, float]) -> int:
    """슬라이스 축 인덱스를 고른다 — 표본 간격이 가장 성긴 축.

    dcm2niix는 볼륨을 재정렬해서 쓰므로 슬라이스 축이 항상 인덱스 2에
    오지 않는다. 실제 Philips MPRAGE는 shape=(170, 288, 288),
    zooms=(1.2, 0.89, 0.89)로 나오며 슬라이스 축이 인덱스 0이다.

    등방성이면 관통면 축이라는 개념이 없으므로 인덱스 2로 정한다.
    간격이 0이거나 음수인 축은 헤더가 불완전한 것이므로 후보에서 뺀다.
    """
    valid = [(z, i) for i, z in enumerate(zooms) if z > 0]
    if len(valid) < 3:
        return 2
    largest = max(z for z, _ in valid)
    smallest = min(z for z, _ in valid)
    if largest / smallest <= _ISOTROPY_RATIO:
        return 2
    # 동점이면 인덱스가 작은 쪽 — max()가 먼저 나온 원소를 유지한다.
    return max(valid, key=lambda zi: (zi[0], -zi[1]))[1]
```

`describe_nifti` 안에서 슬라이스를 세는 두 줄을 바꾼다. 현재:

```python
    zooms = tuple(float(z) for z in raw_zooms[:3])
    shape = img.shape
    slices = int(shape[2]) if len(shape) >= 3 else 0
```

바꾼 뒤:

```python
    zooms = tuple(float(z) for z in raw_zooms[:3])
    shape = img.shape
    slices = int(shape[_slice_axis(zooms)]) if len(shape) >= 3 else 0
```

`describe_nifti`의 docstring에 한 줄을 더한다. 현재 `"""NIfTI 헤더와 사이드카에서 시리즈 선택에 필요한 정보를 뽑는다.` 바로 다음 빈 줄 아래에 넣는다.

```
    slices는 관통면 축(표본 간격이 가장 성긴 축)의 길이다. dcm2niix가 볼륨을
    재정렬하므로 그 축이 항상 인덱스 2는 아니다 — _slice_axis 참고.
```

- [ ] **Step 4: 통과를 확인한다**

```
uv run pytest tests/io/test_dcm2niix.py -v
```

기대: 전부 PASS. 기존 테스트가 `slices`를 단언하고 있었다면 그 값이 여전히 맞는지 확인한다. 값이 바뀌었다면 그 픽스처의 zooms가 등방이 아니고 관통면 축이 2가 아니었다는 뜻이므로, **테스트를 고치지 말고 멈춰서 보고한다.**

- [ ] **Step 5: 전체 스위트로 회귀를 확인한다**

```
uv run pytest -W error
```

기대: 실패 0.

- [ ] **Step 6: 커밋한다**

```bash
git add seg_and_mesh/io/dcm2niix.py tests/io/test_dcm2niix.py
git commit -m "fix(io): 슬라이스 수를 3번째 축이 아니라 관통면 축에서 센다"
```

---

## Task 3: 사이드카 없는 파생 볼륨에 이름을 준다

dcm2niix는 DTI에서 ADC 볼륨을 파생시키며 파일명에 `_ADC`를 붙이는데, **사이드카 JSON은 만들지 않는다.**

```
1101_32DIR_3mm_1NSA.nii.gz      1101_32DIR_3mm_1NSA.json     ← 짝 있음
1101_32DIR_3mm_1NSA_ADC.nii.gz  (없음)                        ← 짝 없음
```

`_collect_outputs`는 `sidecar.exists()`를 확인하므로 죽지는 않는다. 대신 `series_description=""`, `series_number=None`인 **이름 없는 항목**이 목록에 들어간다. 스펙 §6.3은 목록에 SeriesDescription을 표시하라고 요구하는데 빈 문자열이 뜬다.

`_collect_outputs`는 자기가 `-f %s_%d`로 만든 파일만 다루므로 파일명 형식을 안다. 사이드카가 없을 때만 파일명 stem에서 시리즈 번호와 설명을 복구한다. `describe_nifti`는 일반 함수이므로 건드리지 않는다 — 계층을 지킨다.

**Files:**
- Modify: `seg_and_mesh/io/dcm2niix.py:150-159` (`_collect_outputs`)
- Test: `tests/io/test_dcm2niix.py`

**Interfaces:**
- Consumes: `describe_nifti(nifti_path, sidecar_path) -> SeriesOutput` (Task 2에서 수정됨)
- Produces: `run_dcm2niix`가 돌려주는 `SeriesOutput` 중 사이드카가 없는 것의 `series_description`이 빈 문자열이 아니라 파일명에서 뽑은 문자열이 된다. `SeriesOutput` 필드 구성은 그대로 7개.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/io/test_dcm2niix.py` 맨 아래에 추가한다. `_write_nifti`는 Task 2에서 이미 이 파일에 추가했다.

```python
def test_output_without_sidecar_gets_name_from_filename(tmp_path):
    """dcm2niix는 ADC 같은 파생 볼륨에 사이드카를 만들지 않는다.

    실제 출력: 1101_32DIR_3mm_1NSA.nii.gz 에는 짝 .json이 있는데
    1101_32DIR_3mm_1NSA_ADC.nii.gz 에는 없다. 그대로 두면 이름 없는 항목이
    시리즈 목록에 뜬다(스펙 §6.3은 SeriesDescription 표시를 요구한다).
    """
    from seg_and_mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti(
        tmp_path / "1101_32DIR_3mm_1NSA_ADC.nii.gz", (224, 224, 50), (1.0, 1.0, 3.0)
    )

    (out,) = _collect_outputs({path})

    assert out.sidecar_path is None
    assert out.series_description == "32DIR_3mm_1NSA_ADC"
    assert out.series_number == 1101


def test_sidecar_wins_over_filename(tmp_path):
    """사이드카가 있으면 파일명은 쓰지 않는다.

    dcm2niix는 파일명에서 공백을 언더스코어로 바꾸지만 JSON에는 원본
    SeriesDescription을 그대로 넣는다. 실제로 파일 401_T1W_SE_SAG의
    사이드카에는 'T1W_SE SAG'(공백 포함)가 들어 있다.
    """
    import json

    from seg_and_mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti(
        tmp_path / "401_T1W_SE_SAG.nii.gz", (512, 512, 24), (0.45, 0.45, 5.0)
    )
    (tmp_path / "401_T1W_SE_SAG.json").write_text(
        json.dumps({"SeriesNumber": 401, "SeriesDescription": "T1W_SE SAG"}),
        encoding="utf-8",
    )

    (out,) = _collect_outputs({path})

    assert out.series_description == "T1W_SE SAG"
    assert out.series_number == 401


def test_filename_without_leading_series_number(tmp_path):
    """%s_%d 형식이 아닌 파일명이면 설명만 채우고 번호는 None으로 둔다.

    사용자가 직접 올린 NIfTI가 이 경로로 들어올 수 있다. 없는 번호를
    지어내면 안 된다.
    """
    from seg_and_mesh.io.dcm2niix import _collect_outputs

    path = _write_nifti(tmp_path / "input.nii.gz", (64, 64, 48), (1.0, 1.0, 1.0))

    (out,) = _collect_outputs({path})

    assert out.series_number is None
    assert out.series_description == "input"
```

- [ ] **Step 2: 실패를 확인한다**

```
uv run pytest tests/io/test_dcm2niix.py::test_output_without_sidecar_gets_name_from_filename tests/io/test_dcm2niix.py::test_filename_without_leading_series_number -v
```

기대: 둘 다 FAIL. 첫 번째는 `assert '' == '32DIR_3mm_1NSA_ADC'`, 세 번째는 `assert '' == 'input'`.

`test_sidecar_wins_over_filename`은 현재 코드에서도 PASS한다. 회귀 방지용이다.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/io/dcm2niix.py` import 블록에 `dataclasses.replace`와 `re`를 더한다. 현재 `from dataclasses import dataclass`를 다음으로 바꾼다.

```python
import re
from dataclasses import dataclass, replace
```

`_ISOTROPY_RATIO` 아래에 상수를 더한다.

```python
#: %s_%d로 만든 파일명에서 시리즈 번호와 설명을 되찾는 패턴.
#: dcm2niix가 ADC 같은 파생 볼륨에는 사이드카를 만들지 않으므로,
#: 그때만 파일명에서 복구한다.
_STEM_PATTERN = re.compile(r"^(?P<number>\d+)_(?P<description>.+)$")
```

`_collect_outputs`를 통째로 바꾼다.

```python
def _describe_from_stem(stem: str) -> tuple[int | None, str]:
    """%s_%d 파일명 stem에서 (시리즈 번호, 설명)을 되찾는다.

    선두 숫자와 언더스코어가 없으면 번호는 None이고 stem 전체가 설명이다 —
    없는 번호를 지어내지 않는다.
    """
    match = _STEM_PATTERN.match(stem)
    if match is None:
        return None, stem
    return int(match.group("number")), match.group("description")


def _collect_outputs(nifti_paths: set[Path]) -> list[SeriesOutput]:
    """변환 결과 NIfTI들을 SeriesOutput으로 만든다.

    dcm2niix는 파생 볼륨(예: DTI의 ADC)에 사이드카를 만들지 않는다. 그러면
    설명이 빈 문자열인 이름 없는 항목이 시리즈 목록에 뜨는데, 스펙 §6.3은
    목록에 SeriesDescription을 표시하라고 요구한다. 사이드카가 없을 때만
    파일명에서 되찾는다 — 우리가 -f %s_%d로 만든 이름이므로 형식을 안다.
    """
    outputs: list[SeriesOutput] = []
    for nifti_path in sorted(nifti_paths):
        if nifti_path.name.endswith(".nii.gz"):
            stem = nifti_path.name[: -len(".nii.gz")]
        else:
            stem = nifti_path.stem
        sidecar = nifti_path.parent / f"{stem}.json"
        out = describe_nifti(nifti_path, sidecar if sidecar.exists() else None)
        if not out.series_description:
            number, description = _describe_from_stem(stem)
            out = replace(
                out,
                series_description=description,
                series_number=out.series_number if out.series_number is not None else number,
            )
        outputs.append(out)
    return outputs
```

- [ ] **Step 4: 통과를 확인한다**

```
uv run pytest tests/io/test_dcm2niix.py -v
```

기대: 전부 PASS.

- [ ] **Step 5: 전체 스위트로 회귀를 확인한다**

```
uv run pytest -W error
```

기대: 실패 0.

- [ ] **Step 6: 커밋한다**

```bash
git add seg_and_mesh/io/dcm2niix.py tests/io/test_dcm2niix.py
git commit -m "fix(io): 사이드카 없는 파생 볼륨의 이름을 파일명에서 되찾는다"
```

---

## Task 4: 실데이터 회귀 테스트

실제 스터디로 확인한 성질들을 테스트로 고정한다. 데이터가 없으면 skip한다.

`SAM_TEST_DATA_DIR`이 가리키는 폴더 안에 **같은 스터디의 DICOM 폴더 하나와 ZIP 하나**가 있다고 가정한다. 이 레이아웃을 테스트가 직접 찾아낸다 — 특정 파일명을 코드에 박지 않는다(파일명에 환자 실명이 들어 있어 저장소에 남기면 안 된다).

`tests/conftest.py`의 `real_data_dir`, `dcm2niix_bin` fixture를 그대로 쓴다. 새 fixture를 만들지 않는다.

**Files:**
- Create: `tests/io/test_realdata.py`
- Test: 위 파일 자체

**Interfaces:**
- Consumes: `prepare_input(src, workdir) -> PreparedInput`, `SourceKind`, `run_dcm2niix(dicom_dir, out_dir, depth=5, binary=None) -> list[SeriesOutput]`, `rank_series(list[SeriesOutput]) -> list[SeriesCandidate]`, fixture `real_data_dir`, `dcm2niix_bin`
- Produces: 없음 (테스트 전용)

- [ ] **Step 1: 테스트 파일을 쓴다**

`tests/io/test_realdata.py`를 새로 만든다.

```python
"""실제 MRI 스터디 회귀 테스트.

SAM_TEST_DATA_DIR이 가리키는 폴더에 같은 스터디의 DICOM 폴더 하나와 ZIP
하나가 있다고 본다. 파일명에 환자 식별자가 들어 있을 수 있으므로 어떤
이름도 이 파일에 적지 않는다 — 레이아웃을 찾아서 쓴다.

전부 realdata 표시가 붙어 있다. 데이터가 없으면 conftest의 fixture가 skip한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seg_and_mesh.io import (
    SourceKind,
    prepare_input,
    rank_series,
    run_dcm2niix,
)

pytestmark = pytest.mark.realdata


@pytest.fixture
def study_dir(real_data_dir: Path) -> Path:
    """스터디 DICOM이 든 폴더 하나."""
    subdirs = [p for p in sorted(real_data_dir.iterdir()) if p.is_dir()]
    if not subdirs:
        pytest.skip(f"SAM_TEST_DATA_DIR 안에 하위 폴더가 없다: {real_data_dir}")
    return subdirs[0]


@pytest.fixture
def study_zip(real_data_dir: Path) -> Path:
    """같은 스터디를 담은 ZIP 하나."""
    zips = sorted(real_data_dir.glob("*.zip"))
    if not zips:
        pytest.skip(f"SAM_TEST_DATA_DIR 안에 .zip이 없다: {real_data_dir}")
    return zips[0]


def test_directory_input_finds_dicom(study_dir, tmp_path):
    """폴더 입력이 dicom-dir로 판별되고 DICOM을 찾아낸다."""
    prepared = prepare_input(study_dir, tmp_path)

    assert prepared.kind is SourceKind.DICOM_DIR
    assert len(prepared.dicom_files) > 0


def test_zip_input_finds_dicom(study_zip, tmp_path):
    """ZIP 입력이 dicom-zip으로 판별되고 안전 해제를 통과한다.

    실제 157MB / 84엔트리 ZIP이 ExtractLimits 기본값에 걸리지 않아야 한다.
    """
    prepared = prepare_input(study_zip, tmp_path)

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) > 0


def test_zip_and_directory_agree(study_dir, study_zip, tmp_path):
    """같은 스터디면 두 경로가 같은 DICOM 파일 집합을 내놓아야 한다.

    한쪽만 깨지는 회귀(ZIP 해제 누락, 심볼릭 링크 처리 차이 등)를 잡는다.
    """
    from_dir = prepare_input(study_dir, tmp_path / "dir")
    from_zip = prepare_input(study_zip, tmp_path / "zip")

    names_dir = sorted(p.name for p in from_dir.dicom_files)
    names_zip = sorted(p.name for p in from_zip.dicom_files)

    assert names_dir == names_zip


@pytest.mark.dcm2niix
def test_conversion_and_ranking_pick_a_3d_t1(study_dir, tmp_path, dcm2niix_bin):
    """변환 후 순위 1위가 FastSurfer에 넣을 만한 3D T1이어야 한다.

    이 스터디에는 함정이 들어 있다 — 3D MPRAGE 옆에 2D T1 SE 시상면이
    있고 둘 다 설명이 T1 패턴에 걸린다. 2D 쪽이 1위가 되면 FastSurfer가
    조용히 틀린 결과를 낸다(스펙 §6.3).

    특정 시리즈 이름을 단언하지 않는다 — 데이터가 바뀌어도 의미가 유지되게
    성질로 단언한다.
    """
    from seg_and_mesh.io.series import T1_DESCRIPTION_PATTERN

    outputs = run_dcm2niix(study_dir, tmp_path / "nifti", binary=dcm2niix_bin)
    assert len(outputs) > 1, "시리즈가 하나뿐이면 순위를 검증할 수 없다"

    ranked = rank_series(outputs)
    top = ranked[0].series

    assert T1_DESCRIPTION_PATTERN.search(top.series_description), (
        f"1위 설명이 T1 패턴에 안 걸린다: {top.series_description!r}"
    )
    assert top.acquisition_type.upper() == "3D", (
        f"1위가 3D 획득이 아니다: {top.acquisition_type!r}"
    )
    assert top.slices >= 128, f"1위 슬라이스가 128장 미만이다: {top.slices}"
    assert ranked[0].score > ranked[1].score, (
        "1위와 2위가 동점이다 — 함정 시리즈와 구별되지 않는다"
    )


@pytest.mark.dcm2niix
def test_every_output_has_a_description(study_dir, tmp_path, dcm2niix_bin):
    """변환 결과에 이름 없는 항목이 있으면 안 된다.

    dcm2niix는 파생 볼륨(DTI의 ADC 등)에 사이드카를 만들지 않는다.
    그래도 목록에는 이름이 있어야 한다(스펙 §6.3).
    """
    outputs = run_dcm2niix(study_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    nameless = [o.nifti_path.name for o in outputs if not o.series_description]
    assert nameless == [], f"설명이 빈 항목이 있다: {nameless}"
```

- [ ] **Step 2: 데이터 없이 skip되는지 확인한다**

```
uv run pytest tests/io/test_realdata.py -v
```

기대: 전부 SKIP, 사유 `SAM_TEST_DATA_DIR 미설정 — 실제 데이터 테스트를 건너뛴다`.

- [ ] **Step 3: 실제 데이터로 돌린다**

PowerShell에서:

```powershell
$env:SAM_TEST_DATA_DIR = "C:\git\seg-and-mesh\test-asset"
uv run pytest tests/io/test_realdata.py -v
```

기대: `realdata` 3개 PASS, `dcm2niix` 2개 SKIP(바이너리 없음 — Task 5에서 해결).

**셋 중 하나라도 FAIL하면 테스트를 고치지 말고 멈춰서 보고한다.** 이 세 성질은 계획 작성 전에 실제로 확인된 것이다.

- [ ] **Step 4: 전체 스위트로 회귀를 확인한다**

```
uv run pytest -W error
```

기대: 실패 0.

- [ ] **Step 5: 커밋한다**

```bash
git add tests/io/test_realdata.py
git commit -m "test(io): 실제 스터디 회귀 테스트 추가"
```

---

## Task 5: dcm2niix가 든 컨테이너에서 테스트를 돌린다

Task 4의 `dcm2niix` 표시 테스트 2개는 바이너리가 없어 Windows 호스트에서 영영 skip된다. FastSurfer 이미지에는 dcm2niix가 **없다**(직접 확인함) — 스펙 §5.1의 compose 서비스는 `api`와 `fastsurfer` 둘이고 dcm2niix·리맵·메시는 전부 `api` 쪽이다.

데비안 bookworm에 `dcm2niix 1.0.20220720-1+deb12u1` 패키지가 있다. 실제로 이 계획을 쓰며 이 이미지로 스터디를 변환해 8개 시리즈를 뽑았다.

여기서는 `api` 이미지의 **바닥**만 만든다. FastAPI·compose·뷰어는 `web` 계획 소관이다.

**Files:**
- Create: `docker/api.Dockerfile`
- Create: `docker/README.md`
- Modify: `.dockerignore` (없으면 생성)

**Interfaces:**
- Consumes: `pyproject.toml`의 프로젝트 정의
- Produces: 이미지 `sam-api:dev`. `web` 계획이 여기에 FastAPI 레이어를 얹는다.

- [ ] **Step 1: `.dockerignore`를 만든다**

빌드 컨텍스트에 환자 데이터와 대용량 산출물이 들어가지 않게 한다. 저장소 루트에 `.dockerignore`를 만든다.

```
.git/
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.superpowers/
test-asset/
work/
out/
labels/FreeSurferColorLUT.txt
vendor/
```

- [ ] **Step 2: Dockerfile을 만든다**

`docker/api.Dockerfile`:

```dockerfile
# api 서비스 바닥 이미지 (스펙 §5.1 — compose 서비스는 api와 fastsurfer 둘).
# dcm2niix·라벨 리맵·메시 생성이 여기서 돈다. FastSurfer 이미지에는
# dcm2niix가 없으므로 이쪽이 갖는다.
#
# web 계획이 이 위에 FastAPI 레이어를 얹는다.
FROM debian:bookworm-slim

# dcm2niix 1.0.20220720-1+deb12u1 (bookworm)
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
      dcm2niix \
      python3 \
      python3-venv \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 태그 고정. 계획 작성 시점에 존재를 확인했다(docker manifest inspect).
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

# uv가 파이썬을 따로 내려받지 않고 apt의 3.11을 쓰게 한다.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 의존성 레이어를 소스와 분리해 소스만 바뀌었을 때 재설치를 피한다.
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project

COPY seg_and_mesh /app/seg_and_mesh
COPY tests /app/tests
RUN uv sync --frozen

CMD ["uv", "run", "--frozen", "pytest", "-W", "error"]
```

- [ ] **Step 3: 이미지를 빌드한다**

```powershell
docker build -f docker/api.Dockerfile -t sam-api:dev .
```

기대: 성공. 마지막 줄이 `naming to docker.io/library/sam-api:dev`.

- [ ] **Step 4: 컨테이너 안에서 dcm2niix를 확인한다**

```powershell
docker run --rm --entrypoint sh sam-api:dev -c "dcm2niix --version"
```

기대: `v1.0.20220720`.

- [ ] **Step 5: 데이터 없이 전체 스위트를 컨테이너에서 돌린다**

```powershell
docker run --rm sam-api:dev
```

기대: 실패 0. `realdata` 표시 테스트는 SKIP한다.

계획 작성 중 이 이미지를 실제로 빌드해 돌렸다. Task 1~4 적용 **전** 기준으로 `106 passed, 4 skipped`가 나왔다. 호스트에서는 `105 passed, 5 skipped`였는데, 차이는 **심볼릭 링크 테스트가 컨테이너에서는 실제로 실행된다**는 점이다 — Windows 호스트에서는 링크 생성 권한이 없어(WinError 1314) 그동안 skip되었고, 따라서 심볼릭 링크 탈출 방어는 한 번도 실행 검증된 적이 없었다. 이 이미지가 그 구멍을 메운다.

- [ ] **Step 6: 실데이터를 붙여 돌린다**

`test-asset`을 읽기 전용으로 마운트한다.

```powershell
docker run --rm `
  -v "C:\git\seg-and-mesh\test-asset:/data:ro" `
  -e SAM_TEST_DATA_DIR=/data `
  sam-api:dev
```

기대: **실패 0, SKIP 0.** Task 4의 `dcm2niix` 표시 테스트 2개가 실제로 통과해야 한다.

계획 작성 중 Task 1~4 적용 **전** 상태로 이 명령을 실제로 돌려 `110 passed in 61.64s`, skip 0을 확인했다. Task 1~4가 테스트를 추가하므로 통과 수는 그보다 커야 한다.

**여기서 FAIL이 나면 테스트를 고치지 말고 멈춰서 보고한다.**

- [ ] **Step 7: README를 쓴다**

`docker/README.md`:

```markdown
# 컨테이너에서 테스트 돌리기

Windows 호스트에는 dcm2niix가 없다. `dcm2niix` 표시가 붙은 테스트는
호스트에서 항상 skip된다. 컨테이너로 돌리면 실제로 실행된다.

## 빌드

    docker build -f docker/api.Dockerfile -t sam-api:dev .

## 데이터 없이

    docker run --rm sam-api:dev

`realdata` 표시 테스트는 skip된다.

## 실데이터로

    docker run --rm -v "C:\path\to\test-asset:/data:ro" -e SAM_TEST_DATA_DIR=/data sam-api:dev

`/data` 안에 같은 스터디의 DICOM 폴더 하나와 ZIP 하나가 있어야 한다.

**마운트하는 폴더에는 환자 데이터가 들어 있다.** 읽기 전용(`:ro`)으로 붙이고,
이 폴더를 저장소에 커밋하지 않는다 — `.gitignore`와 `.dockerignore` 양쪽에
`test-asset/`이 들어 있다.

## 이미지 범위

이 이미지는 스펙 §5.1 `api` 서비스의 바닥이다. dcm2niix와 파이썬 의존성만
들어 있다. FastAPI·compose·뷰어는 `web` 계획에서 얹는다.
```

- [ ] **Step 8: 커밋한다**

```bash
git add .dockerignore docker/api.Dockerfile docker/README.md
git commit -m "build: dcm2niix가 든 api 바닥 이미지와 컨테이너 테스트 경로 추가"
```

---

## 후속 계획

이 계획이 끝나면 원래 순서로 돌아간다.

| 계획 | 스펙 절 | 내용 |
|---|---|---|
| labels | §3, §2.2 | `canonical-v1.tsv` 생성, 정적 LUT, 리맵, uint8 NIfTI 저장 |
| mesh | §6.5~§6.7, §7.1, §7.2 | 3축 변형 생성, GLB 작성, 지표 계산 |
| segment + jobs | §5.1, §6.4, §7, §9, §12 | docker socket 실행, 잡 큐, status.json, 스토리지 정리, **PHI 마스킹** |
| web | §4, §8 | FastAPI 라우트, three.js 뷰어, compose |
