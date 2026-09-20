# ---- Stage 1: dependency install ----
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/astral-sh/uv:python3.13-alpine AS builder

WORKDIR /app

ARG UV_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
ENV UV_DEFAULT_INDEX="${UV_INDEX}" \
    UV_HTTP_TIMEOUT="120" \
    UV_LINK_MODE="copy"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system --requirement /tmp/requirements.txt

# ---- Stage 2: runtime ----
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.13-alpine AS runtime

WORKDIR /app

ARG VERSION=dev
ARG COMMIT=none
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="stock-bot-backend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# TZ 只影响日志时间戳格式（业务时区逻辑在代码内显式用 ZoneInfo("Asia/Shanghai")）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    APP_VERSION="${VERSION}" \
    APP_COMMIT="${COMMIT}"

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
# 白名单拷贝 console script：api 用 uvicorn，migrate 服务用 alembic；
# builder 的 uv/uvx/pip/f2py/tqdm/… 不进运行时（worker/scheduler 走 python -m）
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/alembic /usr/local/bin/
COPY . .

RUN addgroup -S app && adduser -S app -G app \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
       /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
       /usr/local/bin/pip* \
    && mkdir -p /app/data && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
