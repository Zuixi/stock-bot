# 单位口径统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复全站市值/成交额显示差 1e4/1e3 倍的单位错位——在映射层把 `total_mv`（万元）/`circ_mv`（万元）/`amount`（千元）统一换算为元，`formatCap` 保持元分档不动，全部消费点自动正确；顺带清理 P4 遗留的两处死代码。

**Architecture:** 单点换算在 `mapBackendStockEnriched`（前端唯一 enriched 映射层，StockHeader/FundamentalCards/StockTable/WatchlistTable 全部经它取数）；`volume`（手）不换（formatCap 万档=万手数值恰好正确，K 线图另有 fmtVolume）；公司对比表（后端已 ÷1e4 转亿直显）与头均市值（后端纯元）不经此层，无双重换算。

**Tech Stack:** 前端 TS/React 修改 + Playwright e2e，零后端改动、零新依赖。

**Spec:** P4 最终全分支审查 I1 处置建议（映射层统一换算 + 4 消费点同步），用户 2026-09-03 批准执行。

## Global Constraints

- **换算只做一处**：`mapBackendStockEnriched` 映射层；任何消费点（表格/卡片/头部）不得再做 ÷1e4/×1e3 类补偿。
- **volume（手）不动**；换手率/PE/PB 不动；`formatCap`（NumberText.tsx）不动。
- 排序语义不受影响（后端 SQL 排序在原始 total_mv/amount 上，单调变换不改变序）。
- no-new-deps；验证门 `cd frontend && npm run build` + docker 重建 + `npx playwright test` 全量绿（基线 17 个）。
- e2e 数值断言只用"形状"（如含"万亿"字样），不断言精确数。
- `frontend/tsconfig.tsbuildinfo` 提交前 `git checkout --`；工作区用户并行改动（backend/.coverage）不得纳入；main 分支提交。
- AGENTS.md：本任务提交需同步 Changelog/best-practices 沉淀（同 commit）。

---

### Task 1: 映射层单位换算 + 消费点同步 + 死代码清理

**Files:**
- Modify: `frontend/src/shared/api/stocks.ts`（mapBackendStockEnriched 三处换算；先核对 `mapBackendStock` 基础版无市值/额映射，若有则同步）
- Modify: `frontend/src/features/market/components/StockTable.tsx:120-128`（成交额列补 `unit="cap"`）
- Modify: `frontend/src/features/watchlist/components/WatchlistTable.tsx`（核对是否另有 成交额/成交量 列缺 unit，有则同步补）
- Modify: `frontend/src/shared/ui/kline/KlineChart.tsx`（M1：删 `DEFAULT_TAIL_BARS` 死 import；M2：删 MA 行冗余 `!isLoading && points.length > 0 &&` 守卫——外层三元已含同条件）
- Modify: `frontend/e2e/kline.spec.ts`（头部用例追加单位形状断言）
- Modify: `docs/Changelog.md`、`docs/references/best-practices.md`（AGENTS.md 沉淀，同 commit）

**Interfaces:**
- Consumes: 现有 `StockRecord`（marketCap/circulatingCap/turnover 语义从"原始万元/千元"升级为"元"——类型不变，仅取值语义）。
- Produces: 全站 `NumberText unit="cap"` 消费点显示正确量级（600519 总市值 ≈ "1.63万亿"、成交额 ≈ "26.34亿"）。

- [ ] **Step 1: 核对映射层现状**——Read `frontend/src/shared/api/stocks.ts` 的 `mapBackendStock` 与 `mapBackendStockEnriched`；确认基础版无市值/成交额映射（若有，按同规则换算并记录报告）。

- [ ] **Step 2: 三处换算**——`mapBackendStockEnriched` 改为（注意判空在前，避免 `null * 1e4 = 0`）：

```ts
        volume: item.volume ?? undefined, // 手：formatCap 万档恰为万手数值，不换算
        // TuShare 口径：amount 千元、total_mv/circ_mv 万元 → 统一换算为元（formatCap 按元分档）
        turnover: item.amount == null ? undefined : item.amount * 1e3,
        marketCap: item.total_mv == null ? undefined : item.total_mv * 1e4,
        circulatingCap: item.circ_mv == null ? undefined : item.circ_mv * 1e4,
```

- [ ] **Step 3: 消费点同步**——
  1. `StockTable.tsx` 成交额列（dataIndex "turnover" 的 render）改为 `<NumberText value={v} unit="cap" />`；
  2. Read `WatchlistTable.tsx` 全列定义，若有 成交额/市值 列缺 `unit="cap"` 则补（marketCap 已有则不动）；
  3. grep `grep -rn "turnover" frontend/src --include="*.tsx" | grep -v "unit\|api\|types"` 复核无其他裸渲染点。

- [ ] **Step 4: 死代码清理（P4 M1/M2）**——`KlineChart.tsx`：import 行删 `DEFAULT_TAIL_BARS`（保留 `aggregateDaily`/`KlineFreq`）；MA 行外层 `{!isLoading && points.length > 0 && (...)}` 简化为 `{points.length > 0 && (...)}`（该 JSX 已位于 `isLoading ? Spin : points.length > 0 ? 图表 : Empty` 的图表分支内）。

- [ ] **Step 5: e2e 断言追加**——kline.spec.ts 头部用例（"个股头部：8项指标网格…"）末尾追加：

```ts
  // 单位口径：600519 总市值 ≈1.6万亿(>1e12)、成交额为亿级——断言形状不断言精确数
  const mcapCell = page.locator(".ant-descriptions-item").filter({ hasText: "总市值" });
  await expect(mcapCell).toContainText(/万亿/);
  const turnoverCell = page.locator(".ant-descriptions-item").filter({ hasText: "成交额" });
  await expect(turnoverCell).toContainText(/亿/);
```

- [ ] **Step 6: 构建 + 重建 + 全量 e2e**

Run: `cd frontend && npm run build && cd .. && docker compose build frontend && docker compose up -d frontend && cd frontend && npx playwright test`
Expected: build 通过、e2e 全绿（基线 17 + 本用例强化）。若 "万亿" 断言失败，先实机 curl 600519 enriched 核对 total_mv 量级（茅台 ≈ 1.6e4 万元 → ×1e4 = 1.6e8…注意：1.63万亿 = 16300亿 = 1.63e12 元 = total_mv 1.63e8 万元 ×1e4 ✓ formatCap ≥1e12 走"万亿"档）再排查。

- [ ] **Step 7: 文档沉淀 + Commit**

best-practices 追加（先 grep 去重）：`多源单位在映射层一次性归一（元），消费端只做展示分档：后端字段单位（TuShare total_mv 万元/amount 千元）与前端 formatCap 分档基准（元）错位时，在唯一 mapper 处换算并注释口径，表格列新增时核对 unit props——多消费点各自补偿是单位错误的标准成因。`

Changelog 追加条目（格式照既有）：问题（差 1e4/1e3 倍根因）/修复（映射层单点换算+StockTable 成交额补 cap+死代码清理）/验证（e2e 万亿/亿形状断言）。

```bash
git checkout -- frontend/tsconfig.tsbuildinfo 2>/dev/null
git add frontend/src/shared/api/stocks.ts frontend/src/features/market/components/StockTable.tsx frontend/src/features/watchlist/components/WatchlistTable.tsx frontend/src/shared/ui/kline/KlineChart.tsx frontend/e2e/kline.spec.ts docs/Changelog.md docs/references/best-practices.md
git commit -m "fix(frontend): 市值/成交额单位口径统一 — 映射层万元/千元→元单点换算，全站量级修正+死代码清理"
```

---

## Self-Review 记录

- **覆盖**：审查 I1 的修法（映射层+消费点）✓；M1/M2 ✓；e2e 形状断言 ✓；AGENTS 文档 ✓。
- **一致性**：换算仅 mapper 一处、消费点只补 unit prop 不做算术 ✓；公司对比表/头均市值后端已换算不经此层，计划明示不动 ✓。
- **风险**：600519 市值跌破 1e12 元（万亿档下限）则 "万亿" 断言失效——概率极低（需跌至 ~40% 以下），报告留档；后端排序在原始列上不受影响 ✓。
