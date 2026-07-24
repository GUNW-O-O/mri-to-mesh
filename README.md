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
