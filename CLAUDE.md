# mri2mesh

메쉬 생성옵션별 산출물 **품질 비교** 툴(brain-educate 에셋 생산용). 잡 간 비교는 부가기능. 캔버스 영역 클릭 상호작용 없음.

## 환경
- 테스트: `uv run pytest` — bare `python`은 Windows Store 스텁이라 깨짐. Python 3.11.
- Windows / PowerShell 기본, Bash 병행.

## PHI 경계
- PHI 금지선 = **git/공유 산출물**(공개 저장소·코드·테스트·docs·커밋). 로컬 gitignored `jobs/`는 예외(원본·before 로컬 보관 OK).
- 테스트 픽스처는 **가짜 메타만**(`Hong^Gil^Dong`).

## 익명화
- DICOM→NIfTI 변환 자체가 익명화(dcm2niix `-ba y`로 BIDS 사이드카 익명).
- 반출 `orig.nii.gz`·`seg.nii.gz`는 헤더 스크럽(`io/nifti_anon.py`) — 영상·affine·dtype·voxel·단위만 유지. seg=완전익명, orig=얼굴 형상 잔존(deface는 스텁, 예정).
- 세그 성공 후 원본(`input/`·`nifti/`·`fs/`) 삭제, 익명 orig/seg + mesh만 유지. 재작업은 원본 재수입.

## 메쉬 옵션 흐름
- 메쉬 생성 옵션은 **시리즈 선택 화면**에서 함께 고름(세그 후 게이트 아님) — 세그는 옵션과 무관하게 항상 동일.
- baseline = brainds `nifti_pipeline/mesh_export.py` 기준값(minVoxel 100, smoothing laplacian iter 30 relax 0.1, vtk contour). 안 건드리면 이 값.

## Docker
- static은 이미지에 COPY됨 → UI 변경은 `docker compose up --build`(dev용 static 바인드 마운트 있음). 백엔드도 재빌드 필요.
- `.env`(gitignore): `OUTPUT_DIR`(절대경로), `FASTSURFER_IMAGE`. env 접두사 `MRI2MESH_`.

## 커밋
- 한국어 conventional(feat/fix/chore).
