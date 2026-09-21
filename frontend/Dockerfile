# ---- Stage 1: build (requires network access) ----
# 基础镜像来自**官方上游**（不指定镜像源，与业内一致）+ digest 固定：
# 官方源是真多架构（amd64/arm64），且不受第三方镜像源缓存滞后影响。
# 国内本机构建 Docker Hub 不通时，在 Docker Desktop → Settings → Docker Engine 配
# `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中）。
# 升级 digest：docker buildx imagetools inspect <image> 看顶层 Digest。/ 详见 docs/build.md「镜像构建」。
FROM node:22@sha256:dd5847a04b0deee391fa145f1f4c6d214196668b6bcc7988ebed67249f226844 AS builder

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
# 基础镜像来自**官方上游**（不指定镜像源，与业内一致）+ digest 固定：
# 官方源是真多架构（amd64/arm64），且不受第三方镜像源缓存滞后影响。
# 国内本机构建 Docker Hub 不通时，在 Docker Desktop → Settings → Docker Engine 配
# `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中）。
# 升级 digest：docker buildx imagetools inspect <image> 看顶层 Digest。/ 详见 docs/build.md「镜像构建」。
FROM nginx:alpine@sha256:62ff2089abf5a9ed33bd232895bef5e22f7bb4b200675cec49a5ebc48e3d4ac8 AS runtime

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
