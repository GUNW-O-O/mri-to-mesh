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

# 태그 고정. latest를 쓰지 않는다.
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

# uv가 파이썬을 따로 내려받지 않고 apt의 3.11을 쓰게 한다.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 의존성 레이어를 소스와 분리해 소스만 바뀌었을 때 재설치를 피한다.
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project

COPY seg_and_mesh /app/seg_and_mesh
# 런타임이 읽는 정적 라벨 표 (스펙 §3). 원본 LUT는 복사하지 않는다.
COPY labels/canonical-v1.tsv /app/labels/canonical-v1.tsv
COPY tests /app/tests
RUN uv sync --frozen

CMD ["uv", "run", "--frozen", "pytest", "-W", "error"]
