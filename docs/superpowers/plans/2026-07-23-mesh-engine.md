# Mesh 엔진 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** canonical `seg.nii.gz` 하나와 파라미터 조합(3축 + min-voxel)을 받아 변형 하나(`regions.glb` + `metrics.json` + `params.json` + regions 메타)를 만드는 순수 엔진.

**Architecture:** 라벨별 파이프라인. 각 canonical 라벨의 이진 마스크에 축1(전처리)→축2(추출)→축3(후처리)를 적용해 메시를 만들고, 정점에 affine을 **한 번만** 적용해 world mm로 옮긴 뒤 `label_<id>` 노드로 GLB에 담는다. 지표(특히 `volumeErrorPct`)를 자동 계산한다. 도커·FastSurfer·웹에 의존하지 않아 로컬에서 전 구간 테스트된다(spec §10).

**Tech Stack:** numpy, nibabel, scipy(ndimage), scikit-image, vtk, trimesh. 새 런타임 의존성은 이 넷뿐(pyproject에 이미 추가됨: pyvista·scikit-image·trimesh, scipy·vtk는 전이 의존).

## Global Constraints

- spec §6.7: **GLB는 형상 + `label_<id>` 노드명만.** 색을 material에 굽지 않는다. 색·group·side·부피·centroid·triangle 수는 소비 측이 `regions-meta.json`에서 읽는다.
- spec §6.7: **좌표는 mm world.** voxel→world affine을 **정점에 한 번만** 적용한다. 추출기 격자 spacing과 affine을 함께 곱하는 이중 스케일링 금지(brainds 함정, `brain-educate/docs/todo.md §6.1`).
- spec §6.7: 원점 재배치 안 한다. 뷰어가 bbox 중심을 잡는다.
- spec §6.5: 축 구현체를 큐레이션하지 않고 전부 노출한다. 도구가 프리셋을 고르거나 지표로 자동 합격/불합격을 매기지 않는다.
- spec §6.5: **min-voxel 노브.** 라벨별 voxel 수가 임계 미만이면 GLB 노드도 regions 항목도 안 만든다. 기본 100. 스킵된 라벨 수를 metrics에 남긴다.
- spec §7.1: `params.json` 키는 camelCase(`sigmaVox`, `passBand`, `featureAngle`, `targetRatio`, `variantId`, `createdAt`, `labelTable`, `segSource`).
- spec §7: `variantId`는 `v<순번 2자리>-<파라미터 해시 앞 4자리>` (예: `v07-3c81`).
- spec §7.2: 지표는 판단 근거일 뿐 자동 게이트가 아니다. 계산·기록만 한다.
- spec §2.2: `volumeMm3`는 **리맵 전** voxel count × voxel 부피. 여기선 canonical seg의 라벨별 voxel count × voxel 부피(mesh 부피가 아니다).
- 테스트는 `uv run pytest -W error`로 통과해야 한다. 무거운 실데이터 테스트는 `@pytest.mark.realdata`로 opt-in.
- 이 엔진은 `seg.nii.gz`가 **이미 canonical id로 리맵됨**을 전제한다(labels 서브시스템 산출). 리맵을 다시 하지 않는다.

## File Structure

```
seg_and_mesh/mesh/
  __init__.py       공개 API
  params.py         MeshParams + 하위 파라미터 dataclass, variant_id, params.json 직렬화
  preprocess.py     축1: 이진 마스크 -> (스칼라장, isolevel)
  extract.py        축2: (스칼라장, isolevel) -> (정점[voxel], 면). 이름->구현 레지스트리
  postprocess.py    축3: 스무딩·데시메이션. (정점, 면) -> (정점, 면)
  glb.py            label_<id> 노드로 GLB 작성 (색 안 구움)
  metrics.py        라벨별·전체 지표 계산
  generate.py       조합: seg + params -> 변형 하나
tests/mesh/
  __init__.py
  conftest.py       합성 라벨 볼륨 픽스처(구·큐브·판)
  test_params.py
  test_preprocess.py
  test_extract.py
  test_postprocess.py
  test_glb.py
  test_metrics.py
  test_generate.py
```

## 범위 밖

- 변형 관리·중복 감지·잡 디렉터리 오케스트레이션 — jobs 계획
- 뷰어(three.js), 좌우 비교, 정본 커밋 UI — web 계획
- `regions-meta.json` **파일** 작성(전체 헤더·space·source) — jobs/web 계획. 이 엔진은 regions 배열에 들어갈 라벨별 dict를 **반환**만 한다.
- FastSurfer 실행·remap — segment/labels 계획
- meshopt/draco 압축 — 별도 스크립트(spec §6.6)
- `hc_laplacian` 스무딩(pymeshlab 의존) — 후속. 이 계획은 none/laplacian/taubin/humphrey 4종.

---

## Task 1: 메시 의존성 커밋 + 파라미터 모델

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (이미 작업트리에 수정됨 — 커밋만)
- Create: `seg_and_mesh/mesh/__init__.py`
- Create: `seg_and_mesh/mesh/params.py`
- Test: `tests/mesh/__init__.py`, `tests/mesh/test_params.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Preprocess: method: str = "none"; sigma_vox: float = 0.6`
  - `@dataclass(frozen=True) Extractor: name: str = "skimage_mc"; options: tuple = ()`  (dict는 unhashable이라 frozen에 못 넣음 → 정렬된 tuple[tuple[str, ...]])
  - `@dataclass(frozen=True) Smoothing: method: str = "none"; iterations: int = 20; pass_band: float = 0.1; feature_angle: float = 60.0; relaxation: float = 0.1; alpha: float = 0.1; beta: float = 0.2`
  - `@dataclass(frozen=True) Decimation: method: str = "none"; target_ratio: float = 0.35`
  - `@dataclass(frozen=True) MeshParams: preprocess: Preprocess; extractor: Extractor; smoothing: Smoothing; decimation: Decimation; min_voxel: int = 100; label_table: str = "canonical-v1"; seg_source: str = ""`
  - `MeshParams.to_params_dict(self) -> dict` — spec §7.1 camelCase 형태(variantId·createdAt 제외한 파라미터부)
  - `MeshParams.param_hash(self) -> str` — 파라미터 정준 JSON의 sha1 앞 4자리(hex)
  - `MeshParams.variant_id(self, index: int) -> str` — `f"v{index:02d}-{self.param_hash()}"`
  - `default_params() -> MeshParams` — 전부 기본값

- [ ] **Step 1: `tests/mesh/__init__.py`를 빈 파일로 만든다**

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/mesh/test_params.py`:

```python
"""파라미터 모델과 variantId (스펙 §7, §7.1)."""

from __future__ import annotations

from seg_and_mesh.mesh import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)


def test_default_params_are_conservative():
    p = default_params()
    assert p.preprocess.method == "none"
    assert p.extractor.name == "skimage_mc"
    assert p.smoothing.method == "none"
    assert p.decimation.method == "none"
    assert p.min_voxel == 100
    assert p.label_table == "canonical-v1"


def test_params_are_frozen():
    p = default_params()
    import pytest

    with pytest.raises(Exception):
        p.min_voxel = 0


def test_params_dict_uses_spec_camelcase():
    """스펙 §7.1 params.json 키. 소비 측이 이 형태를 기대한다."""
    p = MeshParams(
        preprocess=Preprocess(method="gaussian", sigma_vox=0.6),
        extractor=Extractor(name="vtk_surfacenets"),
        smoothing=Smoothing(method="taubin", iterations=20, pass_band=0.1, feature_angle=60.0),
        decimation=Decimation(method="quadric", target_ratio=0.35),
        min_voxel=100,
        seg_source="aparc.DKTatlas+aseg.deep.withCC.mgz",
    )
    d = p.to_params_dict()
    assert d["preprocess"] == {"method": "gaussian", "sigmaVox": 0.6}
    assert d["extractor"]["name"] == "vtk_surfacenets"
    assert d["smoothing"] == {
        "method": "taubin", "iterations": 20, "passBand": 0.1, "featureAngle": 60.0
    }
    assert d["decimation"] == {"method": "quadric", "targetRatio": 0.35}
    assert d["minVoxel"] == 100
    assert d["labelTable"] == "canonical-v1"
    assert d["segSource"] == "aparc.DKTatlas+aseg.deep.withCC.mgz"


def test_variant_id_format():
    """v<순번 2자리>-<해시 4자리> (스펙 §7)."""
    p = default_params()
    vid = p.variant_id(7)
    assert vid.startswith("v07-")
    assert len(vid) == len("v07-") + 4
    # 16진수 4자리
    int(vid.split("-")[1], 16)


def test_variant_id_hash_is_deterministic_and_param_sensitive():
    a = default_params()
    b = MeshParams(
        preprocess=Preprocess(method="gaussian"),
        extractor=Extractor(),
        smoothing=Smoothing(),
        decimation=Decimation(),
    )
    assert a.param_hash() == default_params().param_hash()  # 결정론
    assert a.param_hash() != b.param_hash()  # 파라미터 바뀌면 해시 바뀜


def test_index_changes_prefix_not_hash():
    """순번은 접두만 바꾸고 해시는 파라미터에만 달렸다."""
    p = default_params()
    assert p.variant_id(1).split("-")[1] == p.variant_id(2).split("-")[1]
```

- [ ] **Step 3: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_params.py -q`
Expected: `ModuleNotFoundError: No module named 'seg_and_mesh.mesh'`

- [ ] **Step 4: 구현한다**

`seg_and_mesh/mesh/params.py`:

```python
"""메시 변형 파라미터 (스펙 §6.5, §7.1).

3축(전처리·추출·후처리) + min-voxel 노브. frozen dataclass라 변형 하나가
불변 값이고, 파라미터 해시로 variantId를 만든다(스펙 §7).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preprocess:
    """축1: 마스크 안티에일리어싱 (스펙 §6.5)."""

    method: str = "none"  # none | gaussian | distance
    sigma_vox: float = 0.6


@dataclass(frozen=True)
class Extractor:
    """축2: 표면 추출기 (스펙 §6.5).

    options는 정렬된 튜플이라 frozen에 담긴다(dict는 unhashable).
    """

    name: str = "skimage_mc"
    options: tuple = ()


@dataclass(frozen=True)
class Smoothing:
    """축3-1: 스무딩 (스펙 §6.5)."""

    method: str = "none"  # none | laplacian | taubin | humphrey
    iterations: int = 20
    pass_band: float = 0.1
    feature_angle: float = 60.0
    relaxation: float = 0.1
    alpha: float = 0.1
    beta: float = 0.2


@dataclass(frozen=True)
class Decimation:
    """축3-2: 데시메이션 (스펙 §6.5)."""

    method: str = "none"  # none | quadric
    target_ratio: float = 0.35


@dataclass(frozen=True)
class MeshParams:
    """변형 하나의 전체 파라미터."""

    preprocess: Preprocess
    extractor: Extractor
    smoothing: Smoothing
    decimation: Decimation
    min_voxel: int = 100
    label_table: str = "canonical-v1"
    seg_source: str = ""

    def to_params_dict(self) -> dict:
        """스펙 §7.1 params.json의 파라미터부(camelCase).

        variantId·createdAt는 생성 시점 값이라 여기 없다 — generate가 붙인다.
        """
        pre = {"method": self.preprocess.method}
        if self.preprocess.method == "gaussian":
            pre["sigmaVox"] = self.preprocess.sigma_vox

        smo: dict = {"method": self.smoothing.method}
        if self.smoothing.method in ("laplacian", "taubin", "humphrey"):
            smo["iterations"] = self.smoothing.iterations
        if self.smoothing.method == "taubin":
            smo["passBand"] = self.smoothing.pass_band
            smo["featureAngle"] = self.smoothing.feature_angle
        if self.smoothing.method == "laplacian":
            smo["relaxation"] = self.smoothing.relaxation
        if self.smoothing.method == "humphrey":
            smo["alpha"] = self.smoothing.alpha
            smo["beta"] = self.smoothing.beta

        dec: dict = {"method": self.decimation.method}
        if self.decimation.method == "quadric":
            dec["targetRatio"] = self.decimation.target_ratio

        return {
            "preprocess": pre,
            "extractor": {"name": self.extractor.name, "options": dict(self.extractor.options)},
            "smoothing": smo,
            "decimation": dec,
            "minVoxel": self.min_voxel,
            "labelTable": self.label_table,
            "segSource": self.seg_source,
        }

    def param_hash(self) -> str:
        """파라미터 정준 JSON의 sha1 앞 4자리. segSource는 제외한다 —
        같은 파라미터면 입력 파일명이 달라도 같은 변형이다."""
        payload = self.to_params_dict()
        payload.pop("segSource", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:4]

    def variant_id(self, index: int) -> str:
        """v<순번 2자리>-<해시 4자리> (스펙 §7)."""
        return f"v{index:02d}-{self.param_hash()}"


def default_params() -> MeshParams:
    """전부 기본값 — 가장 보수적인 조합(전처리·스무딩·데시 없음, 기준선 추출기)."""
    return MeshParams(
        preprocess=Preprocess(),
        extractor=Extractor(),
        smoothing=Smoothing(),
        decimation=Decimation(),
    )
```

`seg_and_mesh/mesh/__init__.py`:

```python
"""메시 엔진 (스펙 §6.5~§6.7, §7.1, §7.2).

canonical seg.nii.gz + 파라미터 조합 -> 변형 하나(GLB + 지표). 도커·웹 없이
로컬에서 전 구간 테스트된다.
"""

from seg_and_mesh.mesh.params import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)

__all__ = [
    "Decimation",
    "Extractor",
    "MeshParams",
    "Preprocess",
    "Smoothing",
    "default_params",
]
```

- [ ] **Step 5: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_params.py -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋한다**

```bash
git add pyproject.toml uv.lock seg_and_mesh/mesh/__init__.py seg_and_mesh/mesh/params.py tests/mesh/__init__.py tests/mesh/test_params.py
git commit -m "feat(mesh): 메시 의존성 커밋과 변형 파라미터 모델"
```

---

## Task 2: 합성 볼륨 픽스처 + 축1 전처리

**Files:**
- Create: `tests/mesh/conftest.py`
- Create: `seg_and_mesh/mesh/preprocess.py`
- Modify: `seg_and_mesh/mesh/__init__.py` (export 추가)
- Test: `tests/mesh/test_preprocess.py`

**Interfaces:**
- Consumes: `Preprocess` (Task 1)
- Produces:
  - `apply_preprocess(mask: numpy.ndarray, params: Preprocess) -> tuple[numpy.ndarray, float]` — 이진 마스크(bool/uint8)를 받아 `(스칼라장 float32, isolevel)`을 낸다. `none`/`gaussian`은 isolevel 0.5, `distance`는 0.0.
  - conftest 픽스처 `sphere_volume`, `cube_volume`, `synthetic_seg` (아래 정의)

- [ ] **Step 1: 합성 픽스처를 쓴다**

`tests/mesh/conftest.py`:

```python
"""합성 라벨 볼륨 (스펙 §13 — 구·큐브·판으로 전 축 조합 검증)."""

from __future__ import annotations

import numpy as np
import pytest


def _sphere(shape, center, radius):
    zz, yy, xx = np.ogrid[: shape[0], : shape[1], : shape[2]]
    d2 = (zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2
    return d2 <= radius**2


@pytest.fixture
def sphere_mask():
    """반경 8, 부피 해석해 = 4/3 pi r^3 ≈ 2144.66 voxel."""
    m = np.zeros((32, 32, 32), dtype=np.uint8)
    m[_sphere((32, 32, 32), (16, 16, 16), 8)] = 1
    return m


@pytest.fixture
def sphere_radius():
    return 8.0


@pytest.fixture
def cube_mask():
    """10^3 큐브 = 1000 voxel."""
    m = np.zeros((20, 20, 20), dtype=np.uint8)
    m[5:15, 5:15, 5:15] = 1
    return m


@pytest.fixture
def synthetic_seg(tmp_path):
    """구(라벨 13)·큐브(라벨 1)·얇은 판(라벨 40)이 든 20^3 라벨 볼륨을
    canonical id로 채워 NIfTI로 쓴다. affine은 등방 1mm."""
    import nibabel as nib

    vol = np.zeros((40, 40, 40), dtype=np.uint8)
    vol[_sphere((40, 40, 40), (10, 10, 10), 6)] = 13  # 구
    vol[25:35, 25:35, 25:35] = 1  # 큐브
    vol[20:22, 5:35, 5:35] = 40  # 얇은 판 (두께 2)
    affine = np.diag([1.0, 1.0, 1.0, 1.0])
    img = nib.Nifti1Image(vol, affine)
    img.header.set_zooms((1.0, 1.0, 1.0))
    path = tmp_path / "synthetic_seg.nii.gz"
    nib.save(img, path)
    return path
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/mesh/test_preprocess.py`:

```python
"""축1 전처리 (스펙 §6.5)."""

from __future__ import annotations

import numpy as np

from seg_and_mesh.mesh import Preprocess
from seg_and_mesh.mesh.preprocess import apply_preprocess


def test_none_returns_binary_field_at_half_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    assert field.dtype == np.float32
    assert level == 0.5
    assert set(np.unique(field)) <= {0.0, 1.0}


def test_gaussian_blurs_but_keeps_half_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="gaussian", sigma_vox=0.8))
    assert level == 0.5
    # 블러 후 경계에 0/1 사이 값이 생긴다
    assert np.any((field > 0.01) & (field < 0.99))
    # 중심은 여전히 1에 가깝다
    assert field[16, 16, 16] > 0.9


def test_distance_is_signed_with_zero_level(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="distance"))
    assert level == 0.0
    # 안쪽은 양수, 바깥은 음수 (부호거리장)
    assert field[16, 16, 16] > 0
    assert field[0, 0, 0] < 0


def test_unknown_method_raises(sphere_mask):
    import pytest

    with pytest.raises(ValueError):
        apply_preprocess(sphere_mask, Preprocess(method="nope"))
```

- [ ] **Step 3: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_preprocess.py -q`
Expected: `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 4: 구현한다**

`seg_and_mesh/mesh/preprocess.py`:

```python
"""축1: 마스크 전처리 (안티에일리어싱, 스펙 §6.5).

이진 마스크에 곧바로 등고면을 뽑으면 voxel 계단이 남는다. 전처리로 계단을
줄이면 약한 스무딩으로 끝난다. 각 방법은 (스칼라장, isolevel)을 낸다 —
추출기는 이 둘만 있으면 된다.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def apply_preprocess(mask: np.ndarray, params) -> tuple[np.ndarray, float]:
    """이진 마스크 -> (스칼라장 float32, isolevel).

    Raises:
        ValueError: 알 수 없는 method.
    """
    binary = np.asarray(mask) > 0

    if params.method == "none":
        return binary.astype(np.float32), 0.5

    if params.method == "gaussian":
        field = ndimage.gaussian_filter(
            binary.astype(np.float32), sigma=float(params.sigma_vox)
        )
        return field, 0.5

    if params.method == "distance":
        # 부호거리장: 안쪽(+) 바깥(-). 0 등고면이 원래 경계다.
        inside = ndimage.distance_transform_edt(binary)
        outside = ndimage.distance_transform_edt(~binary)
        field = (inside - outside).astype(np.float32)
        return field, 0.0

    raise ValueError(f"알 수 없는 전처리 method: {params.method}")
```

`seg_and_mesh/mesh/__init__.py`의 import·`__all__`에 추가:

```python
from seg_and_mesh.mesh.preprocess import apply_preprocess
```

`__all__`에 `"apply_preprocess"` 추가.

- [ ] **Step 5: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_preprocess.py -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋한다**

```bash
git add tests/mesh/conftest.py tests/mesh/test_preprocess.py seg_and_mesh/mesh/preprocess.py seg_and_mesh/mesh/__init__.py
git commit -m "feat(mesh): 합성 볼륨 픽스처와 축1 전처리(none/gaussian/distance)"
```

---

## Task 3: 축2 표면 추출기 레지스트리

**Files:**
- Create: `seg_and_mesh/mesh/extract.py`
- Modify: `seg_and_mesh/mesh/__init__.py`
- Test: `tests/mesh/test_extract.py`

**Interfaces:**
- Consumes: 스칼라장·isolevel (Task 2)
- Produces:
  - `extract(field: numpy.ndarray, isolevel: float, name: str, options: dict | None = None) -> tuple[numpy.ndarray, numpy.ndarray]` — `(정점[voxel 좌표, float], 면[int, (M,3)])`. 정점은 아직 voxel 좌표(affine 미적용).
  - `EXTRACTOR_NAMES: tuple[str, ...]` — 등록된 추출기 이름들
  - `ExtractError(RuntimeError)`
- 추출기 이름(스펙 §6.5): `skimage_mc`, `pymcubes`, `vtk_flyingedges`, `vtk_surfacenets`, `vtk_contour_perlabel`
- **설계 결정(프로토타입 실측):** v1은 전부 **라벨별(마스크 하나)** 추출이다. vtkSurfaceNets3D 다중라벨 1패스는 공유경계 표현이라 라벨별로 나누면 닫히지 않아 부피가 무의미하다(실측 +77%~-99%). 라벨별 이진 추출은 태생적으로 닫힌다(실측 오차 -1~-3%). 다중라벨 최적화는 후속.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/mesh/test_extract.py`:

```python
"""축2 표면 추출 (스펙 §6.5).

구(반경 8, 부피 4/3 pi r^3 ≈ 2144.66)로 각 추출기의 부피 정확도를 본다.
mesh 부피는 voxel 부피보다 약간 작게 나오는 게 정상(계단을 깎으므로).
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from seg_and_mesh.mesh import EXTRACTOR_NAMES
from seg_and_mesh.mesh.extract import ExtractError, extract
from seg_and_mesh.mesh.preprocess import apply_preprocess
from seg_and_mesh.mesh import Preprocess

ANALYTIC_SPHERE_VOLUME = 4.0 / 3.0 * np.pi * 8.0**3  # ≈ 2144.66


def _volume(verts, faces):
    return abs(trimesh.Trimesh(vertices=verts, faces=faces, process=False).volume)


def test_registry_lists_all_five_axis2_options():
    assert set(EXTRACTOR_NAMES) == {
        "skimage_mc", "pymcubes", "vtk_flyingedges",
        "vtk_surfacenets", "vtk_contour_perlabel",
    }


@pytest.mark.parametrize("name", [
    "skimage_mc", "pymcubes", "vtk_flyingedges",
    "vtk_surfacenets", "vtk_contour_perlabel",
])
def test_each_extractor_reconstructs_sphere_volume(sphere_mask, name):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    verts, faces = extract(field, level, name)

    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3
    assert len(faces) > 0

    vol = _volume(verts, faces)
    # 계단 이산화라 ±15% 안이면 충분(스무딩 없이)
    assert vol == pytest.approx(ANALYTIC_SPHERE_VOLUME, rel=0.15), f"{name}: {vol}"


@pytest.mark.parametrize("name", ["skimage_mc", "vtk_surfacenets", "vtk_flyingedges"])
def test_watertight_for_compact_label(sphere_mask, name):
    """볼륨 경계에 안 닿는 조밀 구조는 닫힌 표면이어야 한다."""
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    verts, faces = extract(field, level, name)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    assert mesh.is_watertight


def test_unknown_extractor_raises(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    with pytest.raises(ExtractError):
        extract(field, level, "does_not_exist")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_extract.py -q`
Expected: `ImportError`.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/mesh/extract.py`:

```python
"""축2: 표면 추출기 (스펙 §6.5).

각 추출기는 스칼라장 + isolevel을 받아 (정점[voxel 좌표], 면)을 낸다.
정점은 아직 voxel 좌표다 — affine은 generate가 한 번만 적용한다(스펙 §6.7).

v1은 전부 라벨별(마스크 하나) 추출이다. 다중라벨 1패스(vtkSurfaceNets3D)는
공유경계라 라벨별로 닫히지 않아 부피가 무의미하다(프로토타입 실측). 라벨별
이진 추출은 태생적으로 닫혀 부피가 맞다.
"""

from __future__ import annotations

import numpy as np


class ExtractError(RuntimeError):
    """알 수 없는 추출기이거나 추출이 실패했다."""


def _skimage_mc(field, isolevel, options):
    from skimage import measure

    # 경계 잘림 방지 패딩. 바깥은 배경 값으로.
    pad = 1
    padded = np.pad(field, pad, mode="constant", constant_values=field.min())
    verts, faces, _, _ = measure.marching_cubes(padded, level=isolevel)
    return verts - pad, faces


def _pymcubes(field, isolevel, options):
    import mcubes

    pad = 1
    padded = np.pad(field, pad, mode="constant", constant_values=field.min())
    verts, faces = mcubes.marching_cubes(padded, isolevel)
    return np.asarray(verts) - pad, np.asarray(faces)


def _to_vtk_image(field):
    import vtk
    from vtk.util import numpy_support

    img = vtk.vtkImageData()
    img.SetDimensions(*field.shape)
    img.SetSpacing(1.0, 1.0, 1.0)
    img.SetOrigin(0.0, 0.0, 0.0)
    arr = numpy_support.numpy_to_vtk(
        field.ravel(order="F").astype(np.float32), deep=True
    )
    img.GetPointData().SetScalars(arr)
    return img


def _vtk_poly_to_arrays(poly):
    from vtk.util import numpy_support

    pts = numpy_support.vtk_to_numpy(poly.GetPoints().GetData())
    conn = numpy_support.vtk_to_numpy(poly.GetPolys().GetConnectivityArray())
    offs = numpy_support.vtk_to_numpy(poly.GetPolys().GetOffsetsArray())
    sizes = np.diff(offs)
    if not np.all(sizes == 3):
        # 삼각형화
        import vtk

        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(poly)
        tri.Update()
        return _vtk_poly_to_arrays(tri.GetOutput())
    return np.asarray(pts), conn.reshape(-1, 3)


def _vtk_flyingedges(field, isolevel, options):
    import vtk

    img = _to_vtk_image(field)
    fe = vtk.vtkFlyingEdges3D()
    fe.SetInputData(img)
    fe.SetValue(0, isolevel)
    fe.ComputeNormalsOff()
    fe.Update()
    return _vtk_poly_to_arrays(fe.GetOutput())


def _vtk_surfacenets(field, isolevel, options):
    import vtk

    # SurfaceNets는 라벨 이미지를 기대한다. 이진화해 라벨 1 vs 0으로 준다.
    binary = (field >= isolevel).astype(np.float32)
    img = _to_vtk_image(binary)
    sn = vtk.vtkSurfaceNets3D()
    sn.SetInputData(img)
    sn.GenerateLabels(1, 1, 1)
    sn.SetValue(0, 1)
    sn.SetOutputStyleToBoundary()
    sn.Update()
    return _vtk_poly_to_arrays(sn.GetOutput())


def _vtk_contour_perlabel(field, isolevel, options):
    import vtk

    img = _to_vtk_image(field)
    cf = vtk.vtkContourFilter()
    cf.SetInputData(img)
    cf.SetValue(0, isolevel)
    cf.ComputeNormalsOff()
    cf.Update()
    return _vtk_poly_to_arrays(cf.GetOutput())


_EXTRACTORS = {
    "skimage_mc": _skimage_mc,
    "pymcubes": _pymcubes,
    "vtk_flyingedges": _vtk_flyingedges,
    "vtk_surfacenets": _vtk_surfacenets,
    "vtk_contour_perlabel": _vtk_contour_perlabel,
}

EXTRACTOR_NAMES = tuple(_EXTRACTORS)


def extract(field, isolevel, name, options=None):
    """스칼라장 + isolevel -> (정점[voxel], 면).

    Raises:
        ExtractError: 알 수 없는 추출기.
    """
    impl = _EXTRACTORS.get(name)
    if impl is None:
        raise ExtractError(f"알 수 없는 추출기: {name} (가능: {EXTRACTOR_NAMES})")
    verts, faces = impl(field, isolevel, dict(options or {}))
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)
```

`seg_and_mesh/mesh/__init__.py`에 추가:

```python
from seg_and_mesh.mesh.extract import EXTRACTOR_NAMES, ExtractError, extract
```

`__all__`에 `"EXTRACTOR_NAMES"`, `"ExtractError"`, `"extract"` 추가.

**주의:** `pymcubes`(PyMCubes)가 설치돼 있어야 한다. Task 1의 pyproject에 없으면 추가한다: `PyMCubes>=0.1.4`. 설치 후 `uv sync`.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_extract.py -q`
Expected: 전부 PASS. (실패 시 추출기 구현만 고친다. 부피 허용치 ±15%는 계단 이산화 실측 근거다.)

- [ ] **Step 5: 커밋한다**

```bash
git add seg_and_mesh/mesh/extract.py seg_and_mesh/mesh/__init__.py tests/mesh/test_extract.py pyproject.toml uv.lock
git commit -m "feat(mesh): 축2 표면 추출기 5종 레지스트리(라벨별 이진)"
```

---

## Task 4: 축3 후처리(스무딩·데시메이션)

**Files:**
- Create: `seg_and_mesh/mesh/postprocess.py`
- Modify: `seg_and_mesh/mesh/__init__.py`
- Test: `tests/mesh/test_postprocess.py`

**Interfaces:**
- Consumes: `(정점, 면)` (Task 3), `Smoothing`·`Decimation` (Task 1)
- Produces:
  - `smooth(verts, faces, params: Smoothing) -> tuple[numpy.ndarray, numpy.ndarray]` — none/laplacian/taubin/humphrey
  - `decimate(verts, faces, params: Decimation) -> tuple[numpy.ndarray, numpy.ndarray]` — none/quadric
  - `PostprocessError(RuntimeError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/mesh/test_postprocess.py`:

```python
"""축3 후처리 (스펙 §6.5)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from seg_and_mesh.mesh import Decimation, Preprocess, Smoothing
from seg_and_mesh.mesh.extract import extract
from seg_and_mesh.mesh.postprocess import PostprocessError, decimate, smooth
from seg_and_mesh.mesh.preprocess import apply_preprocess


def _sphere_mesh(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_smooth_none_is_identity(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = smooth(verts, faces, Smoothing(method="none"))
    assert np.allclose(v2, verts)
    assert np.array_equal(f2, faces)


@pytest.mark.parametrize("method", ["laplacian", "taubin", "humphrey"])
def test_smoothing_reduces_surface_roughness(sphere_mask, method):
    """스무딩 후 표면적이 줄어든다(계단이 깎이므로)."""
    verts, faces = _sphere_mesh(sphere_mask)
    area0 = trimesh.Trimesh(verts, faces, process=False).area
    v2, f2 = smooth(verts, faces, Smoothing(method=method, iterations=15))
    area1 = trimesh.Trimesh(v2, f2, process=False).area
    assert area1 < area0
    assert len(v2) == len(verts)  # 스무딩은 위상을 안 바꾼다


def test_taubin_shrinks_less_than_laplacian(sphere_mask):
    """taubin은 수축 억제 필터다(스펙 §6.5). 부피가 laplacian보다 덜 준다."""
    verts, faces = _sphere_mesh(sphere_mask)
    v0 = abs(trimesh.Trimesh(verts, faces, process=False).volume)

    vl, fl = smooth(verts, faces, Smoothing(method="laplacian", iterations=30, relaxation=0.1))
    vt, ft = smooth(verts, faces, Smoothing(method="taubin", iterations=30))
    vol_l = abs(trimesh.Trimesh(vl, fl, process=False).volume)
    vol_t = abs(trimesh.Trimesh(vt, ft, process=False).volume)

    assert (v0 - vol_t) < (v0 - vol_l)


def test_decimate_none_is_identity(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = decimate(verts, faces, Decimation(method="none"))
    assert np.array_equal(f2, faces)


def test_quadric_reduces_triangle_count(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    v2, f2 = decimate(verts, faces, Decimation(method="quadric", target_ratio=0.3))
    assert len(f2) < len(faces) * 0.5


def test_unknown_smoothing_raises(sphere_mask):
    verts, faces = _sphere_mesh(sphere_mask)
    with pytest.raises(PostprocessError):
        smooth(verts, faces, Smoothing(method="nope"))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_postprocess.py -q`
Expected: `ImportError`.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/mesh/postprocess.py`:

```python
"""축3: 스무딩·데시메이션 (스펙 §6.5).

스무딩은 위상을 유지한 채 정점을 옮긴다. 데시메이션은 삼각형 수를 줄인다.
humphrey는 trimesh, 나머지 스무딩과 데시메이션은 VTK를 쓴다 — 새 의존성이
없다. hc_laplacian(pymeshlab)은 후속으로 미룬다.
"""

from __future__ import annotations

import numpy as np


class PostprocessError(RuntimeError):
    """알 수 없는 후처리 method이거나 처리가 실패했다."""


def _to_vtk_poly(verts, faces):
    import vtk
    from vtk.util import numpy_support

    poly = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(numpy_support.numpy_to_vtk(np.ascontiguousarray(verts, dtype=np.float64), deep=True))
    poly.SetPoints(pts)

    cells = vtk.vtkCellArray()
    tri = np.empty((len(faces), 4), dtype=np.int64)
    tri[:, 0] = 3
    tri[:, 1:] = faces
    id_arr = numpy_support.numpy_to_vtkIdTypeArray(tri.ravel(), deep=True)
    cells.SetCells(len(faces), id_arr)
    poly.SetPolys(cells)
    return poly


def _from_vtk_poly(poly):
    from vtk.util import numpy_support

    verts = numpy_support.vtk_to_numpy(poly.GetPoints().GetData())
    conn = numpy_support.vtk_to_numpy(poly.GetPolys().GetConnectivityArray())
    return np.asarray(verts, dtype=np.float64), conn.reshape(-1, 3).astype(np.int64)


def smooth(verts, faces, params):
    """정점을 스무딩한다. 위상(면)은 유지한다.

    Raises:
        PostprocessError: 알 수 없는 method.
    """
    if params.method == "none":
        return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    if params.method == "humphrey":
        import trimesh

        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        trimesh.smoothing.filter_humphrey(
            mesh, alpha=params.alpha, beta=params.beta, iterations=params.iterations
        )
        return np.asarray(mesh.vertices), np.asarray(mesh.faces)

    if params.method in ("laplacian", "taubin"):
        import vtk

        poly = _to_vtk_poly(verts, faces)
        if params.method == "laplacian":
            f = vtk.vtkSmoothPolyDataFilter()
            f.SetInputData(poly)
            f.SetNumberOfIterations(params.iterations)
            f.SetRelaxationFactor(params.relaxation)
            f.FeatureEdgeSmoothingOff()
            f.BoundarySmoothingOn()
        else:  # taubin — windowed sinc, 수축 억제
            f = vtk.vtkWindowedSincPolyDataFilter()
            f.SetInputData(poly)
            f.SetNumberOfIterations(params.iterations)
            f.SetPassBand(params.pass_band)
            f.SetFeatureAngle(params.feature_angle)
            f.NonManifoldSmoothingOn()
            f.NormalizeCoordinatesOn()
        f.Update()
        return _from_vtk_poly(f.GetOutput())

    raise PostprocessError(f"알 수 없는 스무딩 method: {params.method}")


def decimate(verts, faces, params):
    """삼각형 수를 줄인다.

    Raises:
        PostprocessError: 알 수 없는 method.
    """
    if params.method == "none":
        return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    if params.method == "quadric":
        import vtk

        poly = _to_vtk_poly(verts, faces)
        d = vtk.vtkQuadricDecimation()
        d.SetInputData(poly)
        # targetRatio는 "남길 비율" — vtk의 TargetReduction은 "줄일 비율"
        d.SetTargetReduction(1.0 - params.target_ratio)
        d.Update()
        return _from_vtk_poly(d.GetOutput())

    raise PostprocessError(f"알 수 없는 데시메이션 method: {params.method}")
```

`seg_and_mesh/mesh/__init__.py`에 추가:

```python
from seg_and_mesh.mesh.postprocess import PostprocessError, decimate, smooth
```

`__all__`에 `"PostprocessError"`, `"decimate"`, `"smooth"` 추가.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_postprocess.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋한다**

```bash
git add seg_and_mesh/mesh/postprocess.py seg_and_mesh/mesh/__init__.py tests/mesh/test_postprocess.py
git commit -m "feat(mesh): 축3 스무딩(none/laplacian/taubin/humphrey)과 quadric 데시메이션"
```

---

## Task 5: GLB 작성

**Files:**
- Create: `seg_and_mesh/mesh/glb.py`
- Modify: `seg_and_mesh/mesh/__init__.py`
- Test: `tests/mesh/test_glb.py`

**Interfaces:**
- Consumes: `(정점[world mm], 면)` per 라벨
- Produces:
  - `write_glb(meshes: dict[int, tuple[numpy.ndarray, numpy.ndarray]], out_path: pathlib.Path) -> int` — `label_<id>` 노드로 GLB를 쓰고 바이트 크기를 낸다. 색을 굽지 않는다. 정점은 이미 world mm(호출자가 affine 적용).
  - `GlbError(RuntimeError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/mesh/test_glb.py`:

```python
"""GLB 작성 (스펙 §6.7)."""

from __future__ import annotations

import numpy as np
import trimesh

from seg_and_mesh.mesh import Preprocess
from seg_and_mesh.mesh.extract import extract
from seg_and_mesh.mesh.glb import write_glb
from seg_and_mesh.mesh.preprocess import apply_preprocess


def _sphere(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_nodes_are_named_label_id(tmp_path, sphere_mask):
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    size = write_glb({13: (verts, faces), 1: (verts, faces)}, out)

    assert size > 0
    assert out.stat().st_size == size

    scene = trimesh.load(out)
    assert set(scene.geometry.keys()) == {"label_13", "label_1"}


def test_no_material_color_baked(tmp_path, sphere_mask):
    """색은 regions-meta.json이 운반한다(B안). GLB에 색을 굽지 않는다."""
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    write_glb({13: (verts, faces)}, out)

    scene = trimesh.load(out)
    mesh = scene.geometry["label_13"]
    # 정점 색이 실리지 않았다
    assert not mesh.visual.kind == "vertex" or mesh.visual.vertex_colors is None or \
        len(np.unique(mesh.visual.vertex_colors, axis=0)) <= 1


def test_coordinates_preserved(tmp_path, sphere_mask):
    """정점 좌표가 그대로 실린다(호출자가 이미 world mm로 만들었다)."""
    verts, faces = _sphere(sphere_mask)
    out = tmp_path / "regions.glb"
    write_glb({13: (verts, faces)}, out)

    scene = trimesh.load(out)
    mesh = scene.geometry["label_13"]
    assert mesh.bounds[0] == np.asarray(verts).min(axis=0).astype(np.float32).tolist().__class__ or True
    assert np.allclose(mesh.bounds[0], verts.min(axis=0), atol=1e-3)
    assert np.allclose(mesh.bounds[1], verts.max(axis=0), atol=1e-3)


def test_empty_meshes_dict_raises(tmp_path):
    import pytest

    from seg_and_mesh.mesh.glb import GlbError

    with pytest.raises(GlbError):
        write_glb({}, tmp_path / "regions.glb")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_glb.py -q`
Expected: `ImportError`.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/mesh/glb.py`:

```python
"""GLB 작성 (스펙 §6.7).

trimesh로 직접 쓴다 — 렌더러 씬 export(pyvista.Plotter.export_gltf)는 조명·
카메라를 함께 싣고 압축이 없어 파일이 커진다. 노드명을 label_<id>로 확정하고
색을 굽지 않는다(색은 regions-meta.json이 운반, B안). 정점은 이미 world mm다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


class GlbError(RuntimeError):
    """GLB를 쓰지 못했다."""


def write_glb(meshes: dict, out_path) -> int:
    """label_<id> 노드로 GLB를 쓰고 바이트 크기를 낸다.

    Args:
        meshes: {label_id: (verts[world mm], faces)}.
        out_path: 쓸 경로. 상위 폴더가 없으면 만든다.

    Raises:
        GlbError: meshes가 비었거나 쓰기에 실패했을 때.
    """
    if not meshes:
        raise GlbError("빈 메시로 GLB를 쓸 수 없다")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scene = trimesh.Scene()
    for label_id, (verts, faces) in meshes.items():
        mesh = trimesh.Trimesh(
            vertices=np.asarray(verts, dtype=np.float32),
            faces=np.asarray(faces, dtype=np.int64),
            process=False,
        )
        name = f"label_{label_id}"
        scene.add_geometry(mesh, node_name=name, geom_name=name)

    try:
        data = scene.export(file_type="glb")
    except Exception as exc:  # trimesh는 다양한 예외를 던진다
        raise GlbError(f"GLB export 실패: {out_path}") from exc

    out_path.write_bytes(data)
    return len(data)
```

`seg_and_mesh/mesh/__init__.py`에 추가:

```python
from seg_and_mesh.mesh.glb import GlbError, write_glb
```

`__all__`에 `"GlbError"`, `"write_glb"` 추가.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_glb.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋한다**

```bash
git add seg_and_mesh/mesh/glb.py seg_and_mesh/mesh/__init__.py tests/mesh/test_glb.py
git commit -m "feat(mesh): label_<id> 노드 GLB 직접 작성(색 미포함)"
```

---

## Task 6: 지표 계산

**Files:**
- Create: `seg_and_mesh/mesh/metrics.py`
- Modify: `seg_and_mesh/mesh/__init__.py`
- Test: `tests/mesh/test_metrics.py`

**Interfaces:**
- Consumes: `(정점[world mm], 면)` per 라벨, 라벨별 voxel count, voxel 부피
- Produces:
  - `region_metrics(label_id: int, verts, faces, voxel_count: int, voxel_volume_mm3: float) -> dict` — 스펙 §7.2 `perRegion` 항목 하나. 키: `labelId`, `triangles`, `meshVolumeMm3`, `voxelVolumeMm3`, `volumeErrorPct`, `surfaceAreaMm2`, `boundaryEdges`, `nonManifoldEdges`.
  - `summarize(per_region: list[dict], durations: dict, glb_bytes: int, labels_skipped: int) -> dict` — 스펙 §7.2 전체 metrics.json 구조(`durationSec`, `glbBytes`, `total`, `perRegion`, `summary`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/mesh/test_metrics.py`:

```python
"""지표 계산 (스펙 §7.2)."""

from __future__ import annotations

import numpy as np
import pytest

from seg_and_mesh.mesh import Preprocess
from seg_and_mesh.mesh.extract import extract
from seg_and_mesh.mesh.metrics import region_metrics, summarize
from seg_and_mesh.mesh.preprocess import apply_preprocess

ANALYTIC_SPHERE_VOLUME = 4.0 / 3.0 * np.pi * 8.0**3


def _sphere(sphere_mask):
    field, level = apply_preprocess(sphere_mask, Preprocess(method="none"))
    return extract(field, level, "skimage_mc")


def test_region_metrics_volume_error(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    voxel_count = int(sphere_mask.sum())
    m = region_metrics(13, verts, faces, voxel_count, 1.0)

    assert m["labelId"] == 13
    assert m["triangles"] == len(faces)
    assert m["voxelVolumeMm3"] == pytest.approx(float(voxel_count))
    # mesh 부피는 해석해에 가깝다
    assert m["meshVolumeMm3"] == pytest.approx(ANALYTIC_SPHERE_VOLUME, rel=0.1)
    # 계단을 깎으므로 mesh < voxel → 음수 오차
    assert -10 < m["volumeErrorPct"] < 5
    assert m["surfaceAreaMm2"] > 0


def test_watertight_sphere_has_no_boundary_or_nonmanifold(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    m = region_metrics(13, verts, faces, int(sphere_mask.sum()), 1.0)
    assert m["boundaryEdges"] == 0
    assert m["nonManifoldEdges"] == 0


def test_open_mesh_reports_boundary_edges():
    """한 삼각형만 있으면 세 변이 전부 경계다."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2]], dtype=int)
    m = region_metrics(1, verts, faces, 1, 1.0)
    assert m["boundaryEdges"] == 3


def test_summarize_shape(sphere_mask):
    verts, faces = _sphere(sphere_mask)
    per = [region_metrics(13, verts, faces, int(sphere_mask.sum()), 1.0)]
    out = summarize(
        per,
        durations={"extract": 0.4, "smooth": 0.0, "decimate": 0.0, "write": 0.2},
        glb_bytes=12345,
        labels_skipped=3,
    )
    assert out["glbBytes"] == 12345
    assert out["total"]["triangles"] == len(faces)
    assert out["total"]["vertices"] == len(verts)
    assert out["perRegion"] == per
    assert out["summary"]["labelsSkippedByMinVoxel"] == 3
    assert "volumeErrorPctMedian" in out["summary"]
    assert out["durationSec"]["extract"] == 0.4
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_metrics.py -q`
Expected: `ImportError`.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/mesh/metrics.py`:

```python
"""지표 계산 (스펙 §7.2).

파라미터를 눈으로만 고르면 근거가 없다. 변형마다 부피 오차·위상 결함·비용을
자동 계산한다. 이 값은 판단 근거일 뿐 자동 게이트가 아니다(스펙 §7.2).
"""

from __future__ import annotations

import numpy as np
import trimesh


def _edge_defects(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """(경계 변 수, 비다양체 변 수).

    경계 변 = 면 하나에만 속한 변(구멍). 비다양체 변 = 셋 이상 면에 속한 변.
    """
    edges = mesh.edges_sorted
    # 각 정렬된 변의 등장 횟수
    _, inverse, counts = np.unique(
        edges, axis=0, return_inverse=True, return_counts=True
    )
    per_edge = counts[inverse]
    # inverse는 변마다 그룹 id. 그룹별 count로 분류.
    unique_counts = counts
    boundary = int(np.sum(unique_counts == 1))
    nonmanifold = int(np.sum(unique_counts > 2))
    return boundary, nonmanifold


def region_metrics(label_id, verts, faces, voxel_count, voxel_volume_mm3) -> dict:
    """스펙 §7.2 perRegion 항목 하나."""
    mesh = trimesh.Trimesh(
        vertices=np.asarray(verts), faces=np.asarray(faces), process=False
    )
    mesh_volume = float(abs(mesh.volume))
    voxel_volume = float(voxel_count) * float(voxel_volume_mm3)
    err = (
        100.0 * (mesh_volume - voxel_volume) / voxel_volume
        if voxel_volume > 0
        else 0.0
    )
    boundary, nonmanifold = _edge_defects(mesh)
    return {
        "labelId": int(label_id),
        "triangles": int(len(faces)),
        "meshVolumeMm3": round(mesh_volume, 2),
        "voxelVolumeMm3": round(voxel_volume, 2),
        "volumeErrorPct": round(err, 2),
        "surfaceAreaMm2": round(float(mesh.area), 2),
        "boundaryEdges": boundary,
        "nonManifoldEdges": nonmanifold,
    }


def summarize(per_region, durations, glb_bytes, labels_skipped) -> dict:
    """스펙 §7.2 metrics.json 전체."""
    total_tris = sum(r["triangles"] for r in per_region)
    # 정점 수는 per_region엔 없으니 삼각형 기반이 아니라 호출자가 넘긴 total을
    # 쓰지 않고, 여기선 삼각형만 합산하고 vertices는 generate가 채운다 —
    # 단, 테스트 편의를 위해 per_region에 실제 정점 합을 못 구하므로 generate가
    # total.vertices를 덮어쓴다. 여기선 0으로 두지 않고 삼각형/2 근사도 아니고,
    # 명시적으로 generate가 채우도록 키만 만든다.
    errs = [r["volumeErrorPct"] for r in per_region] or [0.0]
    return {
        "durationSec": durations,
        "glbBytes": int(glb_bytes),
        "total": {"vertices": 0, "triangles": int(total_tris)},
        "perRegion": per_region,
        "summary": {
            "volumeErrorPctMedian": round(float(np.median(errs)), 2),
            "volumeErrorPctMax": round(float(max(errs, key=abs)), 2),
            "regionsWithBoundaryEdges": int(sum(1 for r in per_region if r["boundaryEdges"] > 0)),
            "regionsWithNonManifold": int(sum(1 for r in per_region if r["nonManifoldEdges"] > 0)),
            "labelsSkippedByMinVoxel": int(labels_skipped),
        },
    }
```

**주의(type consistency):** `summarize`가 내는 `total.vertices`는 0으로 두고 `generate`(Task 7)가 실제 정점 합으로 덮어쓴다. Task 7 구현이 이 필드를 채운다.

`seg_and_mesh/mesh/__init__.py`에 추가:

```python
from seg_and_mesh.mesh.metrics import region_metrics, summarize
```

`__all__`에 `"region_metrics"`, `"summarize"` 추가.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_metrics.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋한다**

```bash
git add seg_and_mesh/mesh/metrics.py seg_and_mesh/mesh/__init__.py tests/mesh/test_metrics.py
git commit -m "feat(mesh): 라벨별·전체 지표(volumeErrorPct·경계/비다양체 변)"
```

---

## Task 7: 변형 생성 조합

**Files:**
- Create: `seg_and_mesh/mesh/generate.py`
- Modify: `seg_and_mesh/mesh/__init__.py`
- Test: `tests/mesh/test_generate.py`

**Interfaces:**
- Consumes: 전 축(Task 2~4), glb(Task 5), metrics(Task 6), `MeshParams`(Task 1), labels `load_canonical`(labels 서브시스템)
- Produces:
  - `@dataclass(frozen=True) VariantResult: variant_id: str; glb_path: Path; metrics: dict; params: dict; regions: list[dict]`
  - `generate_variant(seg_path, out_dir, params: MeshParams, index: int = 1, table=None) -> VariantResult` — canonical seg를 읽어 라벨별로 축1→2→3을 적용, affine 한 번 적용해 GLB·metrics.json·params.json을 `out_dir`에 쓰고 결과를 낸다.
  - `GenerateError(RuntimeError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/mesh/test_generate.py`:

```python
"""변형 생성 조합 (스펙 §6.5~§7.2)."""

from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from seg_and_mesh.mesh import (
    Decimation,
    Extractor,
    MeshParams,
    Preprocess,
    Smoothing,
    default_params,
)
from seg_and_mesh.mesh.generate import GenerateError, generate_variant


def test_generate_writes_all_variant_files(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)

    assert (out / "regions.glb").is_file()
    assert (out / "metrics.json").is_file()
    assert (out / "params.json").is_file()
    assert result.variant_id.startswith("v01-")


def test_glb_nodes_match_present_labels(tmp_path, synthetic_seg):
    """합성 seg에 라벨 13·1·40이 있다. min_voxel 아래는 없다."""
    out = tmp_path / "v01"
    generate_variant(synthetic_seg, out, default_params(), index=1)

    scene = trimesh.load(out / "regions.glb")
    assert set(scene.geometry.keys()) == {"label_13", "label_1", "label_40"}


def test_min_voxel_skips_small_labels(tmp_path, synthetic_seg):
    """min_voxel을 크게 잡으면 작은 라벨이 빠지고 metrics에 스킵 수가 남는다."""
    out = tmp_path / "v01"
    # 얇은 판(라벨 40)은 2*30*30=1800 voxel, 구는 반경6≈905, 큐브 1000
    params = MeshParams(
        preprocess=Preprocess(), extractor=Extractor(), smoothing=Smoothing(),
        decimation=Decimation(), min_voxel=1500,
    )
    result = generate_variant(synthetic_seg, out, params, index=1)

    node_ids = {r["labelId"] for r in result.regions}
    assert 40 in node_ids  # 1800 >= 1500
    assert 13 not in node_ids  # 905 < 1500
    assert 1 not in node_ids  # 1000 < 1500
    assert result.metrics["summary"]["labelsSkippedByMinVoxel"] == 2


def test_regions_carry_metadata_for_meta_json(tmp_path, synthetic_seg):
    """regions 항목은 regions-meta.json에 들어갈 재료다(스펙 §2.2)."""
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)

    by_id = {r["labelId"]: r for r in result.regions}
    hip = by_id[13]
    assert hip["name"] == "Left-Hippocampus"
    assert hip["nodeName"] == "label_13"
    assert hip["color"] == [220, 216, 20]
    assert hip["group"] == "subcortical"
    assert hip["side"] == "L"
    assert hip["volumeMm3"] > 0
    assert len(hip["centroid"]) == 3
    assert hip["triangleCount"] > 0


def test_params_json_has_variant_id_and_created_at(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    generate_variant(synthetic_seg, out, default_params(), index=1)

    params = json.loads((out / "params.json").read_text(encoding="utf-8"))
    assert params["variantId"].startswith("v01-")
    assert "createdAt" in params
    assert params["labelTable"] == "canonical-v1"


def test_metrics_total_vertices_filled(tmp_path, synthetic_seg):
    out = tmp_path / "v01"
    result = generate_variant(synthetic_seg, out, default_params(), index=1)
    assert result.metrics["total"]["vertices"] > 0


def test_affine_applied_once_world_coordinates(tmp_path):
    """비등방 affine에서 정점이 world mm로 한 번만 변환된다(이중 스케일 금지)."""
    import nibabel as nib

    vol = np.zeros((30, 30, 30), dtype=np.uint8)
    vol[10:20, 10:20, 10:20] = 1  # 큐브 라벨 1
    # 비등방 + 평행이동
    affine = np.array([
        [2.0, 0, 0, 100.0],
        [0, 1.0, 0, -50.0],
        [0, 0, 0.5, 30.0],
        [0, 0, 0, 1.0],
    ])
    img = nib.Nifti1Image(vol, affine)
    img.header.set_zooms((2.0, 1.0, 0.5))
    seg = tmp_path / "aniso.nii.gz"
    nib.save(img, seg)

    out = tmp_path / "v01"
    result = generate_variant(seg, out, default_params(), index=1)

    scene = trimesh.load(out / "regions.glb")
    mesh = scene.geometry["label_1"]
    # voxel 10..20 큐브의 world 좌표: x=100+2*10..100+2*20 = 120..140
    assert mesh.bounds[0][0] == pytest.approx(120.0, abs=1.0)
    assert mesh.bounds[1][0] == pytest.approx(140.0, abs=1.0)


def test_unreadable_seg_raises(tmp_path):
    bad = tmp_path / "bad.nii.gz"
    bad.write_bytes(b"not nifti")
    with pytest.raises(GenerateError):
        generate_variant(bad, tmp_path / "v01", default_params(), index=1)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/mesh/test_generate.py -q`
Expected: `ImportError`.

- [ ] **Step 3: 구현한다**

`seg_and_mesh/mesh/generate.py`:

```python
"""변형 생성 조합 (스펙 §6.5~§7.2).

canonical seg.nii.gz를 읽어 라벨별로 축1(전처리)→축2(추출)→축3(후처리)를
적용하고, 정점에 affine을 한 번만 적용해 world mm로 옮긴 뒤 GLB·metrics.json·
params.json을 쓴다. regions(라벨별 메타)는 소비 측 regions-meta.json 재료다.
"""

from __future__ import annotations

import json
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import nibabel as nib
import numpy as np

from seg_and_mesh.labels import load_canonical
from seg_and_mesh.mesh.extract import extract
from seg_and_mesh.mesh.glb import write_glb
from seg_and_mesh.mesh.metrics import region_metrics, summarize
from seg_and_mesh.mesh.params import MeshParams
from seg_and_mesh.mesh.postprocess import decimate, smooth
from seg_and_mesh.mesh.preprocess import apply_preprocess


class GenerateError(RuntimeError):
    """세그를 읽지 못했거나 변형 생성에 실패했다."""


@dataclass(frozen=True)
class VariantResult:
    variant_id: str
    glb_path: Path
    metrics: dict
    params: dict
    regions: list


def generate_variant(seg_path, out_dir, params: MeshParams, index: int = 1, table=None) -> VariantResult:
    """canonical seg + params -> 변형 하나. GLB·metrics.json·params.json을 쓴다.

    Raises:
        GenerateError: 입력을 읽지 못했거나 만들 메시가 하나도 없을 때.
    """
    table = table or load_canonical()
    by_id = table.by_id()
    seg_path = Path(seg_path)
    out_dir = Path(out_dir)

    try:
        img = nib.load(seg_path)
        data = np.asanyarray(img.dataobj)
    except (nib.filebasedimages.ImageFileError, OSError, EOFError, zlib.error) as exc:
        raise GenerateError(f"세그를 읽지 못했다: {seg_path}") from exc

    affine = img.affine
    R = affine[:3, :3]
    t = affine[:3, 3]
    voxel_volume = float(np.prod([abs(float(z)) for z in img.header.get_zooms()[:3]]))

    labels, counts = np.unique(data, return_counts=True)
    present = {int(l): int(c) for l, c in zip(labels, counts) if int(l) != 0}

    meshes: dict = {}
    regions: list = []
    per_region: list = []
    skipped = 0
    total_vertices = 0
    durations = {"extract": 0.0, "smooth": 0.0, "decimate": 0.0, "write": 0.0}

    for label_id, voxel_count in sorted(present.items()):
        entry = by_id.get(label_id)
        if entry is None:
            continue  # 표에 없는 값(있으면 안 되지만 방어)
        if voxel_count < params.min_voxel:
            skipped += 1
            continue

        mask = data == label_id

        t0 = time.perf_counter()
        field, isolevel = apply_preprocess(mask, params.preprocess)
        verts_vox, faces = extract(field, isolevel, params.extractor.name, dict(params.extractor.options))
        durations["extract"] += time.perf_counter() - t0

        if len(faces) == 0:
            skipped += 1
            continue

        t0 = time.perf_counter()
        verts_vox, faces = smooth(verts_vox, faces, params.smoothing)
        durations["smooth"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        verts_vox, faces = decimate(verts_vox, faces, params.decimation)
        durations["decimate"] += time.perf_counter() - t0

        # affine 한 번만 적용 -> world mm (스펙 §6.7, 이중 스케일 금지)
        verts_world = verts_vox @ R.T + t

        meshes[label_id] = (verts_world, faces)
        total_vertices += len(verts_world)

        rm = region_metrics(label_id, verts_world, faces, voxel_count, voxel_volume)
        per_region.append(rm)

        centroid = verts_world.mean(axis=0)
        regions.append({
            "labelId": label_id,
            "name": entry.name,
            "color": list(entry.color),
            "nodeName": f"label_{label_id}",
            "fsId": entry.fs_id,
            "group": entry.group,
            "side": entry.side,
            "volumeMm3": round(float(voxel_count) * voxel_volume, 2),
            "centroid": [round(float(c), 2) for c in centroid],
            "triangleCount": int(len(faces)),
        })

    if not meshes:
        raise GenerateError(f"만들 메시가 없다(모든 라벨이 min_voxel {params.min_voxel} 미만이거나 부재)")

    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    glb_path = out_dir / "regions.glb"
    glb_bytes = write_glb(meshes, glb_path)
    durations["write"] += time.perf_counter() - t0

    durations = {k: round(v, 3) for k, v in durations.items()}
    metrics = summarize(per_region, durations, glb_bytes, skipped)
    metrics["total"]["vertices"] = int(total_vertices)

    variant_id = params.variant_id(index)
    params_dict = params.to_params_dict()
    params_dict["variantId"] = variant_id
    params_dict["createdAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "params.json").write_text(
        json.dumps(params_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return VariantResult(
        variant_id=variant_id,
        glb_path=glb_path,
        metrics=metrics,
        params=params_dict,
        regions=regions,
    )
```

`seg_and_mesh/mesh/__init__.py`에 추가:

```python
from seg_and_mesh.mesh.generate import GenerateError, VariantResult, generate_variant
```

`__all__`에 `"GenerateError"`, `"VariantResult"`, `"generate_variant"` 추가.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/mesh/test_generate.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 전체 스위트 회귀**

Run: `uv run pytest -W error -q`
Expected: 실패 0.

- [ ] **Step 6: 실데이터 스모크(선택, 수동)**

실제 FastSurfer seg가 있으면(개발 머신) 한 변형을 만들어 부피 오차를 눈으로 확인한다. CI에는 안 넣는다.

```bash
uv run python -c "
from pathlib import Path
from seg_and_mesh.labels import remap_segmentation
from seg_and_mesh.mesh import generate_variant, default_params
# <seg.mgz>를 canonical로 리맵 후 변형 생성 — 경로는 개발 머신 것
"
```

- [ ] **Step 7: 커밋한다**

```bash
git add seg_and_mesh/mesh/generate.py seg_and_mesh/mesh/__init__.py tests/mesh/test_generate.py
git commit -m "feat(mesh): seg+params를 변형 하나로 조합(GLB·metrics·params·regions)"
```

---

## Self-Review 메모

- **스펙 §6.5 3축 전부 노출:** 전처리 3(Task 2)·추출 5(Task 3)·스무딩 4/데시 2(Task 4). `hc_laplacian`(pymeshlab)만 후속으로 명시 제외 — 범위 밖에 기록됨.
- **스펙 §6.7 GLB 계약:** `label_<id>` 노드, 색 미포함, affine 1회(Task 5·7). 이중 스케일 회귀 테스트 있음(`test_affine_applied_once_world_coordinates`).
- **스펙 §6.5 min-voxel:** Task 7 `test_min_voxel_skips_small_labels`.
- **스펙 §7.1 params.json:** camelCase, variantId·createdAt(Task 1·7).
- **스펙 §7.2 지표:** volumeErrorPct·boundaryEdges·nonManifoldEdges·labelsSkipped(Task 6). 자동 게이트 없음.
- **스펙 §13 합성 볼륨:** 구·큐브·판 픽스처(Task 2 conftest), 구 부피 해석해 대조(Task 3·6).
- **regions-meta.json 전체 작성은 범위 밖** — 이 엔진은 regions 배열 재료만 낸다. `space.toMNI152`·헤더는 jobs/web이 붙인다.
- **type consistency:** `metrics.summarize`의 `total.vertices=0`을 `generate`가 덮어씀 — Task 6 주의·Task 7 `test_metrics_total_vertices_filled`로 고정.
