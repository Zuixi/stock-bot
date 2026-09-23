# 首次运行（Day 1）

单线教程：从零到「栈起来 + 看见行情 + 本地快检通过」。细节与排障见 [`index.md`](./index.md) · [`local-dev.md`](./local-dev.md)。

## 前提

- Docker ≥ 24、Compose V2
- Node.js ≥ 22（预构建前端时需要）
- 约 30–45 分钟（含首次镜像构建）

## 步骤

### 1. 环境变量（两个文件，勿混用）

```bash
cp .env.docker.example .env
cp backend/.env.example backend/.env
```

在 `backend/.env` 填入真实 `TUSHARE_TOKEN`。根目录 `.env` 与 `backend/.env` 分工见 [`index.md`「环境变量配置」](./index.md#环境变量配置)。

### 2. 预构建前端（网络受限或首次 compose 必做）

Dockerfile 的 `runtime` 阶段只打包 `dist/`：

```bash
cd frontend && npm ci && npm run build && cd ..
```

### 3. 启动全栈

```bash
docker compose up -d --build
docker compose ps
```

端口与 URL 以 [`index.md` 权威表](./index.md) 为准；行情台通常经网关 **http://localhost**（不是直连 `:8000`）。

### 4. 验证页面

- 打开 **http://localhost** → 行情相关页面可加载（免登录区块）
- 需登录功能（自选等）须在网关下测，见 [ADR 0002](../decisions/0002-split-auth-service-and-forward-auth.md)

### 5. 本地快检

```bash
bash scripts/self_review.sh
```

有 backend 改动且环境就绪时再跑：

```bash
bash scripts/self_review.sh --full
```

（`--full` 会清空 `TUSHARE_TOKEN` 跑 pytest，与 CI 口径一致。）

## 首日建议不读

- `plans/*.md` **正文**（状态只看 [`plans/index.md`](../../plans/index.md)；`unverified` 勿当规格）
- [`frontend-architecture.md`](../frontend-architecture.md)（已迁移，见 [`authority.md`](../authority.md)）
- 根目录 `src/`、`tests/`（早期 CLI 遗留）

## 下一步

| 我想 | 去 |
|---|---|
| 按任务找文档 | [`../index.md`](../index.md) |
| 改代码前的约束 | [`AGENTS.md`](../../AGENTS.md)（仓库根） |
| 功能在哪 | [`../features.md`](../features.md) |
