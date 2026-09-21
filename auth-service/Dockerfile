# ---- Stage 1: dependency install ----
# 基础镜像来自**官方上游**（不指定镜像源，与业内一致）+ digest 固定：
# 官方源是真多架构（amd64/arm64），且不受第三方镜像源缓存滞后影响。
# 国内本机构建 Docker Hub 不通时，在 Docker Desktop → Settings → Docker Engine 配
# `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中）。
# 升级 digest：docker buildx imagetools inspect <image> 看顶层 Digest。/ 详见 docs/build.md「镜像构建」。
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:66cfb8e0b8aca1decede4fddf5fecd750eea0b5f82dad20a8355b6e67f0b3982 AS builder

WORKDIR /app

ARG UV_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
ENV UV_DEFAULT_INDEX="${UV_INDEX}" \
    UV_HTTP_TIMEOUT="120" \
    UV_LINK_MODE="copy"

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --no-dev --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system --requirement /tmp/requirements.txt

# ---- Stage 2: runtime ----
# 基础镜像来自**官方上游**（不指定镜像源，与业内一致）+ digest 固定：
# 官方源是真多架构（amd64/arm64），且不受第三方镜像源缓存滞后影响。
# 国内本机构建 Docker Hub 不通时，在 Docker Desktop → Settings → Docker Engine 配
# `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中）。
# 升级 digest：docker buildx imagetools inspect <image> 看顶层 Digest。/ 详见 docs/build.md「镜像构建」。
FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e AS runtime

WORKDIR /app

ARG VERSION=dev
ARG COMMIT=none
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="stock-bot-auth-service" \
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
# 白名单拷贝 console script：服务本体用 uvicorn，migrate-auth 服务用 alembic
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/alembic /usr/local/bin/
COPY . .

RUN addgroup -S app && adduser -S app -G app \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
       /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
       /usr/local/bin/pip* \
    && chown -R app:app /app

USER app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
