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

RUN addgroup -S app && adduser -S app -G app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

RUN mkdir -p /app/data && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
