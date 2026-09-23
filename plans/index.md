# plans 索引

状态视图。**新增计划必须在此登记**；`status` 只允许来自可核对的事实，不作推断。

- `completed` — 正文声明已完成，或代码/部署现状可核对
- `active` — 正文声明仍有未完成阶段
- `unverified` — 无状态声明且无可靠佐证；回访该计划时补认
- `superseded` — 被后续计划取代

| 文件 | 日期 | 主题 | 状态 | 依据 |
|---|---|---|---|---|
| [industry-research-workbench.md](./industry-research-workbench.md) | (未注明) | 行业投研工作台（猪智投）产品化主线 | active | `AGENTS.md` 列为本项目产品化方向 |
| [2026-09-02-industry-workbench-hardening.md](./2026-09-02-industry-workbench-hardening.md) | 2026-09-02 | 工作台加固（P1–P4 评审修复） | unverified | 复选框 0✓/43☐ |
| [2026-09-03-akshare-integration.md](./2026-09-03-akshare-integration.md) | 2026-09-03 | AKShare 真实数据接入 + Playwright 浏览器验证 | unverified | 与 [ADR 0001](../docs/decisions/0001-single-primary-source-tushare.md) 相关：其兜底路径后续实测容器内不可用 |
| [2026-09-03-kline-component-upgrade.md](./2026-09-03-kline-component-upgrade.md) | 2026-09-03 | K 线组件升级 | unverified | 复选框 47✓/0☐ |
| [2026-09-03-kline-p4-ui-iteration.md](./2026-09-03-kline-p4-ui-iteration.md) | 2026-09-03 | K 线 P4 UI 迭代 | unverified | 复选框 16✓/0☐ |
| [2026-09-03-market-data-face.md](./2026-09-03-market-data-face.md) | 2026-09-03 | 市场数据面 | unverified | 复选框 0✓/84☐ |
| [2026-09-03-unit-normalization.md](./2026-09-03-unit-normalization.md) | 2026-09-03 | 单位口径统一 | unverified | 复选框 0✓/7☐；`tests/benchmarks/test_normalization_bench.py` 存在 |
| [2026-09-03-workbench-p3-p6-completion.md](./2026-09-03-workbench-p3-p6-completion.md) | 2026-09-03 | 工作台收尾（P3 剩余 + P5 + P6） | completed | 正文声明：五阶段全部落地并提交 |
| [2026-09-09-auth-gateway-implementation.md](./2026-09-09-auth-gateway-implementation.md) | 2026-09-09 | 认证微服务 + API Gateway + 多用户数据归属 | completed | `docker-compose.yml` 已含 `gateway`/`auth-service`/`forward-auth`/`migrate-auth` |
| [2026-09-09-benchmark-tiers.md](./2026-09-09-benchmark-tiers.md) | 2026-09-09 | Benchmark 分级（Tier 1/2）与门禁 | unverified | `scripts/bench.sh` + `benchmarks/baseline.json` + `tests/benchmarks/` 齐全 |
| [2026-09-11-public-market-homepage.md](./2026-09-11-public-market-homepage.md) | 2026-09-11 | 公开行情台首页（定位拍板） | unverified | 正文为定位拍板；`frontend/src/pages/market*` 已存在 |
| [2026-09-11-public-market-homepage-tasks.md](./2026-09-11-public-market-homepage-tasks.md) | 2026-09-11 | 公开行情台首页（Phase 0–3 任务分解） | unverified | 复选框 0✓/90☐ |
| [2026-09-14-limit-up-sentiment.md](./2026-09-14-limit-up-sentiment.md) | 2026-09-14 | 连板梯队与市场情绪 | unverified | 复选框 0✓/60☐ |
| [2026-09-17-data-sync-self-healing.md](./2026-09-17-data-sync-self-healing.md) | 2026-09-17 | 对账式自愈数据面 | active | 正文声明：Phase 1+2 已实机验收，Phase 3 待排期 |
| [2026-09-17-market-sentiment-overhaul.md](./2026-09-17-market-sentiment-overhaul.md) | 2026-09-17 | 市场数据可信度与实时化改造 | completed | 正文声明：Phase 0–3 全部实施完毕并通过门禁 |
| [2026-09-18-concept-boards-and-new-stocks.md](./2026-09-18-concept-boards-and-new-stocks.md) | 2026-09-18 | 概念板块成分 + 次新股追踪 | completed | 正文声明：T0–T16 全部落地（T17 刻意跳过） |
| [2026-09-23-documentation-system.md](./2026-09-23-documentation-system.md) | 2026-09-23 | 文档系统建设（分层知识库 · 决策记录 · 操作层） | completed | 正文头部与 §十 实施记录；偏离项已逐条说明 |
| [2026-09-24-docs-system-phase2.md](./2026-09-24-docs-system-phase2.md) | 2026-09-24 | 文档系统二期（权威矩阵 · CI doc_gate · first-run · slop_scan） | completed | 正文 status: completed；Changelog 2026-09-24 |

## 维护约定

- 状态变更只改本表与对应计划的头部，**不回改计划正文**
- `unverified` 的条目应随回访逐步收敛为 `completed`；长期无回访的计划保持 `unverified` 是可接受的（明确未知优于错误状态）
- 被取代的计划：状态改 `superseded`，并在"依据"列写替代计划文件名
