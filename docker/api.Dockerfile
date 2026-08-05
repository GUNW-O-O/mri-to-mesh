# api 서비스 바닥 이미지 (스펙 §5.1 — compose 서비스는 api와 fastsurfer 둘).
# dcm2niix·라벨 리맵·메시 생성이 여기서 돈다. FastSurfer 이미지에는
# dcm2niix가 없으므로 이쪽이 갖는다.
#
# web 계획이 이 위에 FastAPI 레이어를 얹는다.
FROM debian:bookworm-slim

# dcm2niix 1.0.20220720-1+deb12u1 (bookworm)
# docker.io: api가 형제 FastSurfer 컨테이너를 `docker run`으로 띄우기 위한
# docker CLI(client 포함). compose가 호스트 docker socket을 마운트해 준다 —
# 이 이미지 안에 docker daemon은 없다(스펙 §5.1).
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
      dcm2niix \
      python3 \
      python3-venv \
      ca-certificates \
      docker.io \
 && rm -rf /var/lib/apt/lists/*

# 태그 고정. latest를 쓰지 않는다.
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

# uv가 파이썬을 따로 내려받지 않고 apt의 3.11을 쓰게 한다.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1

WORKDIR /app

# 의존성 레이어를 소스와 분리해 소스만 바뀌었을 때 재설치를 피한다.
# --no-dev: 이건 PHI를 서빙하는 런타임 이미지다 — 테스트 전용 의존성
# (httpx2·pytest)을 넣지 않는다. 테스트는 로컬에서 uv run pytest로 돈다.
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --no-dev

COPY mri2mesh /app/mri2mesh
# 런타임이 읽는 정적 라벨 표 (스펙 §3). 원본 LUT는 복사하지 않는다.
COPY labels/canonical-v1.tsv /app/labels/canonical-v1.tsv
# tests/는 서빙 이미지에 넣지 않는다 — 실행 코드가 아니다.
RUN uv sync --frozen --no-dev

# 컨테이너 안 잡 루트. compose가 여기에 호스트 OUTPUT_DIR을 바인드 마운트한다.
ENV MRI2MESH_JOBS_ROOT=/work/jobs
# 0.0.0.0으로 바인드해야 컨테이너 밖(호스트)에서 접근 가능하다 — 컨테이너
# 자신의 loopback은 호스트에서 안 보인다. 실제 외부 노출 차단은
# docker-compose.yml의 "127.0.0.1:${WEB_PORT}:8000" 퍼블리시가 맡는다.
CMD ["uv", "run", "--frozen", "uvicorn", "mri2mesh.web.server:app", \
     "--host", "0.0.0.0", "--port", "8000"]
