# 컨테이너에서 테스트 돌리기

Windows 호스트에는 dcm2niix가 없다. `dcm2niix` 표시가 붙은 테스트는
호스트에서 항상 skip된다. 심볼릭 링크 테스트도 호스트에서는 링크 생성
권한이 없어(WinError 1314) skip된다. 컨테이너로 돌리면 둘 다 실제로 실행된다.

## 빌드

    docker build -f docker/api.Dockerfile -t mri2mesh-api:dev .

## 데이터 없이

    docker run --rm mri2mesh-api:dev

`realdata` 표시 테스트는 skip된다.

## 실데이터로

    docker run --rm -v "C:\path\to\test-asset:/data:ro" -e MRI2MESH_TEST_DATA_DIR=/data mri2mesh-api:dev

`/data` 안에 같은 스터디의 DICOM 폴더 하나와 ZIP 하나가 있어야 한다.
이 구성에서 skip이 0이 된다 — 스위트 전체가 실제로 실행되는 유일한 경로다.

**마운트하는 폴더에는 환자 데이터가 들어 있다.** 읽기 전용(`:ro`)으로 붙이고,
이 폴더를 저장소에 커밋하지 않는다 — `.gitignore`와 `.dockerignore` 양쪽에
`test-asset/`이 들어 있다.

## 이미지 범위

이 이미지는 스펙 §5.1 `api` 서비스의 바닥이다. dcm2niix와 파이썬 의존성만
들어 있다. FastAPI·compose·뷰어는 `web` 계획에서 얹는다.
