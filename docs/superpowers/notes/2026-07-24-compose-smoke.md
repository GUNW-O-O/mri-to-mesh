# compose 수동 스모크 절차

- 작성일: 2026-07-24 (Task 7)
- 목적: `docker compose up`으로 뜨는 로컬 워크벤치를 실 GPU·실 DICOM으로
  1회 눈으로 확인. CI/자동 테스트로는 대체하지 않는다(뷰어 JS·compose는
  스펙상 수동 검증 구역).

## 절차

1. `cp .env.example .env` 후 `OUTPUT_DIR`·`FASTSURFER_IMAGE`를 채운다.
   - `OUTPUT_DIR`: 잡 산출물을 쌓을 호스트의 절대 경로(존재해야 함).
   - `FASTSURFER_IMAGE`: 예 `deepmi/fastsurfer:cuda-v2.5.4`. `latest` 금지.
2. `docker compose up --build`
3. 브라우저로 `http://localhost:8000` 접속, 실제 T1 DICOM zip 업로드.
4. 시리즈 선택 → 실행 → 완료 후 변형(GLB) 로드까지 확인.
5. 확인 후 `docker compose down`.

## 확인할 것

- 포트가 `127.0.0.1`에만 열려 있는지(다른 기기에서 `http://<이 호스트의 LAN IP>:8000`
  으로 접속이 안 돼야 한다) — `docker-compose.yml`의 `127.0.0.1:${WEB_PORT}:8000`
  퍼블리시가 이를 보장한다.
- api 컨테이너가 `docker run`으로 형제 FastSurfer 컨테이너를 실제로 띄우는지
  (`/var/run/docker.sock` 마운트 + `docker.io` CLI).
- FastSurfer가 쓰는 `-v` 마운트 경로가 **호스트** 경로인지(`SAM_HOST_JOBS_ROOT`
  → `to_host_path` 변환 확인). 컨테이너 내부 경로(`/work/jobs/...`)가 그대로
  넘어가면 형제 컨테이너 안에서 해당 경로가 없어 실패한다.
- `status.json`을 브라우저 쪽 API 응답으로 봤을 때 `input.filename`이
  `<file>`로 마스킹돼 있는지(스펙 §12, PHI).

## 알려진 제약

- GPU가 없는 개발 환경에서는 이 스모크를 실행할 수 없다(FastSurfer는
  `--seg_only`라도 실질적으로 GPU를 가정). CPU 폴백 이미지
  (`deepmi/fastsurfer:cpu-v2.5.4`)로 대신 돌리면 GPU 없이도 절차 자체는
  검증 가능하나 훨씬 느리다.
- 이 문서 작성 시점(Task 7)에는 실제로 실행하지 않았다 — 절차만 기록.
  다음에 실 GPU 호스트에서 수행할 때 결과를 이 파일에 추가한다.
