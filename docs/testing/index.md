# 测试与门禁

本目录是**测试与门禁的权威文档**：什么检查、何时跑、是否阻断、失败了怎么办。

命令的唯一真相在脚本与 CI 配置里；本目录只解释"什么时候跑它、看什么判据"。**不要在文档里抄命令的完整形态**（CI 一改口径，文档立刻说谎）。

## 门禁矩阵

列集合 = `.github/workflows/ci.yml` 的 job ∪ `scripts/self_review.sh`（pre-commit 兜底）∪ `scripts/bench.sh`。由 `scripts/doc_gate.sh` 校验一致性：**新增 CI job 或删改 self_review 步骤必须同步本表**。

| 检查项 | 命令来源 | 何时跑 | 阻断 | 失败怎么办 |
|---|---|---|---|---|
| 空白/冲突标记 | `self_review.sh` [1/4] · CI `docs-consistency` | commit / PR | 是 | 修空白、删冲突标记 |
| 后端 lint + format | `self_review.sh` [2/4]（仅改动文件）· CI `backend-lint`（全量） | commit / PR | 是 | `uv run --extra dev ruff format <files>` |
| 后端类型 | `self_review.sh --full` · CI `backend-typecheck` | 手动全量 / PR | 是（CI） | 修类型；别用 `# type: ignore` 掩盖契约错误 |
| 后端单测（排除 e2e/bench） | `self_review.sh --full` · CI `backend-test` · pre-commit `backend-test` | commit / PR | 是 | 见 [`backend.md`](./backend.md) |
| 前端类型 `tsc --noEmit` | `self_review.sh --full` · CI `frontend-lint` · pre-commit | commit / PR | 是 | 修类型；见 [ADR 0006](../decisions/0006-no-eslint-tsc-as-static-check.md) |
| 前端设计令牌 | CI `frontend-lint`（`npm run check:design`） | PR | 是 | 涨跌色对比度 ≥ 4.5 且 `theme.ts` ↔ `theme.css` 必须一致 |
| 前端构建 | CI `frontend-build` | PR | 是 | `npm run build`（含 `tsc -b`） |
| auth-service lint / 类型 / 测试 | CI `auth-service-lint` · `auth-service-typecheck` · `auth-service-test` | PR | 是 | 与后端同口径，但**独立库与版本线** |
| CPU 基准 Tier 1 | `scripts/bench.sh` · CI `bench-cpu` | 手动 / PR | 是 | 见 [`benchmarks.md`](./benchmarks.md) |
| 全栈冒烟（compose） | CI `docker-smoke` | PR | 是 | 起栈 + 网关路由 + 登录闭环；失败时 CI 会打印 Traefik rawdata 与日志 |
| 文档一致性（Changelog 启发式） | `self_review.sh` [3/4] · CI `docs-consistency` | commit / PR | 否（告警） | 补 `docs/Changelog.md` |
| 文档交叉引用 / ADR 只增不改 / 端口表 / 空文件 | `scripts/doc_gate.sh` · CI `docs-consistency` | commit / PR | 是（e2e 术语为告警） | 按报错 `hint:` 修；见 [`../index.md`](../index.md) |
| 改动探测器（slop_scan） | `scripts/slop_scan.sh` · `self_review.sh` [2/4] 后 | commit / 手动 | 否（告警） | 命中则复核 best-practices 对应分类 |
| 后端 e2e（需 docker 栈） | `pytest -m e2e` | 手动 | — | 见 [`backend.md`](./backend.md) |
| 前端 Playwright e2e | `npm run test:e2e` | 手动（**不在 CI**） | — | 见 [`frontend-e2e.md`](./frontend-e2e.md) |

## 三档节奏

| 档次 | 触发 | 覆盖面 | 耗时量级 |
|---|---|---|---|
| 快检 | `git commit`（pre-commit） | 改动文件的 ruff check+format、后端单测、`tsc`、空白检查 | 秒级 |
| PR CI | 推 PR / 合 main | 全量 lint、mypy、pytest（含 DB 服务）、前端构建、compose 冒烟、bench A/B | 分钟级 |
| 手动全量 | `bash scripts/self_review.sh --full` | 上面 + mypy + pytest（清空 `TUSHARE_TOKEN`）+ `tsc` | 分钟级 |

## 术语消歧（务必区分）

本仓库有两个都叫 "e2e" 的东西：

- **后端 `e2e` marker**：`backend/tests/` 中用 `@pytest.mark.e2e` 标记的用例，**需要 docker compose 栈在运行**（api + worker + rabbitmq）。默认被 `-m "not e2e and not bench"` 排除。例：`tests/test_industry_e2e.py`
- **前端 Playwright e2e**：`frontend/e2e/*.spec.ts`，由 `npm run test:e2e` 驱动，`baseURL` 默认 `http://localhost:3000`

在文档或讨论中提 "e2e" 时**必须写明是哪一个**。

## 新增测试怎么归类

| 你要验证的 | 放哪 | 需要什么 |
|---|---|---|
| 纯函数 / 规则引擎 / 归一化 | `backend/tests/test_*.py`（无 marker） | 什么都不需要，秒级 |
| 需要真 DB / Redis 的行为 | 同上（**不加 marker**，但必须能连测试库） | 见 [`backend.md`](./backend.md) 的 fixture 约定 |
| 跨服务全链路（含队列） | 加 `@pytest.mark.e2e` | docker compose 栈 |
| 真实浏览器交互 | `frontend/e2e/*.spec.ts` | 起前端 + 后端 |
| 性能回归 | `backend/tests/benchmarks/` + `bench` marker | `scripts/bench.sh` |
