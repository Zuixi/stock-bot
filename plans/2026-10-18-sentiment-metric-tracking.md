---
status: completed
scope: 短线情绪「情绪温度计」9 张卡的持久化补齐 + 单指标历史追踪详情页（盘后冻结、盘中不落库、读时计算）
touches: backend/app/models/market_data.py, backend/app/migrations/versions/, backend/app/services/limit_up_service.py, backend/app/services/sentiment_metric_service.py, backend/app/schemas/limit_up.py, backend/app/api/v1/market_data.py, backend/app/repositories/limit_up_repo.py, backend/scripts/backfill_sentiment_metrics.py, backend/tests/, frontend/src/shared/api/limitUp.ts, frontend/src/features/sentiment/, frontend/src/features/market/components/SentimentThermometer.*, frontend/src/pages/market-sentiment-metric/, frontend/src/pages/market/sections.tsx, frontend/src/app/router/, frontend/scripts/check-routes.mjs, docs/decisions/0021-sentiment-metric-tracking.md, docs/design/, docs/features.md, docs/Changelog.md
updated: 2026-10-18
next-action: -
---

# 短线情绪指标持久化与历史追踪 实施计划

> **For agentic workers:** 本文件即 spec。任务逐条勾选；每一步的验收都在本地真库
> （`127.0.0.1:5433`，`market_sentiment_daily` 244 行 / 2025-10-15 → 2026-10-16）上跑过。

**Goal:** 让「情绪温度计」的 9 张卡各自可追踪：盘后按交易日冻结、盘中不写日表、点击卡片进入
单指标详情页（序列 + 分位参考线 + 分布 + 窗口统计 + 逐日表）。用户能回答"今天这个数在历史上什么位置、
连续几天、样本够不够"，而不是只能看今天。

**Why now（用户实测）:** `/market` 短线情绪能读今天，但卡片不可点、没有单指标历史；核对源码发现
`market_sentiment_daily` 已冻结 244 天，却**缺两张卡的列**（今开溢价、昨日涨停样本数）、
`/sentiment/trends` 又**丢掉已冻结的列**（`yzt_avg_pct` / `promo_2to3`）。缺的是读面与两个列，
不是"再建一张事实表"。

**Architecture:** 三层分工不变——
1. **持久层（日级，盘后）**：`market_sentiment_daily` 补 `yzt_avg_open_premium` / `yzt_n`；
   `persist_snapshot` 一并写；历史行由只补 NULL 的回补脚本填。
2. **读面（读时计算，不物化）**：新增 `sentiment_metric_service`（纯函数 + Redis 缓存 900s）
   与 `GET /market/sentiment/metrics/{metric_key}`；统计/分位/分布/连续天数全在后端。
3. **前端**：`/market/sentiment/:metric` 详情页 + 温度计卡片可点（仅收盘口径）。

**口径依据：** [ADR 0021](../docs/decisions/0021-sentiment-metric-tracking.md)、
[limit-up-sentiment §3.6](../docs/design/limit-up-sentiment.md)、
[ADR 0014](../docs/decisions/0014-market-derived-fact-persistence.md)（A/B/C 判据）。

## Global Constraints

- 盘中口径**不写** `market_sentiment_daily`；`market_sentiment_intraday` 是缓存不是事实。
- **缺失 ≠ 0**：NULL 不进样本、不计 0；样本不足不给分位/均值/分布。
- 统计量/分位只在后端算，前端只做量纲渲染（ratio ×100）。
- 分层：`api/v1 → services → repositories`；新增服务不写库。
- 迁移与模型同 PR，`uv run alembic heads` 单 head；新增列 NULL 允许。
- 每步收尾：`git status --porcelain` + `bash scripts/slop_scan.sh` + `bash scripts/self_review.sh`；
  改路由同步 IA §4 / ARCHITECTURE §6 / `check-routes.mjs`。

## Task 1: 持久化补齐（列 + 写入 + 历史回填）

**为什么：** 9 张卡里有 2 张（今开溢价、昨日涨停均值）的列压根不在日表，历史无法重算成"当天看到的值"。

- [x] 迁移 `c1a4e7b9d2f6`：`+yzt_avg_open_premium Float NULL`、`+yzt_n Integer NOT NULL default 0`
- [x] 模型 `MarketSentimentDaily` 同列；`persist_snapshot` 写两列（`.get` 兼容旧 Redis 快照）
- [x] `list_sentiment_calendar` 回两列；`SentimentCalendarPointOut` 追加字段
- [x] `scripts/backfill_sentiment_metrics.py`：一条 CTE 算全区间，**只 UPDATE 这两列**；`--dry-run/--force`
- [x] 真库实测：`2026-10-16` 回填 `yzt_avg_open_premium=1.4038` / `yzt_n=36`，与
      `get_snapshot(2026-10-16)` 的 KPI **逐字一致**

## Task 2: 趋势序列补列（不改分位面）

**为什么：** `sentiment_series_row` 只投影 7 个字段，库里明明有今开溢价与 2进3，前端读不到。

- [x] `sentiment_series_row` 追加 `yzt_avg_pct / yzt_avg_open_premium / yzt_n / promo_2to3 / promo_2to3_n`
- [x] `SentimentTrendPointOut` 同字段（追加，向后兼容）；`/sentiment/trends` 的 3 个分位指标不变
- [x] 前端 `limitUp.ts` 类型 + mapper 同步

## Task 3: 单指标详情端点（读时计算）

- [x] `sentiment_metric_service.METRIC_DEFS`：9 个指标的 label/unit/group/方向/口径原文/样本列
- [x] 纯函数 `metric_payload`：有效样本过滤、统计量（样本 ≥5）、分位（≥60）、直方图（≥10）、
      连续同向天数、较前一**有效**值、9 个 peers
- [x] `GET /market/sentiment/metrics/{metric_key}?days=250`（公开；未知 key → 404）
- [x] 缓存键 `market:limit-up:metric:{key}:{days}`；落库时与日历缓存同点失效
- [x] 单测 16 条：NULL/分母 0 排除、59 vs 60、`<=` 分位口径、跨 NULL 的 delta、分布守恒、
      连续天数断点、peers 顺序、未知 key 404

## Task 4: 前端详情页 + 卡片可点

- [x] 路由 `/market/sentiment/:metric`（`MarketSentimentMetricPage`，公开）；IA §4 / ARCH §6 /
      `check-routes.mjs` 三处登记（NEW_S1_ROUTES 8 条）
- [x] 详情页：hero（值/较前值/分位/连续天数/样本）+ 9 指标切换器 + 主图（P25/P50/P75 参考线 +
      dataZoom 全窗）+ 分布（当前值落箱）+ 窗口统计 + 最近 20 日表 + 口径注
- [x] 温度计 9 张卡改为 `<Link>`（键盘可达、可中键新开）；**盘中口径不可点**（没有同口径历史）
- [x] 分区提示文案改为「收盘口径（本地自算 · 可回放 · 点任一指标看历史）」
- [x] 视觉验证：1440×2320 桌面截图 + 390×844 窄屏截图（`broken_rate` / `zt_count`）；窄屏无横向溢出、
      分位线标签改为图下静态文案、表格只留 日期/值/较前值 三列（不再折行）

## Task 5: 文档与门禁

- [x] ADR 0021 + `docs/decisions/index.md`
- [x] `docs/design/limit-up-sentiment.md`：§3.6 补持久化与详情页、§四表列、§五新端点
- [x] `docs/features.md` 新增一行（路径与 API 前缀可执行）
- [x] `docs/Changelog.md` 一句话记录
- [x] `frontend/scripts/check-routes.mjs` / IA §4 / ARCH §6 同步；`npm run check:routes` 通过
- [x] 后端 ruff / mypy / pytest（相关 89 passed）；前端 `tsc --noEmit` / `npm run build`

## Task 6: 体验修复（2026-10-18 用户实测反馈）

- [x] **切指标整页抖动**：`RouteErrorBoundary` 的 key 由 `pathname` 改为 `routePatternKey(pathname)`
      （`app/router/routeKey.ts`）；query 加 `keepPreviousData` + 顶部 2px 进度条；实测 scroll 600→600、
      skeleton 0、标题在数据到达后整体切换
- [x] `check-routes.mjs` 新增断言：`<ErrorBoundary key={pathname}>` 即失败（防回归）
- [x] **返回入口**：裸文字改 `ArrowLeftOutlined` 胶囊按钮；标题旁长句改 chip + meta 行
- [x] **历史分布**：`当前` 标签水平居中于竖线顶端、加底色描边；窄屏隐藏 y 轴名；标签用完整精度
- [x] **近 20 日**：表格改折线（红/绿点=较前一日、末点标签），精确数字进默认收起的逐日明细
- [x] 主图分位线标签改为图下静态文案（数值接近时不再互相压字）；窄屏 switcher 右缘渐隐
- [x] 指标切换器吸附在顶栏下（`top:64px`）：读长图/明细时仍可直接换指标，不必滚回顶部

## Task 7: 情绪脉搏 + 阶段 + 小样本治理 + 零位移（2026-10-19）

- [x] **切指标零位移**：`smd-loading` 改正 `position:fixed` 覆盖层；Wilson 区间改所有指标固定 sub 行；
      样本门槛注恒显；hero rank/hint 预留 min-height。实测 1440：scrollY/bodyH 切换前后均为 600/1798；
      390：hero 215→215
- [x] **阶段规则** `sentiment_regime_service`：PIT 250 日分位、绝对水位守卫、持续 2 天平滑、
      末段以当日原始判定为准、`REGIME_VERSION=s1.r2`；详情页背景带 + 图例
- [x] **小样本**：`n<5` 标 `low_sample`（原始值保留、空心点、不进统计/分位/分布/连续天数）；
      Wilson 95% 区间；peer 与 hero 同源（`50.00%*`）
- [x] **pulse 端点** `GET /sentiment/pulse?days=60&benchmark=399006.SZ`：9 指标序列 + regime +
      指数 5 日收益分位 + 同分位轴对比 + 连续背离；缓存 900s / 失效前缀
- [x] **情绪脉搏组件**：左侧阶段栏 + 2×2 small multiples（`echarts.connect` 联动）+
      情绪 vs 创业板指对比；20/60/120/250 与基准写 URL；`收盘口径` chip；移动端 2×2
- [x] **详情页窗口** `?days=` + `Segmented`；低样本线断开 + 空心散点；主图右缘标签修正
- [x] 测试：10 条新 pytest；相关 54 passed；tsc/build/routes/doc_gate/mypy/ruff（全量 1862 passed；1 条 DB/job 用例受并行写库干扰、单跑全绿）
- [x] 重建 api/frontend 容器并真栈验证（见 Changelog）

## 验收

- 详情页对 9 个指标均 200；`days` 越界 422；未知指标 404
- 样本 <60 的指标（如近 30 日）不给分位；<5 不给统计；<10 不画分布
- 温度计只在收盘口径出现可点链接；盘中口径无链接
- 回填脚本可重复运行：默认只补 `yzt_avg_open_premium IS NULL` 的行（有 cohort 但当日溢价全缺的日子会保留 NULL，重复运行仍会重试，不覆写非 NULL 值）
