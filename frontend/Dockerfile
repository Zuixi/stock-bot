# ---- Stage 1: build (requires network access) ----
# 基础镜像按 digest 固定（2026-09-21）：浮动 tag 一旦上游补丁版本变动，依赖层整层失效，
# 服务器每次发版就得重下 ~300MB（实测 v0.0.5 拉取 14 分钟）。升级姿势见 docs/build.md「镜像构建」。
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22@sha256:c0d6bf7b78c76eb82d67a3572f3c738af993a9cce0ea0b3fb3a4d25d4ee53fb5 AS builder

WORKDIR /app

ENV HTTP_PROXY="" \
    HTTPS_PROXY="" \
    http_proxy="" \
    https_proxy="" \
    no_proxy="*"

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --include=optional \
    && npm i @rollup/rollup-linux-x64-gnu --no-save

COPY . .

ENV VITE_API_BASE=""
RUN npm run build

# ---- Stage 2: serve ----
# 基础镜像按 digest 固定（2026-09-21）：浮动 tag 一旦上游补丁版本变动，依赖层整层失效，
# 服务器每次发版就得重下 ~300MB（实测 v0.0.5 拉取 14 分钟）。升级姿势见 docs/build.md「镜像构建」。
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/nginx:alpine@sha256:ad410b95f177bbfb6693662c031eee92e59c3932c2c55c45cf3032cf6ee193d5 AS runtime

ARG VERSION=dev
ARG COMMIT=none
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="stock-bot-frontend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV TZ=Asia/Shanghai

COPY nginx.conf /etc/nginx/conf.d/default.conf

# Always copy build artifacts from builder stage
COPY --from=builder /app/dist/ /usr/share/nginx/html/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
