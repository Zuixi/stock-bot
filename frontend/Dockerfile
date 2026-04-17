# ---- Stage 1: build (requires network access) ----
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22 AS builder

WORKDIR /app

ENV HTTP_PROXY="" \
    HTTPS_PROXY="" \
    http_proxy="" \
    https_proxy="" \
    no_proxy="*"

COPY package.json package-lock.json ./
RUN npm ci --include=optional \
    && npm i @rollup/rollup-linux-x64-gnu --no-save

COPY . .

ENV VITE_API_BASE=""
RUN npm run build

# ---- Stage 2: serve ----
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/nginx:alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf

# Always copy build artifacts from builder stage
COPY --from=builder /app/dist/ /usr/share/nginx/html/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
