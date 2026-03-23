FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/astral-sh/uv:python3.13-alpine

WORKDIR /app

# Unset any proxy set by Docker buildkit / host
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""
ENV no_proxy="*"

# Use Aliyun pypi mirror
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV UV_TRUSTED_HOST=mirrors.aliyun.com

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache -e ".[dev]"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
