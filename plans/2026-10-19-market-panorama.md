---
status: completed
scope: A股全景从五张今日快照升级为日表 + /market/panorama + 市场结构脉搏 + 卡片变化层
touches: backend/app/models/market_data.py, backend/app/migrations/versions/, backend/app/repositories/panorama_repo.py, backend/app/services/market_panorama_service.py, backend/app/services/market_panorama_ingest_service.py, backend/app/scheduler/jobs.py, backend/app/scheduler/runner.py, backend/app/services/market_service.py, backend/app/services/heatmap_service.py, backend/app/services/market_data_service.py, backend/app/schemas/panorama.py, backend/app/api/v1/market.py, backend/scripts/backfill_market_panorama.py, backend/tests/test_market_panorama.py, frontend/src/shared/api/panorama.ts, frontend/src/shared/api/market.ts, frontend/src/features/market/components/MarketPanoramaPulse.*, frontend/src/features/market/components/DistributionChart.*, frontend/src/features/market/components/SwIndustryGrid.*, frontend/src/features/market/components/SectorHeatmap.tsx, frontend/src/features/market/hooks/useMarketPanorama.ts, frontend/src/pages/market/sections.tsx, frontend/e2e/marketPanorama.spec.ts, frontend/e2e/marketLayout.spec.ts, docs/design/market-panorama.md, docs/decisions/0023-market-panorama-daily-snapshots.md, docs/features.md, docs/Changelog.md
updated: 2026-10-19
next-action: -
---

# A股全景：市场结构脉搏（阶段 1/2）

## 目标

把 `/market` 「A股全景」从五张今日快照升级为**可回看、能解释变化**的市场结构面板：
广度/行业日表 → `/market/panorama` → 仪表带（罗盘 + KPI）→ 卡片变化层。

## 范围与阶段

### 阶段 1：数据与读面

- [x] 三张日表 + 迁移 `e3f1a7c9d2b4`（`market_breadth_daily` / `market_breadth_bucket_daily` / `market_industry_daily`）
- [x] 回补脚本 + `market_panorama_ingest_service` 共用 SQL（幂等；本地 244 天 / 2,684 桶 / 7,564 行业行）
- [x] 日终 job `market_panorama_daily`（Mon-Fri 17:25，纯本地派生；停摆后迟到补跑）
- [x] 广度口径与 `/market/distribution` 11 桶逐字对齐（上涨 ≥1%、平/微涨 0~1%、下跌 <0）
- [x] `GET /market/panorama`：广度序列/11 桶/31 行业/排名变化/结构四态/资金流/新鲜度
- [x] SQL 内聚合 Top3/HHI/离散度，不把 7,750 行行业明细读进 Python
- [x] 结构四态加中位涨跌幅守卫（2026-10-16 上涨 37.8% 但中位 +0.47% → 结构，不是普跌）
- [x] 资金流端点只认 `eod_full`，与脉搏同 `as_of`（10-13；不再把 10-15 的 poll 子集当末完整日）

### 阶段 2：前端与交互

- [x] `MarketPanoramaPulse`：广度×集中度罗盘（60 日轨迹 + 今日点）+ 4 个 5 日 sparkline KPI
- [x] 涨跌分布卡"较昨日/5 日上涨占比"（固定 22px，不因加载跳动）
- [x] 申万行业网格今日涨跌角标 + 方向发丝线 + 排名变化 tooltip
- [x] 热力图"今日/昨日"切换（`GET /market/sectors?date=`；非交易日/未来日 404）
- [x] `20D/60D/120D/250D` 写 URL `?pano=`；不轮询、`keepPreviousData`、无骨架闪动
- [x] 移动端 390：罗盘 + 2×2 KPI，无横向溢出

## 性能

| 范围 | 冷请求 | 响应 | 缓存命中 |
|---|---|---|---|
| 20D | 19ms | 17KB | 4ms |
| 60D | 21ms | 31KB | 4ms |
| 120D | 24ms | 54KB | 4ms |
| 250D | 31ms | 99KB | 6ms |

- 5 组范围查询；SQL 聚合；Redis 120s；不轮询。
- 前端 sparkline 用 SVG，不启 4 个 ECharts 实例。

## E2E

- `e2e/marketPanorama.spec.ts`：状态/罗盘/4 KPI/日表口径、切 250D 不跳、热力图昨日、390 无溢出。
- 阶段 1 后跑：`marketLayout` / `marketTrust` / `marketPolling` → 27 passed。
- 阶段 2 后跑：上述 + `marketDataFace` / `marketTrends` / `marketPanorama` → 48 passed；
  全量 `playwright test` **253 passed / 13 skipped**。
- `marketLayout` 的 `rowBottoms` 改为**文档坐标**：脉搏让页面变长，点击折叠会触发视口滚动，
  原先的 `getBoundingClientRect().bottom` 会把滚动误判成卡片变矮。

## 明确未做（下一阶段）

- 跨卡行业筛选（`?sw=` 过滤热力图/资金流/网格）；
- 热门板块"新进/连续/掉出"；
- 热力图 5D/20D 区间（需逐股窗口收益）；
- 热力图分类标签修正：卡内是 CSRC 行业，分区维度条已从"申万一级"改为"行业板块"。
