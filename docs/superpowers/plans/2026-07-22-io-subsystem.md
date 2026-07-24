# io 서브시스템 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 업로드된 MRI 입력(DICOM ZIP / DICOM 폴더 / NIfTI)을 확장자에 의존하지 않고 판별·안전 해제·NIfTI 변환하고, T1 후보 시리즈를 점수순으로 추천하는 `seg_and_mesh/io/` 패키지를 완성한다.

**Architecture:** 순수 함수 + 파일 경로 입출력. 각 모듈은 하나의 책임만 갖는다 — `detect`(매직바이트 판별), `archive`(ZIP 안전 해제), `ingest`(입력 정규화), `dcm2niix`(외부 바이너리 래핑), `series`(T1 추천). 도커·FastAPI·잡 큐에 의존하지 않으므로 전 구간을 로컬 pytest로 검증한다. 외부 바이너리(`dcm2niix`)와 실제 MRI 데이터가 필요한 테스트는 마커로 분리해 없으면 skip한다.

**Tech Stack:** Python 3.11 (uv 관리), pytest, pydicom 3.x, nibabel 5.x, dcm2niix(외부 바이너리)

**설계 스펙:** `docs/superpowers/specs/2026-07-22-seg-and-mesh-design.md` — 이 계획은 스펙 §6.1, §6.2, §6.3, §10, §11, §13을 구현한다.

## Global Constraints

- 패키지 루트는 `seg_and_mesh/`, io 서브패키지는 `seg_and_mesh/io/` (스펙 §10 모듈 구조 그대로). `io`는 표준 라이브러리 모듈명과 겹치지만 Python 3의 절대 임포트에서는 충돌하지 않는다. 패키지 내부에서 표준 `io`가 필요하면 `import io`로 그대로 쓰면 stdlib이 잡힌다.
- **확장자를 신뢰하지 않는다.** 파일 종류 판별은 전부 매직바이트로 한다 (스펙 §6.1).
- ZIP 전개 상한 기본값: 총 20GB (`MAX_EXTRACT_GB=20`), 업로드 상한 4096MB (`MAX_UPLOAD_MB=4096`) — 스펙 §11.
- dcm2niix 재귀 탐색 깊이는 `-d 5` (스펙 §6.3).
- T1 자동 추천 기준 (스펙 §6.3): 등방성에 가까운 voxel(약 1mm), 슬라이스 128장 이상, description이 `mprage|t1|bravo|spgr|tfl` 매칭. **자동 선택만으로 진행하지 않고 사용자 확인을 받는다** — 이 계획의 코드는 추천 순위만 반환하고 절대 자동 확정하지 않는다.
- 안전 검사에 걸린 ZIP은 **잡 전체를 거부한다** (스펙 §6.2). 부분 성공 없음.
- 모든 예외 메시지와 docstring은 한국어로 쓴다. 식별자·타입명은 영어.
- 커밋 메시지는 Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`).
- 테스트 마커: `realdata`(실제 MRI 데이터 필요), `dcm2niix`(외부 바이너리 필요). 없으면 skip하되 CI 기본 실행에서 실패하지 않아야 한다.

---

### Task 1: 프로젝트 스캐폴딩

빈 저장소에 uv 프로젝트, 패키지 뼈대, pytest 설정, 환경 변수 예시를 만든다.

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `seg_and_mesh/__init__.py`
- Create: `seg_and_mesh/io/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `seg_and_mesh.__version__: str`
  - pytest fixture `real_data_dir() -> pathlib.Path` — 환경 변수 `SAM_TEST_DATA_DIR`가 가리키는 실제 MRI 데이터 폴더. 미설정이면 `pytest.skip`.
  - pytest 마커 `realdata`, `dcm2niix`

- [ ] **Step 1: `.python-version` 작성**

```
3.11
```

- [ ] **Step 2: `pyproject.toml` 작성**

```toml
[project]
name = "seg-and-mesh"
version = "0.1.0"
description = "뇌 T1 MRI에서 영역별 세그멘테이션 볼륨과 3D 메시를 산출하는 로컬 도구"
requires-python = ">=3.11,<3.13"
dependencies = [
    "pydicom>=3.0,<4",
    "nibabel>=5.2,<6",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["seg_and_mesh"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = [
    "realdata: 실제 MRI 데이터가 있을 때만 실행한다 (환경 변수 SAM_TEST_DATA_DIR)",
    "dcm2niix: dcm2niix 바이너리가 있을 때만 실행한다 (PATH 또는 DCM2NIIX_BIN)",
]
```

- [ ] **Step 3: `.gitignore` 작성**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.pytest_cache/
.coverage

# 환경 설정 (스펙 §11 — 저장소에는 .env.example만 둔다)
.env

# 작업 산출물
/work/
/out/

# FastSurfer 이미지에서 추출한 원본 LUT (labels 계획에서 tsv 생성 후 커밋 대상은 tsv뿐)
/labels/FreeSurferColorLUT.txt

# 외부 바이너리
/vendor/
```

- [ ] **Step 4: `.env.example` 작성 (스펙 §11 표 그대로)**

```dotenv
# 호스트 출력 폴더. compose 바인드 마운트 경로가 되므로 호스트마다 다르다. 필수.
OUTPUT_DIR=

# FastSurfer 이미지 태그. 특정 버전으로 고정한다. latest는 쓰지 않는다. 필수.
FASTSURFER_IMAGE=

# FastSurfer CPU 스레드
FASTSURFER_THREADS=8

# 업로드 상한 (MB)
MAX_UPLOAD_MB=4096

# ZIP 전개 상한 (GB)
MAX_EXTRACT_GB=20

# 로컬 포트
WEB_PORT=8000

# --- 개발용 (컨테이너 밖에서 테스트할 때만) ---
# dcm2niix 바이너리 경로. 미지정이면 PATH에서 찾는다.
# DCM2NIIX_BIN=
# 실제 MRI 테스트 데이터 폴더. 미지정이면 realdata 테스트를 skip한다.
# SAM_TEST_DATA_DIR=
```

- [ ] **Step 5: `README.md` 작성**

````markdown
# seg-and-mesh

뇌 T1 MRI를 입력받아 영역별 세그멘테이션 볼륨과 3D 메시를 산출하는 로컬 도구.

- 설계: [docs/superpowers/specs/2026-07-22-seg-and-mesh-design.md](docs/superpowers/specs/2026-07-22-seg-and-mesh-design.md)
- 계획: [docs/superpowers/plans/](docs/superpowers/plans/)

## 개발 환경

```bash
uv sync
uv run pytest
```

## 상태

구현 중. 현재 `seg_and_mesh/io/` (입력 판별·ZIP 안전 해제·NIfTI 변환·시리즈 추천)까지.
````

- [ ] **Step 6: 패키지 뼈대 작성**

`seg_and_mesh/__init__.py`:

```python
"""seg-and-mesh — 뇌 T1 MRI에서 세그멘테이션 볼륨과 영역별 3D 메시를 산출한다."""

__version__ = "0.1.0"
```

`seg_and_mesh/io/__init__.py`:

```python
"""입력 판별, ZIP 안전 해제, dcm2niix 래핑 (스펙 §6.1~§6.3)."""
```

`tests/__init__.py`: 빈 파일.

- [ ] **Step 7: `tests/conftest.py` 작성**

```python
"""테스트 공통 fixture."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def real_data_dir() -> Path:
    """실제 MRI 테스트 데이터 폴더. 환경 변수 SAM_TEST_DATA_DIR로 지정한다."""
    raw = os.environ.get("SAM_TEST_DATA_DIR")
    if not raw:
        pytest.skip("SAM_TEST_DATA_DIR 미설정 — 실제 데이터 테스트를 건너뛴다")
    path = Path(raw)
    if not path.is_dir():
        pytest.skip(f"SAM_TEST_DATA_DIR가 폴더가 아니다: {path}")
    return path


@pytest.fixture
def dcm2niix_bin() -> str:
    """dcm2niix 실행 파일 경로. 없으면 skip."""
    found = os.environ.get("DCM2NIIX_BIN") or shutil.which("dcm2niix")
    if not found:
        pytest.skip("dcm2niix 바이너리를 찾지 못했다 — PATH 또는 DCM2NIIX_BIN 설정 필요")
    return found
```

- [ ] **Step 8: 실패하는 테스트 작성**

`tests/test_package.py`:

```python
"""패키지가 임포트되고 버전이 노출되는지 확인한다."""

import seg_and_mesh
import seg_and_mesh.io


def test_version_is_exposed():
    assert seg_and_mesh.__version__ == "0.1.0"


def test_io_subpackage_does_not_shadow_stdlib_io():
    """seg_and_mesh.io가 표준 라이브러리 io를 가리지 않는지 확인한다."""
    import io as stdlib_io

    assert hasattr(stdlib_io, "BytesIO")
    assert seg_and_mesh.io is not stdlib_io
```

- [ ] **Step 9: 의존성 설치 후 테스트 실행**

```bash
uv sync
uv run pytest tests/test_package.py -v
```

Expected: 2 passed. (Step 6에서 파일을 이미 만들었으므로 통과한다. `uv sync` 실패 시 `.python-version`의 3.11을 uv가 내려받도록 `uv python install 3.11` 실행 후 재시도.)

- [ ] **Step 10: 커밋**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example README.md seg_and_mesh tests
git commit -m "chore: uv 프로젝트 스캐폴딩과 pytest 설정 추가"
```

---

### Task 2: 입력 판별 — 매직바이트 + pydicom 폴백 (스펙 §6.1)

**Files:**
- Create: `seg_and_mesh/io/detect.py`
- Test: `tests/io/__init__.py`, `tests/io/test_detect.py`

**Interfaces:**
- Consumes: Task 1의 패키지 뼈대
- Produces:
  - `class InputKind(str, Enum)` — 멤버 `ZIP="zip"`, `NIFTI="nifti"`, `DICOM="dicom"`, `UNKNOWN="unknown"`
  - `detect_format(path: pathlib.Path) -> InputKind`
  - `detect_format_bytes(head: bytes) -> InputKind | None` — 선두 바이트만으로 판별. 확정 못 하면 `None`.
  - `PROBE_BYTES: int = 1024`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/io/__init__.py`는 빈 파일로 만든다.

`tests/io/test_detect.py`:

```python
"""입력 판별 테스트 (스펙 §6.1).

확장자를 신뢰하지 않는다는 계약을 지키는지 확인한다 — 모든 fixture 파일은
내용과 무관한 확장자를 갖는다.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import pytest
from pydicom.dataset import Dataset

from seg_and_mesh.io.detect import InputKind, detect_format


def _nifti1_bytes(endian: str = "<", magic: bytes = b"n+1\x00") -> bytes:
    """NIfTI-1 헤더 348바이트. sizeof_hdr==348, offset 344에 magic."""
    hdr = bytearray(348)
    struct.pack_into(endian + "i", hdr, 0, 348)
    hdr[344:348] = magic
    return bytes(hdr) + b"\x00" * 100


def _nifti2_bytes(endian: str = "<") -> bytes:
    """NIfTI-2 헤더. sizeof_hdr==540, offset 4에 'n+2\\0'."""
    hdr = bytearray(540)
    struct.pack_into(endian + "i", hdr, 0, 540)
    hdr[4:8] = b"n+2\x00"
    hdr[8:12] = b"\r\n\x1a\n"
    return bytes(hdr) + b"\x00" * 100


def _dicom_with_preamble() -> bytes:
    """offset 128에 'DICM'이 있는 표준 DICOM 선두."""
    return b"\x00" * 128 + b"DICM" + b"\x02\x00\x00\x00UL\x04\x00" + b"\x00" * 200


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_zip_detected_by_magic(tmp_path):
    path = _write(tmp_path, "study.dcm", b"PK\x03\x04" + b"\x00" * 200)
    assert detect_format(path) is InputKind.ZIP


def test_nifti1_little_endian(tmp_path):
    path = _write(tmp_path, "IM000001", _nifti1_bytes("<"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti1_big_endian(tmp_path):
    path = _write(tmp_path, "noext", _nifti1_bytes(">"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti1_hdr_img_pair_magic(tmp_path):
    """.hdr/.img 쌍의 magic 'ni1\\0'도 NIfTI로 인정한다."""
    path = _write(tmp_path, "vol.img", _nifti1_bytes("<", b"ni1\x00"))
    assert detect_format(path) is InputKind.NIFTI


def test_nifti2(tmp_path):
    path = _write(tmp_path, "vol.ima", _nifti2_bytes("<"))
    assert detect_format(path) is InputKind.NIFTI


def test_gzipped_nifti(tmp_path):
    path = _write(tmp_path, "anything", gzip.compress(_nifti1_bytes("<")))
    assert detect_format(path) is InputKind.NIFTI


def test_dicom_with_preamble(tmp_path):
    path = _write(tmp_path, "I10", _dicom_with_preamble())
    assert detect_format(path) is InputKind.DICOM


def test_dicom_without_preamble(tmp_path):
    """메타 헤더 없이 raw dataset으로 내보낸 DICOM은 'DICM'이 없다.

    pydicom 폴백으로 SOPClassUID / Rows·Columns를 보고 인정한다.
    """
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    ds.Rows = 256
    ds.Columns = 256
    path = tmp_path / "1.2.840.113619.2.1.1.1"
    ds.save_as(path, implicit_vr=True, little_endian=True, enforce_file_format=False)

    assert path.read_bytes()[128:132] != b"DICM"
    assert detect_format(path) is InputKind.DICOM


def test_garbage_is_unknown(tmp_path):
    path = _write(tmp_path, "notes.nii.gz", b"hello world, not an image" * 10)
    assert detect_format(path) is InputKind.UNKNOWN


def test_empty_file_is_unknown(tmp_path):
    path = _write(tmp_path, "empty.zip", b"")
    assert detect_format(path) is InputKind.UNKNOWN


def test_truncated_gzip_is_unknown(tmp_path):
    """깨진 gzip은 예외를 던지지 않고 UNKNOWN을 반환한다."""
    path = _write(tmp_path, "broken", gzip.compress(_nifti1_bytes())[:20])
    assert detect_format(path) is InputKind.UNKNOWN


def test_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        detect_format(tmp_path)


def test_ds_store_and_thumbs_are_unknown(tmp_path):
    """스펙 §6.2 — __MACOSX/, .DS_Store 등은 매직바이트 검사에서 자동 탈락한다."""
    path = _write(tmp_path, ".DS_Store", b"\x00\x00\x00\x01Bud1" + b"\x00" * 100)
    assert detect_format(path) is InputKind.UNKNOWN
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_detect.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seg_and_mesh.io.detect'`

- [ ] **Step 3: `seg_and_mesh/io/detect.py` 구현**

```python
"""입력 파일 종류 판별 (스펙 §6.1).

확장자를 신뢰하지 않는다. DICOM 파일명은 IM000001, I10, SOP Instance UID
문자열, 확장자 없음, .ima, .img 등 제각각이다. 매직바이트로 판별한다.

| 대상    | 판별                                                    |
|---------|---------------------------------------------------------|
| ZIP     | 첫 4바이트 PK\\x03\\x04                                   |
| gzip    | \\x1f\\x8b → 전개 후 아래 검사                            |
| NIfTI-1 | 첫 4바이트 int32 == 348 (양쪽 엔디안) + offset 344 magic |
| NIfTI-2 | sizeof_hdr == 540 + offset 4에 'n+2'                    |
| DICOM   | offset 128에 'DICM'                                     |

preamble 없는 DICOM은 'DICM'이 없으므로 pydicom 폴백으로 2단계 판별한다.
"""

from __future__ import annotations

import gzip
import struct
import zlib
from enum import Enum
from pathlib import Path

import pydicom

#: 판별에 읽는 선두 바이트. NIfTI-1 magic이 offset 344에 있으므로 348 이상이어야 한다.
PROBE_BYTES = 1024

_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"
_NIFTI1_MAGICS = (b"n+1\x00", b"ni1\x00")
_NIFTI2_MAGIC = b"n+2\x00"
_DICOM_PREAMBLE_OFFSET = 128
_DICOM_MAGIC = b"DICM"


class InputKind(str, Enum):
    """판별된 입력 종류."""

    ZIP = "zip"
    NIFTI = "nifti"
    DICOM = "dicom"
    UNKNOWN = "unknown"


def _read_int32(head: bytes, endian: str) -> int | None:
    if len(head) < 4:
        return None
    return struct.unpack(endian + "i", head[:4])[0]


def _is_zip(head: bytes) -> bool:
    return head[:4] == _ZIP_MAGIC


def _is_gzip(head: bytes) -> bool:
    return head[:2] == _GZIP_MAGIC


def _is_nifti(head: bytes) -> bool:
    """NIfTI-1(348) / NIfTI-2(540) 헤더인지 확인한다. 양쪽 엔디안을 모두 본다."""
    for endian in ("<", ">"):
        sizeof_hdr = _read_int32(head, endian)
        if sizeof_hdr == 348 and len(head) >= 348 and head[344:348] in _NIFTI1_MAGICS:
            return True
        if sizeof_hdr == 540 and len(head) >= 8 and head[4:8] == _NIFTI2_MAGIC:
            return True
    return False


def _is_dicom_preamble(head: bytes) -> bool:
    end = _DICOM_PREAMBLE_OFFSET + len(_DICOM_MAGIC)
    return len(head) >= end and head[_DICOM_PREAMBLE_OFFSET:end] == _DICOM_MAGIC


def _is_dicom_fallback(path: Path) -> bool:
    """preamble 없는 DICOM을 pydicom으로 판별한다.

    force=True는 임의 바이너리에도 빈 Dataset을 돌려주므로, 실제 DICOM 태그가
    있는지를 반드시 확인해야 한다.
    """
    try:
        ds = pydicom.dcmread(path, force=True, stop_before_pixels=True)
    except Exception:
        return False
    if "SOPClassUID" in ds:
        return True
    return "Rows" in ds and "Columns" in ds


def detect_format_bytes(head: bytes) -> InputKind | None:
    """선두 바이트만으로 판별한다. 확정하지 못하면 None을 돌려준다.

    gzip은 여기서 판정하지 않는다. 전개가 필요하므로 detect_format이 처리한다.
    """
    if _is_zip(head):
        return InputKind.ZIP
    if _is_nifti(head):
        return InputKind.NIFTI
    if _is_dicom_preamble(head):
        return InputKind.DICOM
    return None


def detect_format(path: Path) -> InputKind:
    """파일 하나의 종류를 판별한다.

    Raises:
        IsADirectoryError: path가 폴더일 때.
        FileNotFoundError: path가 없을 때.
    """
    path = Path(path)
    if path.is_dir():
        raise IsADirectoryError(f"파일이 아니라 폴더다: {path}")

    with open(path, "rb") as fh:
        head = fh.read(PROBE_BYTES)

    if _is_gzip(head):
        try:
            with gzip.open(path, "rb") as gz:
                inner = gz.read(PROBE_BYTES)
        except (OSError, EOFError, zlib.error):
            # 잘린 gzip은 EOFError, 헤더는 멀쩡한데 페이로드가 손상된 gzip은
            # zlib.error를 낸다. zlib.error는 OSError를 상속하지 않으므로
            # 따로 잡아야 한다.
            return InputKind.UNKNOWN
        return detect_format_bytes(inner) or InputKind.UNKNOWN

    kind = detect_format_bytes(head)
    if kind is not None:
        return kind

    if _is_dicom_fallback(path):
        return InputKind.DICOM
    return InputKind.UNKNOWN
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

```bash
uv run pytest tests/io/test_detect.py -v
```

Expected: 13 passed

- [ ] **Step 5: 실제 데이터로 검증하는 테스트 추가**

`tests/io/test_detect.py` 끝에 덧붙인다:

```python
@pytest.mark.realdata
def test_real_data_files_are_all_recognized(real_data_dir):
    """SAM_TEST_DATA_DIR 안의 파일이 ZIP/NIfTI/DICOM 중 하나로 판별되는지 본다.

    UNKNOWN이 나온 파일 목록을 그대로 보여준다. 진짜 잡파일(readme 등)이면
    무시하고, DICOM인데 UNKNOWN이면 판별 로직에 구멍이 있다는 뜻이다.
    """
    kinds: dict[InputKind, list[str]] = {k: [] for k in InputKind}
    for path in sorted(real_data_dir.rglob("*")):
        if path.is_file():
            kinds[detect_format(path)].append(str(path.relative_to(real_data_dir)))

    recognized = sum(len(v) for k, v in kinds.items() if k is not InputKind.UNKNOWN)
    assert recognized > 0, f"인식된 파일이 하나도 없다: {kinds}"
    print(f"\nZIP={len(kinds[InputKind.ZIP])} NIFTI={len(kinds[InputKind.NIFTI])} "
          f"DICOM={len(kinds[InputKind.DICOM])} UNKNOWN={len(kinds[InputKind.UNKNOWN])}")
    if kinds[InputKind.UNKNOWN]:
        print("UNKNOWN 목록 (앞 20개):")
        for name in kinds[InputKind.UNKNOWN][:20]:
            print(f"  {name}")
```

- [ ] **Step 6: 실제 데이터로 실행**

PowerShell에서 실제 데이터 폴더를 지정해 실행한다. `<실제 데이터 폴더>`는 보유 중인 DICOM/ZIP/NIfTI가 들어 있는 경로로 바꾼다.

```powershell
$env:SAM_TEST_DATA_DIR = "<실제 데이터 폴더>"
uv run pytest tests/io/test_detect.py -v -m realdata -s
```

Expected: PASS. 출력의 UNKNOWN 목록을 눈으로 확인한다. DICOM이어야 할 파일이 UNKNOWN이면 그 파일의 선두 132바이트를 덤프해 원인을 찾고 판별 로직을 고친 뒤 회귀 테스트를 추가한다.

- [ ] **Step 7: 커밋**

```bash
git add seg_and_mesh/io/detect.py tests/io
git commit -m "feat: 매직바이트 기반 입력 판별 (ZIP/gzip/NIfTI-1,2/DICOM)"
```

---

### Task 3: ZIP 안전 해제 (스펙 §6.2)

> **실행 후 기록 (2026-07-22).** 아래 코드는 착수 시점 초안이다. 보안 리뷰에서
> 다음이 드러나 실제 구현은 이보다 넓다. 최종 형태는 `seg_and_mesh/io/archive.py`를
> 보라.
>
> - 드라이브 지정 검사가 선두 컴포넌트만 봤다. `a/C:evil.txt`가 통과해
>   `joinpath`가 `dest_root/evil.txt`로 재앵커링했다 — 탈출은 아니지만 아카이브
>   내 다른 엔트리를 덮어쓸 수 있다. 컴포넌트별 검사로 바꿨다.
> - `max_entries`가 `ZipFile()`이 중앙 디렉터리를 전부 `ZipInfo`로 올린 뒤에야
>   검사됐다. `ExtractLimits.max_zip_bytes`(기본 4GB, `MAX_UPLOAD_MB=4096` 대응)를
>   추가해 `ZipFile` 생성 전에 파일 크기를 본다.
> - `mkdir(exist_ok=True)` + 실패 시 `rmtree`가 남이 만든 폴더를 지웠다.
>   비어 있지 않은 기존 `dest_root`는 거부한다.
> - Windows 예약 장치명(`NUL`, `CON`, `COM1`…, 확장자·후행 공백/점 변형 포함)
>   엔트리는 쓰기가 성공하지만 데이터가 null 장치로 사라지고 `files`에 유령
>   경로가 남았다. 스템 완전 일치로 거부한다(`CON000123` 같은 실제 DICOM 이름은
>   통과해야 하므로 접두사 매칭이 아니다).
> - `decode_entry_name`의 cp437 폴백 테스트가 `"plain-ascii"`를 써서 `except`
>   분기에 닿지 않았다.

**Files:**
- Create: `seg_and_mesh/io/archive.py`
- Test: `tests/io/test_archive.py`

**Interfaces:**
- Consumes: 없음 (detect와 독립)
- Produces:
  - `class UnsafeArchiveError(Exception)`
  - `@dataclass(frozen=True) class ExtractLimits` — 필드 `max_total_bytes: int = 20 * 1024**3`, `max_entries: int = 200_000`
  - `@dataclass(frozen=True) class ExtractResult` — 필드 `files: list[Path]`, `total_bytes: int`
  - `decode_entry_name(info: zipfile.ZipInfo) -> str`
  - `safe_extract(zip_path: Path, dest_root: Path, limits: ExtractLimits = ExtractLimits()) -> ExtractResult`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/io/test_archive.py`:

```python
"""ZIP 안전 해제 테스트 (스펙 §6.2)."""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from seg_and_mesh.io.archive import (
    ExtractLimits,
    UnsafeArchiveError,
    decode_entry_name,
    safe_extract,
)


def _zip_with(tmp_path: Path, entries: dict[str, bytes], name: str = "in.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for entry_name, data in entries.items():
            zf.writestr(entry_name, data)
    return path


def _raw_zip(path: Path, entries: list[tuple[bytes, bytes]]) -> Path:
    """파일명 바이트를 그대로 넣고 UTF-8 플래그를 켜지 않는 ZIP을 만든다.

    zipfile.ZipFile.writestr는 비ASCII 이름을 만나면 UTF-8로 인코딩하고
    플래그 0x800을 강제로 켠다. 그래서 CP949 이름을 재현하려면 헤더를 직접
    써야 한다. 압축 없음(stored)으로 최소 구조만 만든다.
    """
    local = b""
    central = b""
    offset = 0
    for name_bytes, data in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        n, size = len(name_bytes), len(data)
        local += (
            struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0, crc, size, size, n, 0)
            + name_bytes
            + data
        )
        central += (
            struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
                        crc, size, size, n, 0, 0, 0, 0, 0, offset)
            + name_bytes
        )
        offset = len(local)
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries), len(entries),
                       len(central), len(local), 0)
    path.write_bytes(local + central + eocd)
    return path


def test_extracts_nested_dirs(tmp_path):
    src = _zip_with(tmp_path, {
        "study/series1/IM000001": b"a" * 10,
        "study/series1/IM000002": b"b" * 10,
        "study/series2/IM000001": b"c" * 10,
    })
    dest = tmp_path / "out"

    result = safe_extract(src, dest)

    assert result.total_bytes == 30
    assert len(result.files) == 3
    assert (dest / "study" / "series1" / "IM000001").read_bytes() == b"a" * 10


def test_rejects_parent_traversal(tmp_path):
    src = _zip_with(tmp_path, {"../evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_deep_parent_traversal(tmp_path):
    src = _zip_with(tmp_path, {"a/b/../../../evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_absolute_path(tmp_path):
    src = _zip_with(tmp_path, {"/etc/passwd": b"x"})
    with pytest.raises(UnsafeArchiveError, match="절대 경로"):
        safe_extract(src, tmp_path / "out")


def test_rejects_windows_drive_path(tmp_path):
    src = _zip_with(tmp_path, {"C:/Windows/evil.dll": b"x"})
    with pytest.raises(UnsafeArchiveError, match="드라이브"):
        safe_extract(src, tmp_path / "out")


def test_rejects_backslash_traversal(tmp_path):
    """Windows 구분자를 쓴 순회도 막는다."""
    src = _zip_with(tmp_path, {"..\\evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_symlink_entry(tmp_path):
    """심볼릭 링크 엔트리는 external_attr의 S_IFLNK로 판별해 거부한다."""
    src = tmp_path / "link.zip"
    with zipfile.ZipFile(src, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # Unix
        info.external_attr = (0o120777 << 16)  # S_IFLNK | 0777
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(UnsafeArchiveError, match="심볼릭 링크"):
        safe_extract(src, tmp_path / "out")


def test_rejects_when_declared_size_exceeds_limit(tmp_path):
    src = _zip_with(tmp_path, {"big.bin": b"x" * 5000})
    limits = ExtractLimits(max_total_bytes=1000)
    with pytest.raises(UnsafeArchiveError, match="용량 상한"):
        safe_extract(src, tmp_path / "out", limits)


def test_rejects_when_entry_count_exceeds_limit(tmp_path):
    src = _zip_with(tmp_path, {f"f{i}": b"x" for i in range(10)})
    limits = ExtractLimits(max_entries=5)
    with pytest.raises(UnsafeArchiveError, match="엔트리 수 상한"):
        safe_extract(src, tmp_path / "out", limits)


def test_cleans_up_dest_on_failure(tmp_path):
    """안전 검사에 걸리면 잡 전체를 거부한다 — 부분 전개물을 남기지 않는다."""
    src = _zip_with(tmp_path, {"ok.bin": b"x" * 10, "../evil.txt": b"y"})
    dest = tmp_path / "out"

    with pytest.raises(UnsafeArchiveError):
        safe_extract(src, dest)

    assert not dest.exists()


def test_decodes_cp949_name_without_utf8_flag():
    """Windows 기본 압축은 CP949로 저장하면서 UTF-8 플래그(0x800)를 켜지 않는다.

    zipfile은 이를 CP437로 해석해 이름이 깨진다. 플래그가 없으면 CP949로 재디코딩한다.
    """
    raw = "환자01".encode("cp949")
    info = zipfile.ZipInfo(raw.decode("cp437"))
    info.flag_bits = 0

    assert decode_entry_name(info) == "환자01"


def test_keeps_utf8_name_when_flag_set():
    info = zipfile.ZipInfo("환자01")
    info.flag_bits = 0x800

    assert decode_entry_name(info) == "환자01"


def test_falls_back_to_raw_name_when_cp949_decode_fails():
    info = zipfile.ZipInfo("plain-ascii")
    info.flag_bits = 0

    assert decode_entry_name(info) == "plain-ascii"


def test_cp949_name_is_used_on_disk(tmp_path):
    """Windows 기본 압축이 만든 ZIP을 재현해 전개 후 경로가 한글인지 본다."""
    src = _raw_zip(tmp_path / "kr.zip", [("환자01/IM000001".encode("cp949"), b"data")])
    dest = tmp_path / "out"

    result = safe_extract(src, dest)

    assert result.files == [dest / "환자01" / "IM000001"]
    assert (dest / "환자01" / "IM000001").read_bytes() == b"data"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_archive.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seg_and_mesh.io.archive'`

- [ ] **Step 3: `seg_and_mesh/io/archive.py` 구현**

```python
"""ZIP 안전 해제 (스펙 §6.2).

강제 사항:
1. 경로 순회 차단 — '..', 절대 경로, 드라이브 지정, 심볼릭 링크 엔트리를 거부한다.
2. 파일명 인코딩 — UTF-8 플래그가 없으면 CP949로 재디코딩한다.
3. 용량 상한 — 전개 총 용량과 엔트리 수 상한을 넘으면 중단한다.

검사에 걸리면 잡 전체를 거부한다. 부분 전개물은 남기지 않는다.
"""

from __future__ import annotations

import ntpath
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: 스트리밍 읽기 단위
_CHUNK_BYTES = 1 << 20

#: ZIP 범용 비트 플래그 11번 — 파일명이 UTF-8임을 뜻한다.
_UTF8_FLAG = 0x800


class UnsafeArchiveError(Exception):
    """ZIP이 안전 검사에 걸렸다. 잡 전체를 거부한다."""


@dataclass(frozen=True)
class ExtractLimits:
    """전개 상한. 기본값은 스펙 §11의 MAX_EXTRACT_GB=20을 따른다."""

    max_total_bytes: int = 20 * 1024**3
    max_entries: int = 200_000


@dataclass(frozen=True)
class ExtractResult:
    """전개 결과. files는 실제로 기록된 일반 파일 경로만 담는다."""

    files: list[Path]
    total_bytes: int


def decode_entry_name(info: zipfile.ZipInfo) -> str:
    """엔트리 이름을 올바른 인코딩으로 되돌린다.

    Windows 기본 압축은 CP949로 저장하면서 UTF-8 플래그를 켜지 않는다.
    zipfile은 플래그가 없는 이름을 CP437로 디코딩하므로 한글이 깨진다.
    CP437로 되감아 CP949로 다시 읽는다.

    판별에는 파일명을 쓰지 않지만 로그·오류 메시지 가독성에 필요하다.
    """
    if info.flag_bits & _UTF8_FLAG:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Unix에서 만든 ZIP은 external_attr 상위 16비트에 st_mode를 담는다."""
    return stat.S_ISLNK(info.external_attr >> 16)


def _safe_destination(name: str, dest_root: Path) -> Path:
    """엔트리 이름을 dest_root 하위의 안전한 경로로 바꾼다.

    Raises:
        UnsafeArchiveError: 절대 경로, 드라이브 지정, 경로 순회일 때.
    """
    normalized = name.replace("\\", "/")

    if normalized.startswith("/"):
        raise UnsafeArchiveError(f"절대 경로 엔트리를 거부한다: {name!r}")
    if ntpath.splitdrive(name)[0]:
        raise UnsafeArchiveError(f"드라이브 지정 엔트리를 거부한다: {name!r}")

    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeArchiveError(f"경로 순회 엔트리를 거부한다: {name!r}")
    if not parts:
        raise UnsafeArchiveError(f"빈 엔트리 이름을 거부한다: {name!r}")

    target = dest_root.joinpath(*parts)
    root_resolved = dest_root.resolve()
    try:
        target.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafeArchiveError(f"경로 순회 엔트리를 거부한다: {name!r}") from exc
    return target


def _extract_all(zf: zipfile.ZipFile, dest_root: Path, limits: ExtractLimits) -> ExtractResult:
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafeArchiveError(
            f"엔트리 수 상한 초과: {len(infos)} > {limits.max_entries}"
        )

    declared = sum(info.file_size for info in infos)
    if declared > limits.max_total_bytes:
        raise UnsafeArchiveError(
            f"전개 용량 상한 초과(선언값): {declared} > {limits.max_total_bytes}"
        )

    files: list[Path] = []
    total = 0
    for info in infos:
        name = decode_entry_name(info)
        if _is_symlink_entry(info):
            raise UnsafeArchiveError(f"심볼릭 링크 엔트리를 거부한다: {name!r}")

        target = _safe_destination(name, dest_root)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            while chunk := src.read(_CHUNK_BYTES):
                total += len(chunk)
                if total > limits.max_total_bytes:
                    raise UnsafeArchiveError(
                        f"전개 용량 상한 초과(실측): {total} > {limits.max_total_bytes}"
                    )
                dst.write(chunk)
        files.append(target)

    return ExtractResult(files=files, total_bytes=total)


def safe_extract(
    zip_path: Path,
    dest_root: Path,
    limits: ExtractLimits = ExtractLimits(),
) -> ExtractResult:
    """ZIP을 dest_root에 안전하게 전개한다.

    dest_root는 이 함수가 만든다. 실패하면 통째로 지우므로, 다른 내용이 들어
    있는 폴더를 넘기면 안 된다.

    Raises:
        UnsafeArchiveError: 안전 검사에 걸렸을 때. dest_root는 삭제된다.
    """
    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return _extract_all(zf, dest_root, limits)
    except Exception:
        shutil.rmtree(dest_root, ignore_errors=True)
        raise
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

```bash
uv run pytest tests/io/test_archive.py -v
```

Expected: 14 passed

- [ ] **Step 5: 커밋**

```bash
git add seg_and_mesh/io/archive.py tests/io/test_archive.py
git commit -m "feat: ZIP 안전 해제 (경로 순회·심링크·CP949·용량 상한)"
```

---

### Task 4: 입력 정규화 — 업로드 하나를 DICOM 목록 또는 NIfTI 하나로 (스펙 §6.1 끝)

ZIP 내부 파일은 전부 매직바이트 검사를 거쳐 DICOM만 추린다. 파일명은 보지 않는다.

**Files:**
- Create: `seg_and_mesh/io/ingest.py`
- Test: `tests/io/test_ingest.py`

**Interfaces:**
- Consumes:
  - `seg_and_mesh.io.detect.InputKind`, `detect_format(path) -> InputKind`
  - `seg_and_mesh.io.archive.safe_extract(zip_path, dest_root, limits) -> ExtractResult`, `ExtractLimits`, `UnsafeArchiveError`
- Produces:
  - `class SourceKind(str, Enum)` — 멤버 `DICOM_ZIP="dicom-zip"`, `DICOM_DIR="dicom-dir"`, `NIFTI="nifti"` (`status.json`의 `input.kind` 값과 같다, 스펙 §9.1)
  - `class UnsupportedInputError(Exception)`
  - `@dataclass(frozen=True) class PreparedInput` — 필드 `kind: SourceKind`, `dicom_dir: Path | None`, `dicom_files: list[Path]`, `nifti_file: Path | None`
  - `prepare_input(src: Path, workdir: Path, limits: ExtractLimits = ExtractLimits()) -> PreparedInput`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/io/test_ingest.py`:

```python
"""입력 정규화 테스트 (스펙 §6.1)."""

from __future__ import annotations

import gzip
import struct
import zipfile
from pathlib import Path

import pytest

from seg_and_mesh.io.ingest import (
    PreparedInput,
    SourceKind,
    UnsupportedInputError,
    prepare_input,
)


def _nifti1_bytes() -> bytes:
    hdr = bytearray(348)
    struct.pack_into("<i", hdr, 0, 348)
    hdr[344:348] = b"n+1\x00"
    return bytes(hdr) + b"\x00" * 100


def _dicom_bytes() -> bytes:
    return b"\x00" * 128 + b"DICM" + b"\x00" * 200


def test_nifti_file_is_copied_into_workdir(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(gzip.compress(_nifti1_bytes()))
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.NIFTI
    assert prepared.nifti_file is not None
    assert prepared.nifti_file.exists()
    assert prepared.nifti_file.parent == workdir
    assert prepared.dicom_files == []


def test_uncompressed_nifti_keeps_nii_suffix(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(_nifti1_bytes())
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.nifti_file.name == "input.nii"


def test_gzipped_nifti_keeps_nii_gz_suffix(tmp_path):
    src = tmp_path / "scan"
    src.write_bytes(gzip.compress(_nifti1_bytes()))
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.nifti_file.name == "input.nii.gz"


def test_zip_keeps_only_dicom_entries(tmp_path):
    """파일명은 보지 않는다 — 내용으로만 추린다."""
    src = tmp_path / "study.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("study/s1/IM000001", _dicom_bytes())
        zf.writestr("study/s1/IM000002", _dicom_bytes())
        zf.writestr("study/readme.txt", b"not an image")
        zf.writestr("__MACOSX/._IM000001", b"\x00\x05\x16\x07junk")
        zf.writestr("study/DICOMDIR", b"random bytes not dicom")
        zf.writestr("looks_like.dcm", b"totally not dicom either")
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) == 2
    assert {p.name for p in prepared.dicom_files} == {"IM000001", "IM000002"}
    assert prepared.dicom_dir is not None
    assert prepared.nifti_file is None


def test_directory_of_dicom_is_accepted(tmp_path):
    src = tmp_path / "series"
    src.mkdir()
    (src / "I10").write_bytes(_dicom_bytes())
    (src / "I11").write_bytes(_dicom_bytes())
    (src / "notes.txt").write_bytes(b"hello")
    workdir = tmp_path / "work"

    prepared = prepare_input(src, workdir)

    assert prepared.kind is SourceKind.DICOM_DIR
    assert len(prepared.dicom_files) == 2
    assert prepared.dicom_dir == src


def test_zip_without_dicom_is_rejected(tmp_path):
    src = tmp_path / "docs.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a.txt", b"hello")

    with pytest.raises(UnsupportedInputError, match="DICOM"):
        prepare_input(src, tmp_path / "work")


def test_garbage_file_is_rejected(tmp_path):
    src = tmp_path / "junk"
    src.write_bytes(b"not anything" * 50)

    with pytest.raises(UnsupportedInputError, match="판별"):
        prepare_input(src, tmp_path / "work")


def test_single_dicom_file_is_rejected(tmp_path):
    """DICOM 한 장으로는 볼륨을 못 만든다. 폴더나 ZIP을 요구한다."""
    src = tmp_path / "IM000001"
    src.write_bytes(_dicom_bytes())

    with pytest.raises(UnsupportedInputError, match="폴더|ZIP"):
        prepare_input(src, tmp_path / "work")


def test_empty_directory_is_rejected(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()

    with pytest.raises(UnsupportedInputError, match="DICOM"):
        prepare_input(src, tmp_path / "work")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_ingest.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seg_and_mesh.io.ingest'`

- [ ] **Step 3: `seg_and_mesh/io/ingest.py` 구현**

```python
"""업로드 입력 하나를 파이프라인이 쓸 형태로 정규화한다 (스펙 §6.1).

ZIP 내부 파일은 전부 매직바이트 검사를 거쳐 DICOM만 추린다. 파일명은 보지 않는다.
"""

from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from seg_and_mesh.io.archive import ExtractLimits, safe_extract
from seg_and_mesh.io.detect import InputKind, detect_format

#: ZIP 전개 결과를 두는 workdir 하위 폴더명
_EXTRACT_DIRNAME = "extracted"


class SourceKind(str, Enum):
    """정규화된 입력 종류. status.json의 input.kind 값과 같다 (스펙 §9.1)."""

    DICOM_ZIP = "dicom-zip"
    DICOM_DIR = "dicom-dir"
    NIFTI = "nifti"


class UnsupportedInputError(Exception):
    """파이프라인이 처리할 수 없는 입력이다."""


@dataclass(frozen=True)
class PreparedInput:
    """정규화 결과.

    DICOM 입력이면 dicom_dir/dicom_files가 채워지고 nifti_file은 None이다.
    NIfTI 입력이면 반대다.
    """

    kind: SourceKind
    dicom_dir: Path | None
    dicom_files: list[Path]
    nifti_file: Path | None


def collect_dicom_files(root: Path) -> list[Path]:
    """root 아래 모든 파일을 매직바이트로 검사해 DICOM만 정렬해 돌려준다."""
    found = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and detect_format(path) is InputKind.DICOM
    ]
    return found


def _nifti_target_name(src: Path) -> str:
    """gzip이면 input.nii.gz, 아니면 input.nii."""
    with open(src, "rb") as fh:
        is_gzip = fh.read(2) == b"\x1f\x8b"
    return "input.nii.gz" if is_gzip else "input.nii"


def _prepare_nifti(src: Path, workdir: Path) -> PreparedInput:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / _nifti_target_name(src)
    shutil.copyfile(src, target)
    return PreparedInput(
        kind=SourceKind.NIFTI, dicom_dir=None, dicom_files=[], nifti_file=target
    )


def _prepare_zip(src: Path, workdir: Path, limits: ExtractLimits) -> PreparedInput:
    extract_dir = workdir / _EXTRACT_DIRNAME
    safe_extract(src, extract_dir, limits)
    dicom_files = collect_dicom_files(extract_dir)
    if not dicom_files:
        raise UnsupportedInputError(f"ZIP 안에 DICOM 파일이 하나도 없다: {src}")
    return PreparedInput(
        kind=SourceKind.DICOM_ZIP,
        dicom_dir=extract_dir,
        dicom_files=dicom_files,
        nifti_file=None,
    )


def _prepare_dir(src: Path) -> PreparedInput:
    dicom_files = collect_dicom_files(src)
    if not dicom_files:
        raise UnsupportedInputError(f"폴더 안에 DICOM 파일이 하나도 없다: {src}")
    return PreparedInput(
        kind=SourceKind.DICOM_DIR,
        dicom_dir=src,
        dicom_files=dicom_files,
        nifti_file=None,
    )


def prepare_input(
    src: Path,
    workdir: Path,
    limits: ExtractLimits = ExtractLimits(),
) -> PreparedInput:
    """업로드 파일 또는 폴더 하나를 정규화한다.

    Raises:
        UnsupportedInputError: 판별 실패, DICOM 없음, 단일 DICOM 파일일 때.
        UnsafeArchiveError: ZIP이 안전 검사에 걸렸을 때.
    """
    src = Path(src)
    workdir = Path(workdir)

    if src.is_dir():
        return _prepare_dir(src)

    kind = detect_format(src)
    if kind is InputKind.ZIP:
        return _prepare_zip(src, workdir, limits)
    if kind is InputKind.NIFTI:
        return _prepare_nifti(src, workdir)
    if kind is InputKind.DICOM:
        raise UnsupportedInputError(
            f"DICOM 한 장으로는 볼륨을 만들 수 없다. 시리즈 폴더나 ZIP을 넣어야 한다: {src}"
        )
    raise UnsupportedInputError(f"입력 종류를 판별하지 못했다: {src}")
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

```bash
uv run pytest tests/io/test_ingest.py -v
```

Expected: 9 passed

- [ ] **Step 5: 실제 데이터 스모크 테스트 추가**

`tests/io/test_ingest.py` 끝에 덧붙인다:

```python
@pytest.mark.realdata
def test_real_zip_yields_dicom_files(real_data_dir, tmp_path):
    """SAM_TEST_DATA_DIR 안의 첫 ZIP을 실제로 정규화한다."""
    zips = sorted(real_data_dir.rglob("*.zip"))
    if not zips:
        pytest.skip("테스트 데이터에 ZIP이 없다")

    prepared = prepare_input(zips[0], tmp_path / "work")

    assert prepared.kind is SourceKind.DICOM_ZIP
    assert len(prepared.dicom_files) > 0
    print(f"\n{zips[0].name}: DICOM {len(prepared.dicom_files)}개")
```

- [ ] **Step 6: 실제 데이터로 실행**

```powershell
$env:SAM_TEST_DATA_DIR = "<실제 데이터 폴더>"
uv run pytest tests/io/test_ingest.py -v -m realdata -s
```

Expected: PASS (또는 ZIP이 없으면 skip)

- [ ] **Step 7: 커밋**

```bash
git add seg_and_mesh/io/ingest.py tests/io/test_ingest.py
git commit -m "feat: 입력 정규화 (ZIP/폴더/NIfTI를 DICOM 목록 또는 NIfTI로)"
```

---

### Task 5: dcm2niix 래핑 (스펙 §6.3 앞부분)

`dcm2niix`는 입력 디렉터리를 재귀 탐색하고(기본 깊이 5, `-d`로 조절) SeriesInstanceUID로 그룹핑한다. 따라서 중첩 폴더 구조가 그대로 처리된다.

**Files:**
- Create: `vendor/README.md` (바이너리 조달 방법 문서)
- Create: `seg_and_mesh/io/dcm2niix.py`
- Test: `tests/io/test_dcm2niix.py`

**Interfaces:**
- Consumes: 없음 (외부 바이너리만)
- Produces:
  - `class Dcm2niixError(RuntimeError)`
  - `@dataclass(frozen=True) class SeriesOutput` — 필드 `nifti_path: Path`, `sidecar_path: Path | None`, `series_number: int | None`, `series_description: str`, `slices: int`, `voxel_size_mm: tuple[float, float, float]`, `acquisition_type: str`
  - `find_dcm2niix() -> str`
  - `run_dcm2niix(dicom_dir: Path, out_dir: Path, depth: int = 5, binary: str | None = None) -> list[SeriesOutput]`
  - `describe_nifti(nifti_path: Path, sidecar_path: Path | None) -> SeriesOutput`

- [ ] **Step 1: `vendor/README.md` 작성 — 바이너리 조달 방법**

````markdown
# 외부 바이너리

이 폴더는 `.gitignore` 대상이다. 커밋하지 않는다.

## dcm2niix (Windows 로컬 개발용)

컨테이너 안에서는 api 이미지가 `dcm2niix`를 포함한다. 컨테이너 밖에서
테스트하려면 직접 받아야 한다.

```powershell
New-Item -ItemType Directory -Force vendor
Invoke-WebRequest `
  -Uri "https://github.com/rordenlab/dcm2niix/releases/latest/download/dcm2niix_win.zip" `
  -OutFile "vendor/dcm2niix_win.zip"
Expand-Archive -Path "vendor/dcm2niix_win.zip" -DestinationPath "vendor" -Force
$env:DCM2NIIX_BIN = (Resolve-Path "vendor/dcm2niix.exe").Path
& $env:DCM2NIIX_BIN -h | Select-Object -First 3
```

`DCM2NIIX_BIN`을 설정하면 `dcm2niix` 마커가 붙은 테스트가 실행된다.
설정하지 않으면 해당 테스트는 skip된다.
````

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/io/test_dcm2niix.py`:

```python
"""dcm2niix 래핑 테스트 (스펙 §6.3).

바이너리 실행이 필요 없는 부분(사이드카 파싱, 오류 처리)은 항상 실행하고,
실제 변환은 dcm2niix 마커로 분리한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from seg_and_mesh.io.dcm2niix import (
    Dcm2niixError,
    SeriesOutput,
    describe_nifti,
    find_dcm2niix,
    run_dcm2niix,
)


def _write_nifti(path: Path, shape=(256, 256, 176), zooms=(1.0, 1.0, 1.0)) -> Path:
    data = np.zeros(shape, dtype=np.uint8)
    img = nib.Nifti1Image(data, affine=np.eye(4))
    img.header.set_zooms(zooms)
    nib.save(img, path)
    return path


def test_describe_nifti_reads_shape_and_zooms(tmp_path):
    nifti = _write_nifti(tmp_path / "5_MPRAGE.nii.gz", (256, 256, 176), (1.0, 1.0, 1.0))
    sidecar = tmp_path / "5_MPRAGE.json"
    sidecar.write_text(
        json.dumps({
            "SeriesNumber": 5,
            "SeriesDescription": "MPRAGE",
            "MRAcquisitionType": "3D",
        }),
        encoding="utf-8",
    )

    out = describe_nifti(nifti, sidecar)

    assert out.series_number == 5
    assert out.series_description == "MPRAGE"
    assert out.slices == 176
    assert out.voxel_size_mm == pytest.approx((1.0, 1.0, 1.0))
    assert out.acquisition_type == "3D"


def test_describe_nifti_without_sidecar(tmp_path):
    """사이드카가 없어도 헤더만으로 기술한다."""
    nifti = _write_nifti(tmp_path / "vol.nii.gz", (256, 256, 30), (0.5, 0.5, 5.0))

    out = describe_nifti(nifti, None)

    assert out.series_number is None
    assert out.series_description == ""
    assert out.slices == 30
    assert out.voxel_size_mm == pytest.approx((0.5, 0.5, 5.0))
    assert out.acquisition_type == ""


def test_describe_nifti_handles_4d(tmp_path):
    """4D(예: DWI)면 slices는 3번째 축 길이를 쓴다."""
    data = np.zeros((64, 64, 20, 8), dtype=np.uint8)
    nifti = tmp_path / "dwi.nii.gz"
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), nifti)

    out = describe_nifti(nifti, None)

    assert out.slices == 20


def test_describe_nifti_tolerates_broken_sidecar(tmp_path):
    nifti = _write_nifti(tmp_path / "vol.nii.gz")
    sidecar = tmp_path / "vol.json"
    sidecar.write_text("{ not valid json", encoding="utf-8")

    out = describe_nifti(nifti, sidecar)

    assert out.series_description == ""


def test_find_dcm2niix_uses_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "dcm2niix.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("DCM2NIIX_BIN", str(fake))

    assert find_dcm2niix() == str(fake)


def test_find_dcm2niix_raises_when_missing(monkeypatch):
    monkeypatch.delenv("DCM2NIIX_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(Dcm2niixError, match="dcm2niix"):
        find_dcm2niix()


def test_run_dcm2niix_raises_on_nonzero_exit(tmp_path, monkeypatch):
    """종료 코드가 0이 아니면 표준에러 꼬리를 담아 예외를 던진다."""
    import subprocess

    class _Result:
        returncode = 2
        stdout = "out"
        stderr = "Error: unable to read directory\n" * 3

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    with pytest.raises(Dcm2niixError, match="unable to read directory"):
        run_dcm2niix(dicom_dir, tmp_path / "out")


def test_run_dcm2niix_raises_when_no_output(tmp_path, monkeypatch):
    import subprocess

    class _Result:
        returncode = 0
        stdout = "Conversion required 0 files"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setenv("DCM2NIIX_BIN", "fake-binary")

    dicom_dir = tmp_path / "dcm"
    dicom_dir.mkdir()

    with pytest.raises(Dcm2niixError, match="NIfTI"):
        run_dcm2niix(dicom_dir, tmp_path / "out")


@pytest.mark.dcm2niix
@pytest.mark.realdata
def test_run_dcm2niix_on_real_data(real_data_dir, dcm2niix_bin, tmp_path):
    """실제 DICOM 폴더/ZIP을 변환해 시리즈 목록이 나오는지 본다."""
    from seg_and_mesh.io.ingest import prepare_input

    candidates = sorted(real_data_dir.rglob("*.zip")) or [real_data_dir]
    prepared = prepare_input(candidates[0], tmp_path / "work")
    assert prepared.dicom_dir is not None

    series = run_dcm2niix(prepared.dicom_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    assert len(series) > 0
    for s in series:
        print(f"\n#{s.series_number} {s.series_description!r} "
              f"slices={s.slices} vox={s.voxel_size_mm} type={s.acquisition_type}")
        assert s.nifti_path.exists()
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_dcm2niix.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seg_and_mesh.io.dcm2niix'`

- [ ] **Step 4: numpy 의존성 추가**

nibabel이 numpy를 끌어오지만 테스트가 직접 임포트하므로 명시한다.

```bash
uv add numpy
```

- [ ] **Step 5: `seg_and_mesh/io/dcm2niix.py` 구현**

```python
"""dcm2niix 래핑 (스펙 §6.3).

dcm2niix는 입력 디렉터리를 재귀 탐색하고(-d로 깊이 조절) SeriesInstanceUID로
그룹핑한다. study/series1/*, study/series2/* 같은 중첩 구조가 그대로 처리된다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib

#: 표준에러가 길 때 예외 메시지에 담을 꼬리 길이
_STDERR_TAIL_CHARS = 2000

#: 출력 파일명 패턴 — %s 시리즈 번호, %d 시리즈 설명
_FILENAME_PATTERN = "%s_%d"


class Dcm2niixError(RuntimeError):
    """dcm2niix 실행 또는 결과 해석에 실패했다."""


@dataclass(frozen=True)
class SeriesOutput:
    """변환된 시리즈 하나."""

    nifti_path: Path
    sidecar_path: Path | None
    series_number: int | None
    series_description: str
    slices: int
    voxel_size_mm: tuple[float, float, float]
    acquisition_type: str


def find_dcm2niix() -> str:
    """dcm2niix 실행 파일 경로를 찾는다. DCM2NIIX_BIN이 PATH보다 우선한다."""
    override = os.environ.get("DCM2NIIX_BIN")
    if override:
        return override
    found = shutil.which("dcm2niix")
    if found:
        return found
    raise Dcm2niixError(
        "dcm2niix 실행 파일을 찾지 못했다. PATH에 넣거나 DCM2NIIX_BIN을 설정해야 한다."
    )


def _load_sidecar(sidecar_path: Path | None) -> dict:
    """BIDS 사이드카 JSON을 읽는다. 없거나 깨졌으면 빈 dict."""
    if sidecar_path is None or not sidecar_path.exists():
        return {}
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def describe_nifti(nifti_path: Path, sidecar_path: Path | None) -> SeriesOutput:
    """NIfTI 헤더와 사이드카에서 시리즈 선택에 필요한 정보를 뽑는다."""
    meta = _load_sidecar(sidecar_path)
    img = nib.load(nifti_path)
    zooms = tuple(float(z) for z in img.header.get_zooms()[:3])
    shape = img.shape
    slices = int(shape[2]) if len(shape) >= 3 else 0

    series_number = meta.get("SeriesNumber")
    return SeriesOutput(
        nifti_path=Path(nifti_path),
        sidecar_path=Path(sidecar_path) if sidecar_path else None,
        series_number=int(series_number) if series_number is not None else None,
        series_description=str(meta.get("SeriesDescription", "")),
        slices=slices,
        voxel_size_mm=zooms,
        acquisition_type=str(meta.get("MRAcquisitionType", "")),
    )


def _collect_outputs(out_dir: Path) -> list[SeriesOutput]:
    outputs: list[SeriesOutput] = []
    for nifti_path in sorted(out_dir.rglob("*.nii*")):
        if nifti_path.name.endswith(".nii.gz"):
            stem = nifti_path.name[: -len(".nii.gz")]
        else:
            stem = nifti_path.stem
        sidecar = nifti_path.parent / f"{stem}.json"
        outputs.append(describe_nifti(nifti_path, sidecar if sidecar.exists() else None))
    return outputs


def run_dcm2niix(
    dicom_dir: Path,
    out_dir: Path,
    depth: int = 5,
    binary: str | None = None,
) -> list[SeriesOutput]:
    """DICOM 폴더를 시리즈별 .nii.gz + BIDS 사이드카로 변환한다.

    Args:
        dicom_dir: DICOM이 들어 있는 폴더. 하위 폴더도 depth까지 탐색한다.
        out_dir: 변환 결과를 둘 폴더. 없으면 만든다.
        depth: 재귀 탐색 깊이 (dcm2niix -d).
        binary: 실행 파일 경로. None이면 find_dcm2niix()로 찾는다.

    Raises:
        Dcm2niixError: 실행 실패, 종료 코드 비0, 또는 결과 NIfTI가 없을 때.
    """
    dicom_dir = Path(dicom_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = binary or find_dcm2niix()

    cmd = [
        exe,
        "-d", str(depth),   # 재귀 탐색 깊이
        "-z", "y",          # gzip 압축
        "-b", "y",          # BIDS 사이드카 생성
        "-f", _FILENAME_PATTERN,
        "-o", str(out_dir),
        str(dicom_dir),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise Dcm2niixError(f"dcm2niix 실행에 실패했다: {exc}") from exc

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-_STDERR_TAIL_CHARS:]
        raise Dcm2niixError(
            f"dcm2niix가 종료 코드 {result.returncode}로 끝났다.\n"
            f"명령: {' '.join(cmd)}\n표준에러 꼬리:\n{tail}"
        )

    outputs = _collect_outputs(out_dir)
    if not outputs:
        tail = (result.stdout or "")[-_STDERR_TAIL_CHARS:]
        raise Dcm2niixError(
            f"dcm2niix가 NIfTI를 하나도 만들지 않았다.\n"
            f"명령: {' '.join(cmd)}\n표준출력 꼬리:\n{tail}"
        )
    return outputs
```

- [ ] **Step 6: 테스트가 통과하는지 확인**

```bash
uv run pytest tests/io/test_dcm2niix.py -v
```

Expected: 8 passed, 1 skipped (`test_run_dcm2niix_on_real_data`)

- [ ] **Step 7: dcm2niix 바이너리를 받아 실데이터로 실행**

`vendor/README.md`의 절차대로 바이너리를 받은 뒤:

```powershell
$env:DCM2NIIX_BIN = (Resolve-Path "vendor/dcm2niix.exe").Path
$env:SAM_TEST_DATA_DIR = "<실제 데이터 폴더>"
uv run pytest tests/io/test_dcm2niix.py -v -m "dcm2niix and realdata" -s
```

Expected: PASS. 출력에 시리즈 목록(번호·설명·슬라이스 수·voxel size·3D 여부)이 찍힌다. 이 출력을 Task 6의 추천 로직 검증에 쓴다.

- [ ] **Step 8: 커밋**

```bash
git add seg_and_mesh/io/dcm2niix.py tests/io/test_dcm2niix.py vendor/README.md pyproject.toml uv.lock
git commit -m "feat: dcm2niix 래핑과 시리즈 메타데이터 추출"
```

---

### Task 6: T1 시리즈 추천 (스펙 §6.3 뒷부분)

**자동 선택만으로 진행하지 않는다.** 이 모듈은 점수순 목록만 만든다. 확정은 사용자 몫이다 — FastSurfer는 1mm T1w를 전제하므로 잘못된 시리즈를 넣으면 결과가 조용히 망가진다.

**Files:**
- Create: `seg_and_mesh/io/series.py`
- Test: `tests/io/test_series.py`

**Interfaces:**
- Consumes: `seg_and_mesh.io.dcm2niix.SeriesOutput`
- Produces:
  - `T1_DESCRIPTION_PATTERN: re.Pattern` — `mprage|t1|bravo|spgr|tfl`, 대소문자 무시
  - `@dataclass(frozen=True) class SeriesCandidate` — 필드 `series: SeriesOutput`, `score: int`, `reasons: list[str]`
  - `score_series(series: SeriesOutput) -> SeriesCandidate`
  - `rank_series(series_list: list[SeriesOutput]) -> list[SeriesCandidate]` — 점수 내림차순, 동점이면 슬라이스 수 내림차순, 그다음 시리즈 번호 오름차순

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/io/test_series.py`:

```python
"""T1 시리즈 추천 테스트 (스펙 §6.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from seg_and_mesh.io.dcm2niix import SeriesOutput
from seg_and_mesh.io.series import rank_series, score_series


def _series(
    description="",
    slices=176,
    vox=(1.0, 1.0, 1.0),
    acq="3D",
    number=1,
) -> SeriesOutput:
    return SeriesOutput(
        nifti_path=Path(f"{number}.nii.gz"),
        sidecar_path=None,
        series_number=number,
        series_description=description,
        slices=slices,
        voxel_size_mm=vox,
        acquisition_type=acq,
    )


def test_ideal_mprage_scores_highest():
    ideal = score_series(_series("MPRAGE", 176, (1.0, 1.0, 1.0), "3D"))
    poor = score_series(_series("AX FLAIR", 24, (0.5, 0.5, 5.0), "2D"))

    assert ideal.score > poor.score


@pytest.mark.parametrize("description", [
    "MPRAGE", "mprage", "T1w", "3D BRAVO", "SPGR", "t1_mprage_sag_p2_iso", "tfl3d",
])
def test_t1_descriptions_are_recognized(description):
    candidate = score_series(_series(description))
    assert any("설명" in r for r in candidate.reasons)


@pytest.mark.parametrize("description", ["AX FLAIR", "DWI", "SWI", "Localizer"])
def test_non_t1_descriptions_are_not_recognized(description):
    candidate = score_series(_series(description))
    assert not any("설명" in r for r in candidate.reasons)


def test_anisotropic_voxel_loses_points():
    isotropic = score_series(_series("MPRAGE", vox=(1.0, 1.0, 1.0)))
    anisotropic = score_series(_series("MPRAGE", vox=(0.5, 0.5, 5.0)))

    assert isotropic.score > anisotropic.score


def test_thin_stack_loses_points():
    thick = score_series(_series("MPRAGE", slices=176))
    thin = score_series(_series("MPRAGE", slices=40))

    assert thick.score > thin.score


def test_slice_threshold_is_128():
    """스펙 §6.3 — 슬라이스 128장 이상."""
    at_threshold = score_series(_series("MPRAGE", slices=128))
    below = score_series(_series("MPRAGE", slices=127))

    assert at_threshold.score > below.score


def test_ranking_puts_best_first():
    ranked = rank_series([
        _series("Localizer", 3, (1.5, 1.5, 8.0), "2D", number=1),
        _series("AX T2 FLAIR", 30, (0.5, 0.5, 5.0), "2D", number=2),
        _series("Sag 3D T1 MPRAGE", 176, (1.0, 1.0, 1.0), "3D", number=3),
    ])

    assert [c.series.series_number for c in ranked] == [3, 2, 1]


def test_ranking_breaks_ties_by_slice_count():
    ranked = rank_series([
        _series("MPRAGE", 160, (1.0, 1.0, 1.0), "3D", number=7),
        _series("MPRAGE", 192, (1.0, 1.0, 1.0), "3D", number=4),
    ])

    assert ranked[0].series.series_number == 4


def test_ranking_empty_list():
    assert rank_series([]) == []


def test_reasons_are_human_readable():
    candidate = score_series(_series("MPRAGE", 176, (1.0, 1.0, 1.0), "3D"))

    joined = " ".join(candidate.reasons)
    assert "등방성" in joined
    assert "슬라이스" in joined
    assert "설명" in joined
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_series.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seg_and_mesh.io.series'`

- [ ] **Step 3: `seg_and_mesh/io/series.py` 구현**

```python
"""T1 후보 시리즈 추천 (스펙 §6.3).

추천 기준: 등방성에 가까운 voxel(약 1mm), 슬라이스 128장 이상,
description이 mprage|t1|bravo|spgr|tfl 매칭.

이 모듈은 순위만 매긴다. 자동 확정하지 않는다. FastSurfer는 1mm T1w를
전제하므로 잘못된 시리즈를 넣으면 결과가 조용히 망가진다. 사용자 확인이 필수다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from seg_and_mesh.io.dcm2niix import SeriesOutput

#: T1 계열 시퀀스 이름 (스펙 §6.3)
T1_DESCRIPTION_PATTERN = re.compile(r"mprage|t1|bravo|spgr|tfl", re.IGNORECASE)

#: 등방성 판정 — 최대/최소 voxel 변 길이 비
_MAX_ISOTROPY_RATIO = 1.2

#: 1mm 근처 판정 범위 (mm)
_MIN_VOXEL_MM = 0.7
_MAX_VOXEL_MM = 1.4

#: 최소 슬라이스 수
_MIN_SLICES = 128

_SCORE_DESCRIPTION = 3
_SCORE_ISOTROPIC = 2
_SCORE_NEAR_1MM = 2
_SCORE_SLICES = 2
_SCORE_3D = 1


@dataclass(frozen=True)
class SeriesCandidate:
    """시리즈 하나의 점수와 근거. reasons는 UI에 그대로 보여준다."""

    series: SeriesOutput
    score: int
    reasons: list[str]


def _is_isotropic(vox: tuple[float, float, float]) -> bool:
    valid = [v for v in vox if v > 0]
    if len(valid) < 3:
        return False
    return max(valid) / min(valid) <= _MAX_ISOTROPY_RATIO


def _is_near_1mm(vox: tuple[float, float, float]) -> bool:
    valid = [v for v in vox if v > 0]
    if len(valid) < 3:
        return False
    return all(_MIN_VOXEL_MM <= v <= _MAX_VOXEL_MM for v in valid)


def score_series(series: SeriesOutput) -> SeriesCandidate:
    """시리즈 하나에 점수를 매기고 근거를 남긴다."""
    score = 0
    reasons: list[str] = []

    if T1_DESCRIPTION_PATTERN.search(series.series_description):
        score += _SCORE_DESCRIPTION
        reasons.append(f"설명이 T1 패턴과 일치: {series.series_description!r}")

    if _is_isotropic(series.voxel_size_mm):
        score += _SCORE_ISOTROPIC
        reasons.append(f"등방성 voxel: {series.voxel_size_mm}")

    if _is_near_1mm(series.voxel_size_mm):
        score += _SCORE_NEAR_1MM
        reasons.append(f"voxel이 1mm 근처: {series.voxel_size_mm}")

    if series.slices >= _MIN_SLICES:
        score += _SCORE_SLICES
        reasons.append(f"슬라이스 {series.slices}장 (>= {_MIN_SLICES})")
    else:
        reasons.append(f"슬라이스 {series.slices}장 (< {_MIN_SLICES})")

    if series.acquisition_type.upper() == "3D":
        score += _SCORE_3D
        reasons.append("3D 획득")

    return SeriesCandidate(series=series, score=score, reasons=reasons)


def rank_series(series_list: list[SeriesOutput]) -> list[SeriesCandidate]:
    """점수 내림차순으로 정렬한다. 동점이면 슬라이스 수, 그다음 시리즈 번호 순."""
    candidates = [score_series(s) for s in series_list]
    return sorted(
        candidates,
        key=lambda c: (
            -c.score,
            -c.series.slices,
            c.series.series_number if c.series.series_number is not None else 1 << 30,
        ),
    )
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

```bash
uv run pytest tests/io/test_series.py -v
```

Expected: 19 passed

- [ ] **Step 5: 실데이터 추천 결과를 눈으로 확인하는 테스트 추가**

`tests/io/test_series.py` 끝에 덧붙인다:

```python
@pytest.mark.dcm2niix
@pytest.mark.realdata
def test_real_data_ranking_puts_t1_first(real_data_dir, dcm2niix_bin, tmp_path):
    """실제 스터디에서 1위가 T1인지 눈으로 확인한다.

    자동 확정은 하지 않으므로 이 테스트는 순위 출력이 목적이다. 1위가 T1이
    아니면 점수 가중치를 조정하고 회귀 케이스를 추가한다.
    """
    from seg_and_mesh.io.dcm2niix import run_dcm2niix
    from seg_and_mesh.io.ingest import prepare_input

    candidates = sorted(real_data_dir.rglob("*.zip")) or [real_data_dir]
    prepared = prepare_input(candidates[0], tmp_path / "work")
    series = run_dcm2niix(prepared.dicom_dir, tmp_path / "nifti", binary=dcm2niix_bin)

    ranked = rank_series(series)

    print("\n추천 순위:")
    for i, c in enumerate(ranked, 1):
        print(f"  {i}. score={c.score} #{c.series.series_number} "
              f"{c.series.series_description!r} slices={c.series.slices} "
              f"vox={c.series.voxel_size_mm}")
        for reason in c.reasons:
            print(f"       - {reason}")

    assert ranked[0].score >= ranked[-1].score
```

- [ ] **Step 6: 실데이터로 실행**

```powershell
$env:DCM2NIIX_BIN = (Resolve-Path "vendor/dcm2niix.exe").Path
$env:SAM_TEST_DATA_DIR = "<실제 데이터 폴더>"
uv run pytest tests/io/test_series.py -v -m "dcm2niix and realdata" -s
```

Expected: PASS. 1위가 T1(MPRAGE 등)인지 확인한다. 아니면 가중치를 고치고 그 케이스를 `_series(...)` 합성 테스트로 고정한다.

- [ ] **Step 7: 커밋**

```bash
git add seg_and_mesh/io/series.py tests/io/test_series.py
git commit -m "feat: T1 후보 시리즈 점수화와 순위 (자동 확정 없음)"
```

---

### Task 7: io 공개 API 정리와 전체 회귀

`seg_and_mesh.io`를 임포트하면 파이프라인이 필요로 하는 것이 다 나오게 만든다. 뒤 계획(`jobs/`, `web/`)이 내부 모듈 경로에 묶이지 않도록 한다.

**Files:**
- Modify: `seg_and_mesh/io/__init__.py`
- Test: `tests/io/test_public_api.py`

**Interfaces:**
- Consumes: Task 2~6의 모든 공개 이름
- Produces: `seg_and_mesh.io.__all__` — `InputKind`, `detect_format`, `ExtractLimits`, `ExtractResult`, `UnsafeArchiveError`, `safe_extract`, `SourceKind`, `PreparedInput`, `UnsupportedInputError`, `prepare_input`, `SeriesOutput`, `Dcm2niixError`, `run_dcm2niix`, `SeriesCandidate`, `rank_series`, `score_series`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/io/test_public_api.py`:

```python
"""io 패키지 공개 API 계약.

뒤 계획(jobs/, web/)은 이 이름들만 쓴다. 내부 모듈 경로에 묶이지 않게 한다.
"""

import seg_and_mesh.io as sam_io

EXPECTED = {
    "InputKind",
    "detect_format",
    "ExtractLimits",
    "ExtractResult",
    "UnsafeArchiveError",
    "safe_extract",
    "SourceKind",
    "PreparedInput",
    "UnsupportedInputError",
    "prepare_input",
    "SeriesOutput",
    "Dcm2niixError",
    "run_dcm2niix",
    "SeriesCandidate",
    "rank_series",
    "score_series",
}


def test_all_names_are_exported():
    assert set(sam_io.__all__) == EXPECTED


def test_all_names_are_importable():
    for name in EXPECTED:
        assert hasattr(sam_io, name), f"{name}이 노출되지 않았다"


def test_source_kind_values_match_status_json_contract():
    """스펙 §9.1의 status.json input.kind 값과 일치해야 한다."""
    assert sam_io.SourceKind.DICOM_ZIP.value == "dicom-zip"
    assert sam_io.SourceKind.DICOM_DIR.value == "dicom-dir"
    assert sam_io.SourceKind.NIFTI.value == "nifti"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/io/test_public_api.py -v
```

Expected: FAIL — `AttributeError: module 'seg_and_mesh.io' has no attribute '__all__'`

- [ ] **Step 3: `seg_and_mesh/io/__init__.py` 채우기**

```python
"""입력 판별, ZIP 안전 해제, dcm2niix 래핑, 시리즈 추천 (스펙 §6.1~§6.3).

파이프라인 1~4단계를 담당한다. 도커·FastSurfer·잡 큐에 의존하지 않으므로
로컬에서 전 구간 테스트된다.
"""

from seg_and_mesh.io.archive import (
    ExtractLimits,
    ExtractResult,
    UnsafeArchiveError,
    safe_extract,
)
from seg_and_mesh.io.dcm2niix import Dcm2niixError, SeriesOutput, run_dcm2niix
from seg_and_mesh.io.detect import InputKind, detect_format
from seg_and_mesh.io.ingest import (
    PreparedInput,
    SourceKind,
    UnsupportedInputError,
    prepare_input,
)
from seg_and_mesh.io.series import SeriesCandidate, rank_series, score_series

__all__ = [
    # detect
    "InputKind",
    "detect_format",
    # archive
    "ExtractLimits",
    "ExtractResult",
    "UnsafeArchiveError",
    "safe_extract",
    # ingest
    "SourceKind",
    "PreparedInput",
    "UnsupportedInputError",
    "prepare_input",
    # dcm2niix
    "SeriesOutput",
    "Dcm2niixError",
    "run_dcm2niix",
    # series
    "SeriesCandidate",
    "rank_series",
    "score_series",
]
```

- [ ] **Step 4: 전체 테스트 실행**

```bash
uv run pytest -v
```

Expected: 전부 PASS. `realdata`/`dcm2niix` 마커 테스트는 환경 변수 미설정이면 skip.

- [ ] **Step 5: 환경 변수를 설정한 전체 실행**

```powershell
$env:DCM2NIIX_BIN = (Resolve-Path "vendor/dcm2niix.exe").Path
$env:SAM_TEST_DATA_DIR = "<실제 데이터 폴더>"
uv run pytest -v
```

Expected: 전부 PASS, skip 없음.

- [ ] **Step 6: 커밋**

```bash
git add seg_and_mesh/io/__init__.py tests/io/test_public_api.py
git commit -m "feat: io 패키지 공개 API 확정"
```

---

### Task 8: FastSurfer 이미지 확보와 LUT 추출 (다음 계획 준비)

labels 계획은 `labels/canonical-v1.tsv`를 FastSurfer 이미지의 `FreeSurferColorLUT.txt`에서 생성한다. 이미지 pull이 오래 걸리므로 io 계획 마지막에 미리 끝내 둔다. **이 태스크는 io 코드에 영향을 주지 않는다.**

**Files:**
- Create: `labels/FreeSurferColorLUT.txt` (gitignore 대상 — 커밋하지 않는다)
- Modify: `.env.example` (확인한 실제 태그를 주석으로 기록)
- Create: `docs/superpowers/notes/2026-07-22-fastsurfer-image.md`

**Interfaces:**
- Consumes: 없음
- Produces: `labels/FreeSurferColorLUT.txt` — labels 계획의 `labels/build_from_lut.py` 입력

- [ ] **Step 1: 레지스트리에서 사용 가능한 태그 확인**

`latest`는 쓰지 않는다 (스펙 §6.4).

> **실행 후 정정 (2026-07-22).** GPU 이미지 태그 명명이 바뀌었다. `gpu-v*`는
> `gpu-v2.2.0`에서 멈췄고, 현재 GPU 판은 `cuda-v<버전>`(별칭 `gpu-latest`)이며
> CUDA 버전별 변형 `cu118-` / `cu126-` / `cu128-`이 따로 있다. `gpu-*`로만
> 거르면 3년 묵은 태그를 고르게 된다. 태그가 61개라 페이지네이션도 필요하다.

```powershell
$all = @()
$url = "https://hub.docker.com/v2/repositories/deepmi/fastsurfer/tags?page_size=100"
for ($i = 0; $i -lt 5 -and $url; $i++) {
    $r = Invoke-RestMethod $url
    $all += $r.results
    $url = $r.next
}
$all | Sort-Object { $_.last_updated } -Descending |
    Select-Object -First 20 name,
        @{n='updated'; e={ ([string]$_.last_updated).Substring(0, 10) }},
        @{n='GB';      e={ [math]::Round($_.full_size / 1GB, 2) }} |
    Format-Table -AutoSize
```

Expected: `cuda-v2.x.x`, `cu128-v2.x.x`, `cpu-v2.x.x` 형식 태그 목록. 가장 최신
안정 버전의 `cuda-` 판을 고른다. CPU 폴백(스펙 §6.4)이 필요하면 같은 버전의
`cpu-` 판을 함께 받는다.

호스트 드라이버가 고른 CUDA 판을 감당하는지 먼저 본다.

```powershell
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
```

- [ ] **Step 2: 이미지 pull**

`<TAG>`를 Step 1에서 고른 태그로 바꾼다.

```powershell
docker pull deepmi/fastsurfer:<TAG>
```

Expected: pull 완료. 수 GB이므로 시간이 걸린다.

- [ ] **Step 3: 이미지 안의 LUT 경로 찾기**

```powershell
docker run --rm --entrypoint find deepmi/fastsurfer:<TAG> / -name "FreeSurferColorLUT.txt" -not -path "*/proc/*"
```

Expected: 경로가 1개 이상 출력된다 (예: `/fastsurfer/FastSurferCNN/config/FreeSurferColorLUT.txt`).

- [ ] **Step 4: LUT 추출**

`<LUT_PATH>`를 Step 3의 결과로 바꾼다.

```powershell
New-Item -ItemType Directory -Force labels
docker create --name sam-lut deepmi/fastsurfer:<TAG>
docker cp "sam-lut:<LUT_PATH>" labels/FreeSurferColorLUT.txt
docker rm sam-lut
(Get-Content labels/FreeSurferColorLUT.txt | Measure-Object -Line).Lines
Get-Content labels/FreeSurferColorLUT.txt -TotalCount 5
```

Expected: 줄 수가 1000줄 이상. 선두에 FreeSurfer LUT 주석 헤더가 보인다.

- [ ] **Step 5: DKT 라벨이 실제로 들어 있는지 확인**

```powershell
Select-String -Path labels/FreeSurferColorLUT.txt -Pattern "^\s*(17|53|251|255)\s" | Select-Object -First 10
```

Expected: `17 Left-Hippocampus`, `53 Right-Hippocampus`, `251 CC_Posterior`, `255 CC_Anterior`가 나온다. 뇌량(`CC_*`)이 없으면 다른 LUT를 잡은 것이므로 Step 3으로 돌아간다.

- [ ] **Step 6: 확인 결과를 노트로 기록**

`docs/superpowers/notes/2026-07-22-fastsurfer-image.md`:

````markdown
# FastSurfer 이미지 확인 결과

- 확인일: 2026-07-22
- 태그: `deepmi/fastsurfer:<TAG>`   (latest는 쓰지 않는다 — 스펙 §6.4)
- 이미지 안 LUT 경로: `<LUT_PATH>`
- 추출 위치: `labels/FreeSurferColorLUT.txt` (gitignore 대상, 커밋하지 않는다)
- LUT 줄 수: `<줄 수>`
- 뇌량 라벨(251~255) 포함 여부: 확인됨

## 재현 명령

```powershell
docker create --name sam-lut deepmi/fastsurfer:<TAG>
docker cp "sam-lut:<LUT_PATH>" labels/FreeSurferColorLUT.txt
docker rm sam-lut
```

## 다음 계획에서 쓰는 곳

`labels/build_from_lut.py`가 이 파일을 읽어 `labels/canonical-v1.tsv`를 생성한다.
생성 결과물(tsv)만 저장소에 커밋하고, 런타임에는 LUT를 읽지 않는다 (스펙 §3).
````

- [ ] **Step 7: `.env.example`에 확인한 태그 기록**

`.env.example`의 `FASTSURFER_IMAGE=` 줄 바로 위 주석에 실제 태그를 덧붙인다.

```dotenv
# FastSurfer 이미지 태그. 특정 버전으로 고정한다. latest는 쓰지 않는다. 필수.
# 2026-07-22 확인: deepmi/fastsurfer:<TAG>
FASTSURFER_IMAGE=
```

- [ ] **Step 8: 커밋**

`labels/FreeSurferColorLUT.txt`는 gitignore 대상이므로 커밋되지 않는다. 확인한다.

```bash
git status --porcelain
git add docs/superpowers/notes .env.example
git commit -m "docs: FastSurfer 이미지 태그와 LUT 추출 경로 확인 결과 기록"
```

Expected: `git status`에 `labels/FreeSurferColorLUT.txt`가 나타나지 않는다.

---

## 이 계획에서 다루지 않는 것

스펙의 나머지 부분은 후속 계획으로 넘긴다.

| 계획 | 스펙 절 | 내용 |
|---|---|---|
| labels | §3, §2.2 | `canonical-v1.tsv` 생성, 정적 LUT, 리맵, uint8 NIfTI 저장 |
| mesh | §6.5~§6.7, §7.1, §7.2 | 3축 변형 생성, GLB 작성, 지표 계산 |
| segment + jobs | §5.1, §6.4, §7, §9, §12 | docker socket 실행, 잡 큐, status.json, 스토리지 정리 |
| web | §4, §8 | FastAPI 라우트, three.js 뷰어, compose |
