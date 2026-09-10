# 公开行情台首页（Phase 0–3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/` 从营销页改造成**免登录行情为主体**的行情台（脉搏/榜单/板块/资金/资讯），并补齐榜单的存储列+索引数据面与申万行业聚合。

**Architecture:** 前端在 landing 独立布局内新增 6 个行情区块，全部复用 `features/market/` 组件与新建的 `shared/ui/` 原语；后端为 `daily_quotes` 补 `pct_chg`/`pre_close` 存储列与复合索引、新增 `/market/rankings` 与 `/market/sw-industry/performance` 两个公开端点（走 `CacheClient` 300s 缓存）。Phase 4（日历）/Phase 5（资讯增强）依赖 Phase 0 spike 结论，**不在本计划内**，spike 后另立计划。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + Redis（`CacheClient`）；React 18 + TS + AntD 5 + TanStack Query + Playwright e2e（前端无单测设施，验证 = e2e + `tsc -b`）。

**Spec:** [plans/2026-09-11-public-market-homepage.md](./2026-09-11-public-market-homepage.md)（前置事实核实 §0、设计原则、Architectural decisions D1–D9、风险表 ①–⑩）。**执行本计划前必须先读 Spec 的 §0 与 D1–D9。**

## Global Constraints

- 工作目录：git worktree `F:/CodeSpaces/stock_bot_wt_landing`，新分支 `feature/public-market-home`，基于 `feature/landing-market`。
- A 股口径：**涨=红（`--up`）、跌=绿（`--down`）**；行情数字一律 `font-variant-numeric: tabular-nums` 右对齐；单位小字降级显示。
- **对外文案/PR/commit message 禁止出现第三方品牌词**（含设计参考对象名称）。
- 口径诚实：个股 T+1，所有榜单显示 `as_of`；缺失显示 `--`，**禁止回退 0.00%**。
- 不新增重型依赖（不加 vitest/jest；后端不加新包）。
- 后端测试：`cd backend && uv run pytest tests/<file> -v`（默认 addopts 已排除 e2e/bench）。
- 前端验证：`cd frontend && npx tsc -b`（类型门禁）+ `npm run test:e2e -- e2e/<file>`（需本地栈在线，`E2E_BASE_URL` 默认 `http://localhost:3000`）。
- 新增 Alembic 迁移后必须 `uv run alembic heads` 检查多 head（并联分支已知坑），多 head 则 `uv run alembic merge`。
- 每个 Task 收尾：`bash scripts/self_review.sh`；本计划触碰 `stock_service`/`market_service` 但**不属于 Tier 1 纯计算热点**，无需跑 bench。
- commit message 用 conventional commits（`feat:`/`fix:`/`test:`/`docs:`）。

---

# Phase 0 — 决策与核实（前置，全部完成后才进 Phase 1 的 Task 7+）

### Task 0.1: TuShare 日历/新闻接口 spike

**Files:**
- Create: `backend/scripts/spike_calendar_sources.py`
- Modify: `docs/design/data-source.md`（追加一节）

**Interfaces:**
- Produces: `docs/design/data-source.md` §「日历与新闻源实测（2026-09-11）」——Phase 4/5 计划的输入；表中每接口必须有 `可用 / 积分不足 / 不存在` 结论。

- [ ] **Step 1: 写 spike 脚本**（探针式，不走 TDD——目的是获取事实）

```python
"""One-off spike: probe TuShare calendar/news interfaces with the real token.

Usage: cd backend && uv run python scripts/spike_calendar_sources.py
Results are recorded by hand into docs/design/data-source.md.
NOTE: uses the private _query() on purpose — spike only, not production code.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.providers.tushare_client import get_tushare_client  # noqa: E402

PROBES: list[tuple[str, dict[str, str]]] = [
    ("disclosure_date", {"start_date": "20260901", "end_date": "20260930"}),  # 财报披露计划
    ("dividend", {"ts_code": "600519.SH"}),                                   # 分红送股
    ("new_share", {"start_date": "20260801", "end_date": "20260911"}),        # IPO 新股
    ("trade_cal", {"exchange": "SSE", "start_date": "20260101", "end_date": "20261231"}),
    ("eco_cal", {"start_date": "20260901", "end_date": "20260930"}),          # 宏观日历
    ("news", {"start_date": "20260910", "end_date": "20260911"}),
    ("major_news", {"start_date": "20260910 00:00:00", "end_date": "20260911 00:00:00"}),
    ("cctv_news", {"date": "20260910"}),
]


async def main() -> None:
    client = get_tushare_client()
    for api, kwargs in PROBES:
        try:
            df = await client._query(api, **kwargs)
            print(f"\n[OK ] {api}: rows={len(df)} cols={list(df.columns)}")
            if len(df):
                print(df.head(2).to_string())
        except Exception as exc:  # noqa: BLE001 — spike wants the raw error
            print(f"\n[ERR] {api}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 运行并记录**

Run: `cd backend && uv run python scripts/spike_calendar_sources.py`
Expected: 8 行 `[OK ]`/`[ERR ]` 输出。把每个接口的结论（含报错原文、样例列名）追加到 `docs/design/data-source.md` 新章节「日历与新闻源实测（2026-09-11）」。

- [ ] **Step 3: 不可用接口给替代方案**

在同一章节为每个不可用接口写明降级决策。最低要求：
`eco_cal` 不可用 → 首页日历只做 财报/分红/新股 三类；`news`/`major_news`/`cctv_news` 全不可用 → Phase 5 只做公告流。

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/spike_calendar_sources.py docs/design/data-source.md
git commit -m "chore(spike): probe tushare calendar/news interfaces, record availability"
```

---

### Task 0.2: 数据语义核实（北向 / 指数覆盖 / 分区状态）

**Files:**
- Modify: `plans/2026-09-11-public-market-homepage.md`（风险表 ②③④ 打勾销项）

**Interfaces:**
- Produces: 三个事实结论，直接决定 Task 11（北向模块形态）、Task 14（索引方式）、Spec 风险 ③ 的处置。

- [ ] **Step 1: 对着运行中的 postgres 执行三条 SQL**

```bash
docker exec -it postgres psql -U stockbot -d stockbot -c "
-- ② 北向近 30 天是否断流
SELECT trade_date, net_amount FROM northbound_daily ORDER BY trade_date DESC LIMIT 30;
-- ③ 指数覆盖：沪深300/上证50/北证50 在不在
SELECT ts_code, max(trade_date) AS last_date FROM index_dailies GROUP BY ts_code ORDER BY ts_code;
-- ④ daily_quotes 是否真的分区（0 = 未分区）
SELECT count(*) AS partitioned FROM pg_partitioned_table WHERE relid = 'daily_quotes'::regclass;"
```

（容器名/库名以 `.env` 与 `docker-compose.yml` 为准；`docker ps` 先确认。）

- [ ] **Step 2: 记录结论到 Spec 风险表**

- ② 北向 `net_amount` 近 30 天全 NULL/缺行 → Task 11 改做「北向成交总额」口径或砍掉该卡；
- ③ `index_dailies` 缺 `000300.SH`/`000016.SH`/`899050.BJ` → 在本 Task 顺手修：把这三只加进 `backend/app/services/market_data_service.py` 的 `GLOBAL_INDICES` 列表（与 `market_service._TARGET_INDICES` 对齐），并注明 daily job 17:30 会自动补齐；
- ④ `partitioned = 0` → Task 14 用普通复合索引；`= 1` → 在迁移里对分区父表 `CREATE INDEX`（PG 11+ 自动下推）。

- [ ] **Step 3: Commit**

```bash
git add plans/2026-09-11-public-market-homepage.md backend/app/services/market_data_service.py
git commit -m "docs(plan): record data-semantics findings; fix global index coverage gap"
```

---

### Task 0.3: noindex/SEO 决策记录

**Files:**
- Modify: `plans/2026-09-11-public-market-homepage.md`（§0.5 决策落字）

- [ ] **Step 1: 实测当前响应头**

```bash
curl -sI http://localhost:80/ | grep -i x-robots
curl -sI http://localhost:80/api/v1/market/distribution | grep -i x-robots
```
Expected: 均返回 `x-robots-tag: noindex, nofollow, nosnippet, noarchive`（来源 `gateway/dynamic/middlewares.yml` 的 `sec-headers`，frontend 与 api 路由都挂了它）。

- [ ] **Step 2: 写死决策**

在 Spec §0.5 追加（默认按此执行，除非用户另行拍板）：

> **决策（2026-09-11）**：维持整站 `noindex`。理由：(a) 公开再分发行情数据（TuShare/东财/巨潮）有条款约束，暂不希望被搜索引擎收录；(b) Vite SPA 本就难以索引，SEO 投入产出不成比例。**本计划删除一切"静态可索引落地路径"目标**；页脚数据来源署名（Task 13）仍执行——那是给用户看的，不是给爬虫。

- [ ] **Step 3: 顺手复核限流值**

`api-ratelimit: average 100 / burst 50 / 1s`（`gateway/dynamic/middlewares.yml`）中 burst < average，疑似笔误。本 Task 不改配置（网关行为变更属部署决策），仅在 Spec §0.5 记一行「待用户确认是否有意」。

- [ ] **Step 4: Commit**

```bash
git add plans/2026-09-11-public-market-homepage.md
git commit -m "docs(plan): record noindex decision and rate-limit review note"
```

---

# Phase 1 — 首页骨架（免登录行情为主体）

### Task 1.1: 涨跌色 WCAG AA 修复 + 设计令牌校验脚本

**Files:**
- Modify: `frontend/src/app/theme.ts`（`THEME_COLORS.light` 的 `up`/`down`）
- Modify: `frontend/src/app/styles/theme.css`（`:root[data-theme="light"]` 的 `--up`/`--down`）
- Create: `frontend/scripts/check-design-tokens.mjs`
- Modify: `frontend/package.json`（scripts 加 `"check:design"`）

**Interfaces:**
- Produces: `npm run check:design` —— 校验 (1) 亮/暗两套 `--up`/`--down` 对 `--bg-page` 对比度 ≥ 4.5，(2) `theme.ts` 与 `theme.css` 同名值一致。后续任何改色先跑它。

- [ ] **Step 1: 写校验脚本（先写测试，此时应 FAIL——旧色不达标）**

```js
// frontend/scripts/check-design-tokens.mjs
// 设计令牌门禁：涨跌色对比度 >= 4.5 (WCAG AA) + theme.ts/theme.css 一致性。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "src/app/styles/theme.css"), "utf8");
const ts = readFileSync(join(root, "src/app/theme.ts"), "utf8");

function blockOf(source, marker) {
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`marker not found: ${marker}`);
  return source.slice(start, source.indexOf("}", start));
}
function varOf(text, name) {
  const m = text.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`--${name} not found`);
  return m[1].toLowerCase();
}
function tsColor(mode, name) {
  const start = ts.indexOf(`${mode}: {`);
  const sec = ts.slice(start, ts.indexOf("},", start));
  const m = sec.match(new RegExp(`${name}:\\s*"(#[0-9a-fA-F]{6})"`));
  if (!m) throw new Error(`THEME_COLORS.${mode}.${name} not found`);
  return m[1].toLowerCase();
}
function lum(hex) {
  const c = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

let failed = 0;
for (const mode of ["light", "dark"]) {
  const b = blockOf(css, `[data-theme="${mode}"]`);
  const bg = varOf(b, "bg-page");
  for (const name of ["up", "down"]) {
    const hex = varOf(b, name);
    const ratio = contrast(hex, bg);
    const ok = ratio >= 4.5;
    console.log(`${ok ? "PASS" : "FAIL"} ${mode} --${name} ${hex} on ${bg}: ${ratio.toFixed(2)}:1`);
    if (!ok) failed += 1;
    if (tsColor(mode, name) !== hex) {
      console.log(`FAIL ${mode} ${name}: theme.ts(${tsColor(mode, name)}) != theme.css(${hex})`);
      failed += 1;
    }
  }
}
process.exit(failed ? 1 : 0);
```

package.json scripts 追加：`"check:design": "node scripts/check-design-tokens.mjs"`。

⚠️ 若 `theme.css` 的选择器不是 `[data-theme="light"]` 而是 `:root[data-theme="light"]`，调 `blockOf` 的 marker 使之匹配（grep 确认）。

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npm run check:design`
Expected: **FAIL**，输出 `light --up #f5222d ... 4.08:1` 与 `light --down #22c55e ... 2.28:1`。

- [ ] **Step 3: 改两处色值**

`theme.css`（`[data-theme="light"]` 块内）：`--up: #c62828;`（5.62:1）、`--down: #0a7d5f;`（5.11:1）。
`theme.ts`（`THEME_COLORS.light`）：`up: "#c62828"`、`down: "#0a7d5f"`。
**暗色不动**（`#f23645`/`#089981` 已达标）。`flat`/`hover` 不动。

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npm run check:design && npx tsc -b`
Expected: 4 行 PASS，exit 0。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/theme.ts frontend/src/app/styles/theme.css \
        frontend/scripts/check-design-tokens.mjs frontend/package.json
git commit -m "fix(ui): raise light-mode up/down colors to WCAG AA, add design-token gate"
```

---

### Task 1.2: 共享 UI 原语（DeltaText / DataRow / SectionCard）

**Files:**
- Create: `frontend/src/shared/ui/DeltaText.tsx` + `DeltaText.css`
- Create: `frontend/src/shared/ui/DataRow.tsx` + `DataRow.css`
- Create: `frontend/src/shared/ui/SectionCard.tsx` + `SectionCard.css`
- Create: `frontend/e2e/public-homepage.spec.ts`（骨架，Task 1.7 起逐步扩展）

**Interfaces:**
- Produces（后续所有行情区块消费，签名不得改动）:
  - `DeltaText({ value: number | null | undefined; suffix?: string })` — 涨跌数字，正 `+`/负 `−`/零无符号，缺失 `--`
  - `DataRow({ logo?, title, ticker?, value?, unit?, href?, delta? })` — 标准数据行
  - `SectionCard({ id?, title, moreHref?, moreText?, children })` — 三段式卡片壳

- [ ] **Step 1: 先建 e2e 骨架（此刻 FAIL——组件尚不存在，页面也未挂载）**

```ts
// frontend/e2e/public-homepage.spec.ts
import { expect, test } from "@playwright/test";

test.describe("公开行情台首页", () => {
  test("共享原语：DeltaText 缺失显示 -- 而非 0.00%", async ({ page }) => {
    await page.goto("/");
    // 组件经 Task 1.7+ 挂载后生效；这里先锁定行为契约
    const deltas = page.locator(".delta");
    await expect(deltas.first()).toBeVisible();
    const count = await deltas.count();
    for (let i = 0; i < count; i += 1) {
      await expect(deltas.nth(i)).not.toHaveText(/^0\.00%$|^0%$/);
    }
  });
});
```

Run: `cd frontend && npm run test:e2e -- e2e/public-homepage.spec.ts` → Expected: FAIL（无 `.delta` 元素）。

- [ ] **Step 2: 实现 DeltaText**

```tsx
// frontend/src/shared/ui/DeltaText.tsx
import "./DeltaText.css";

export interface DeltaTextProps {
  value: number | null | undefined;
  suffix?: string;
}

export function DeltaText({ value, suffix = "%" }: DeltaTextProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="delta delta--flat">--</span>;
  }
  const cls = value > 0 ? "delta--up" : value < 0 ? "delta--down" : "delta--flat";
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return (
    <span className={`delta ${cls}`}>
      {sign}
      {Math.abs(value).toFixed(2)}
      {suffix}
    </span>
  );
}
```

```css
/* frontend/src/shared/ui/DeltaText.css */
.delta { font-variant-numeric: tabular-nums; white-space: nowrap; }
.delta--up { color: var(--up); }
.delta--down { color: var(--down); }
.delta--flat { color: var(--text-secondary); }
```

- [ ] **Step 3: 实现 DataRow 与 SectionCard**

```tsx
// frontend/src/shared/ui/DataRow.tsx
import type { ReactNode } from "react";
import { DeltaText } from "./DeltaText";
import "./DataRow.css";

export interface DataRowProps {
  logo?: ReactNode;
  title: string;
  ticker?: string;
  value?: string;
  unit?: string;
  href?: string;
  delta?: number | null;
}

export function DataRow({ logo, title, ticker, value, unit, href, delta }: DataRowProps) {
  const body = (
    <>
      <span className="datarow__id">
        {logo}
        <span className="datarow__names">
          <span className="datarow__title">{title}</span>
          {ticker ? <span className="datarow__ticker">{ticker}</span> : null}
        </span>
      </span>
      <span className="datarow__val">
        {value !== undefined && (
          <span className="datarow__price">
            {value}
            {unit ? <span className="datarow__unit"> {unit}</span> : null}
          </span>
        )}
        <DeltaText value={delta} />
      </span>
    </>
  );
  return href ? (
    <a className="datarow" href={href}>{body}</a>
  ) : (
    <div className="datarow">{body}</div>
  );
}
```

```css
/* frontend/src/shared/ui/DataRow.css */
.datarow { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 0; border-bottom: 1px solid var(--border); text-decoration: none; color: inherit; }
.datarow:last-child { border-bottom: none; }
.datarow__id { display: flex; align-items: center; gap: 8px; min-width: 0; }
.datarow__names { display: flex; flex-direction: column; min-width: 0; }
.datarow__title { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.datarow__ticker { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; }
.datarow__val { display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
  font-variant-numeric: tabular-nums; }
.datarow__price { font-size: 14px; }
.datarow__unit { font-size: 11px; color: var(--text-secondary); }
```

```tsx
// frontend/src/shared/ui/SectionCard.tsx
import type { ReactNode } from "react";
import "./SectionCard.css";

export interface SectionCardProps {
  id?: string;
  title: string;
  moreHref?: string;
  moreText?: string;
  children: ReactNode;
}

export function SectionCard({ id, title, moreHref, moreText = "查看全部", children }: SectionCardProps) {
  return (
    <section className="section-card" id={id} data-testid={`section-${id ?? title}`}>
      <header className="section-card__head">
        <h3 className="section-card__title">{title}</h3>
        {moreHref ? (
          <a className="section-card__more" href={moreHref}>{moreText} ›</a>
        ) : null}
      </header>
      <div className="section-card__body">{children}</div>
    </section>
  );
}
```

```css
/* frontend/src/shared/ui/SectionCard.css */
.section-card { background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; }
.section-card__head { display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 12px; }
.section-card__title { margin: 0; font-size: 16px; font-weight: 600; }
.section-card__more { font-size: 13px; color: var(--accent); text-decoration: none; }
```

- [ ] **Step 4: 类型门禁**

Run: `cd frontend && npx tsc -b`
Expected: PASS（e2e 仍 FAIL 属预期，Task 1.7 挂载后转绿）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/ui/ frontend/e2e/public-homepage.spec.ts
git commit -m "feat(ui): add DeltaText/DataRow/SectionCard shared primitives"
```

---

### Task 1.3: enriched 排序路径加 Redis 缓存（修硬伤 ①，Phase 1 唯一后端改动）

**Files:**
- Modify: `backend/app/services/stock_service.py`（`list_stocks_enriched` 的 `if params.sort_by:` 分支，约 116–145 行）
- Test: `backend/tests/test_stock_sort_cache.py`

**Interfaces:**
- Consumes: 既有 `CacheClient.get/set(key, value, ttl=...)`、`StockListParams` / `PageParams`
- Produces: 排序路径缓存 key `stocks:enriched:sort:{exchange}:{sort_by}:{sort_order}:{offset}:{page_size}`，TTL 60s。函数签名不变。

- [ ] **Step 1: 写失败测试**

```python
"""Sort path of list_stocks_enriched must be Redis-cached (public homepage protection)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import stock_service


class RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.get_calls = 0

    async def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.store[key] = value


@pytest.mark.asyncio
async def test_sort_path_reads_and_writes_cache(monkeypatch) -> None:
    params = SimpleNamespace(sort_by="changePercent", sort_order="desc", exchange=None)
    page = SimpleNamespace(offset=0, page_size=10)

    stock = SimpleNamespace(id=1, symbol="600000", name="浦发银行", exchange="Shanghai_Stocks")
    enriched = SimpleNamespace(symbol="600000", latest_price=10.0, change_percent=1.5,
                               amount=100.0, total_mv=None, pe_ttm=None, turnover_rate=None,
                               volume=None)

    repo_calls = {"n": 0}

    async def _list_stocks(_db, _p, offset, limit):
        repo_calls["n"] += 1
        return [stock], 1

    async def _enrich(_db, _symbols):
        return [enriched]

    monkeypatch.setattr(stock_service.stock_repo, "list_stocks", _list_stocks)
    monkeypatch.setattr(
        "app.services.market_service.get_stocks_enriched_by_symbols", _enrich
    )

    cache = RecordingCache()
    db = AsyncMock()

    out1, total1 = await stock_service.list_stocks_enriched(db, cache, params, page)
    out2, total2 = await stock_service.list_stocks_enriched(db, cache, params, page)

    assert total1 == total2 == 1
    assert len(out1) == len(out2) == 1
    assert repo_calls["n"] == 1, "第二次同参调用必须命中缓存，不得再查库"
    assert cache.get_calls == 2
```

Run: `cd backend && uv run pytest tests/test_stock_sort_cache.py -v`
Expected: FAIL —— `repo_calls["n"] == 2`（现状排序路径无缓存，查库两次）。

- [ ] **Step 2: 实现缓存**

在 `list_stocks_enriched` 的 `if params.sort_by:` 分支**开头**插入：

```python
    if params.sort_by:
        # ── 公开首页防护：排序路径开销大（全市场 + LATERAL + Python sort），
        #    必须走 Redis 缓存；客户端 staleTime 不算防护。TTL 60s（日频数据）。──
        cache_key = (
            f"stocks:enriched:sort:{params.exchange or 'all'}:{params.sort_by}:"
            f"{params.sort_order}:{page_params.offset}:{page_params.page_size}"
        )
        cached = await cache.get(cache_key)
        if cached is not None:
            return (
                [StockEnrichedOut(**s) for s in cached["items"]],
                cached["total"],
            )
```

在该分支 `return items[...]` 之前，把返回改为落缓存：

```python
        page_items = items[page_params.offset : page_params.offset + page_params.page_size]
        await cache.set(
            cache_key,
            {"items": [i.model_dump(mode="json") for i in page_items], "total": total},
            ttl=60,
        )
        return page_items, total
```

⚠️ `params.exchange` 字段名以 `StockListParams` 实际定义为准（grep `class StockListParams`）；若缓存 key 已存在同款，对齐其命名风格。

- [ ] **Step 3: 跑测试通过**

Run: `cd backend && uv run pytest tests/test_stock_sort_cache.py tests/test_stocks.py -v`
Expected: PASS（含既有 `test_stocks.py` 无回归）。

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/stock_service.py backend/tests/test_stock_sort_cache.py
git commit -m "perf(stocks): redis-cache enriched sort path (public homepage traffic guard)"
```

---

### Task 1.4: 首页 IA 重排 —— 紧凑首屏 + 导航锚点 + 区块骨架

**Files:**
- Modify: `frontend/src/pages/landing/index.tsx`（区块序列改为 Spec D1）
- Modify: `frontend/src/pages/landing/sections/Hero.tsx` → 压缩为 `CompactHero`
- Modify: `frontend/src/pages/landing/sections/LandingNav.tsx`（加锚点：脉搏/榜单/板块/资金/资讯）
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Produces: 六个行情区块的挂载位（本 Task 为空骨架 `<SectionCard>` + 骨架屏），锚点 id：`pulse` / `rankings` / `sectors` / `money` / `calendar` / `news`；后续 Task 逐个填充。

- [ ] **Step 1: 追加失败 e2e**

```ts
test("行情为主：导航锚点齐全且首屏可见脉搏区块", async ({ page }) => {
  await page.goto("/");
  for (const id of ["pulse", "rankings", "sectors", "money", "news"]) {
    await expect(page.locator(`nav a[href="#${id}"]`)).toBeVisible();
  }
  await expect(page.getByTestId("section-pulse")).toBeVisible();
  await expect(page.getByTestId("section-rankings")).toBeVisible();
});
```

Run: `cd frontend && npm run test:e2e -- e2e/public-homepage.spec.ts` → Expected: FAIL。

- [ ] **Step 2: 改 `index.tsx` 区块序列**（按 Spec D1；本 Task 先挂空骨架）

```tsx
{/* 行情台主体 —— 全部公开 */}
<MarketPulse />                                    {/* Task 1.5 填充 */}
<SectionCard id="rankings" title="榜单" moreHref="/market" />        {/* Task 1.6 */}
<SectionCard id="sectors" title="行业与资金" moreHref="/market/category" /> {/* Task 1.7/3.2 */}
<SectionCard id="money" title="资金与情绪" />                          {/* Task 1.8 */}
<SectionCard id="calendar" title="日历" />                            {/* Phase 4 另立计划 */}
<SectionCard id="news" title="快讯" />                                {/* Task 1.9 */}
```

骨架屏占位（每个空 SectionCard 的 children 暂用 AntD `<Skeleton active paragraph={{ rows: 4 }} />`）。
营销区压缩：`ValueProps` 与 `ProductShowcase` 保留但移到行情台之后；`DataCoverage`/`IndustryGrid` 保留原位（尾部）。

- [ ] **Step 3: 压缩 Hero 为 CompactHero**

保留 H1 文案与 CTA（`CtaButton` 复用），移除大图/长信任行，整体高度目标 ≤ 240px；市场状态徽章（「A股 · 交易中/已收盘」，取 `MarketPulse` 同源判断，避免二次请求——先静态文案「A股行情」占位，Task 1.5 接真值）。

- [ ] **Step 4: LandingNav 加锚点**

```tsx
const ANCHORS = [
  { id: "pulse", label: "脉搏" },
  { id: "rankings", label: "榜单" },
  { id: "sectors", label: "板块" },
  { id: "money", label: "资金" },
  { id: "news", label: "快讯" },
] as const;
// 在 logo 与右侧用户区之间渲染：<a href="#pulse">脉搏</a> ...
```

- [ ] **Step 5: 跑 e2e + tsc 通过，Commit**

```bash
cd frontend && npx tsc -b && npm run test:e2e -- e2e/public-homepage.spec.ts
git add frontend/src/pages/landing frontend/e2e/public-homepage.spec.ts
git commit -m "feat(landing): market-first IA with compact hero and section anchors"
```

---

### Task 1.5: MarketPulse 升级（涨跌分布迷你柱）

**Files:**
- Modify: `frontend/src/pages/landing/sections/MarketPulse.tsx`
- Create: `frontend/src/features/market/components/DistributionBars.tsx`（落 `features/market`，与 `/market` 页共用）
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: 既有 `fetchDistribution`（`@/shared/api/market`，分桶口径与市场页同源——沿用 `43733a6` 的约定，`UP_RANGES`/`DOWN_RANGES` 常量已在组件内）
- Produces: `<DistributionBars buckets={...} />`——后续 `/market` 页 DistributionChart 之外的可复用轻量形态

- [ ] **Step 1: 追加失败 e2e**

```ts
test("脉搏区：指数条 + 涨跌分布柱 + 家数汇总", async ({ page }) => {
  await page.goto("/#pulse");
  await expect(page.getByTestId("section-pulse").locator(".index-ticker")).toHaveCount(8, { timeout: 15000 });
  await expect(page.locator(".distribution-bars")).toBeVisible();
});
```

- [ ] **Step 2: 实现 DistributionBars**（横向双向柱：上涨桶向右红色，下跌桶向左绿色）

```tsx
// frontend/src/features/market/components/DistributionBars.tsx
import "./DistributionBars.css";

export interface DistributionBucket {
  label: string;   // ">5%" / "跌停" 等
  count: number;
  direction: "up" | "down" | "flat";
}

export function DistributionBars({ buckets, max }: { buckets: DistributionBucket[]; max?: number }) {
  const peak = max ?? Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="distribution-bars" data-testid="distribution-bars">
      {buckets.map((b) => (
        <div className="distribution-bars__row" key={b.label}>
          <span className="distribution-bars__label">{b.label}</span>
          <span className="distribution-bars__track">
            <span
              className={`distribution-bars__fill distribution-bars__fill--${b.direction}`}
              style={{ width: `${Math.round((b.count / peak) * 100)}%` }}
            />
          </span>
          <span className="distribution-bars__count">{b.count}</span>
        </div>
      ))}
    </div>
  );
}
```

CSS（`DistributionBars.css`）：`__fill--up { background: var(--up); }`、`__fill--down { background: var(--down); }`、label/count 用 `--text-secondary`、count `tabular-nums` 右对齐固定宽。

- [ ] **Step 3: 接入 MarketPulse**

读组件内既有的 `fetchDistribution` 查询结果，把后端 11 桶映射为 `DistributionBucket[]`（方向：`涨停/…/+ 桶 → up`，`跌停/…/− 桶 → down`，`平 → flat`），渲染在「今日 A 股：上涨 N · 下跌 M」汇总行下方。指数 8 格容器加 `className="index-ticker"`（e2e 锚）。失败降级沿用现状文案。

- [ ] **Step 4: 跑 e2e + tsc，Commit**

```bash
git add frontend/src/pages/landing/sections/MarketPulse.tsx frontend/src/features/market/components/DistributionBars*
git commit -m "feat(landing): market pulse with distribution bars (same buckets as /market)"
```

---

### Task 1.6: RankingMatrix（临时路径 = 已缓存的 enriched）

**Files:**
- Create: `frontend/src/features/market/components/RankingMatrix.tsx`
- Modify: `frontend/src/shared/api/stocks.ts`（加 `fetchStockRanking`）
- Modify: `frontend/src/pages/landing/index.tsx`（替换 rankings 骨架）
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: Task 1.3 已缓存的 `GET /api/v1/exchanges/{exchange}/stocks?sort_by=&sort_order=&limit=`
- Produces: `fetchStockRanking(sortBy: "changePercent" | "turnover", order: "desc" | "asc", limit: number)`；Phase 2 Task 2.7 会切到 `/market/rankings` 并补换手率榜

- [ ] **Step 1: 追加失败 e2e**

```ts
test("榜单区：三个 Tab 默认涨幅榜有数据", async ({ page }) => {
  await page.goto("/#rankings");
  const section = page.getByTestId("section-rankings");
  await expect(section.getByRole("tab", { name: "涨幅榜" })).toBeVisible();
  await expect(section.getByRole("tab", { name: "跌幅榜" })).toBeVisible();
  await expect(section.getByRole("tab", { name: "成交额榜" })).toBeVisible();
  await expect(section.locator(".datarow").first()).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 2: api 封装**（`shared/api/stocks.ts`，复用既有列表端点；查询参数名以现有 `listStocks` 实现为准）

```ts
export interface RankingRow {
  symbol: string;
  name: string;
  latestPrice: number | null;
  changePercent: number | null;
  amount: number | null;   // 成交额（元）
}

export async function fetchStockRanking(
  sortBy: "changePercent" | "turnover",
  order: "desc" | "asc",
  limit = 10,
): Promise<RankingRow[]> {
  // 走既有 /exchanges/{exchange}/stocks 列表端点的 sort_by 能力（Task 1.3 已加服务端缓存）
  const data = await apiGet<{ items: RankingRow[] }>(
    `/api/v1/exchanges/all/stocks?sort_by=${sortBy}&sort_order=${order}&limit=${limit}&page_size=${limit}`,
  );
  return data.items;
}
```

⚠️ 先 grep 现有 `listStocks` 的真实路径与参数名并对齐（exchange 取值、分页参数名）；`/exchanges/all` 是否合法以 `stocks.py` 为准，不合法则循环三个交易所合并后截断。

- [ ] **Step 3: 实现 RankingMatrix**

```tsx
// frontend/src/features/market/components/RankingMatrix.tsx
import { Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DataRow } from "@/shared/ui/DataRow";
import { SectionCard } from "@/shared/ui/SectionCard";
import { fetchStockRanking } from "@/shared/api/stocks";

const TABS = [
  { key: "gainers", label: "涨幅榜", sortBy: "changePercent", order: "desc" },
  { key: "losers", label: "跌幅榜", sortBy: "changePercent", order: "asc" },
  { key: "amount", label: "成交额榜", sortBy: "turnover", order: "desc" },
] as const;

export function RankingMatrix() {
  const [active, setActive] = useState<(typeof TABS)[number]["key"]>("gainers");
  const tab = TABS.find((t) => t.key === active)!;
  const { data, isLoading } = useQuery({
    queryKey: ["home", "ranking", active],
    queryFn: () => fetchStockRanking(tab.sortBy, tab.order, 10),
    staleTime: 5 * 60_000,   // 服务端已有 60s 缓存，客户端放宽
  });
  return (
    <SectionCard id="rankings" title="今日榜单" moreHref="/market" moreText="进入行情页">
      <Tabs activeKey={active} onChange={(k) => setActive(k as typeof active)}
        items={TABS.map((t) => ({ key: t.key, label: t.label }))} />
      {isLoading
        ? <Skeleton active paragraph={{ rows: 6 }} />
        : (data ?? []).map((r) => (
            <DataRow key={r.symbol} title={r.name} ticker={r.symbol}
              value={r.latestPrice?.toFixed(2)} unit="元"
              delta={r.changePercent} href={`/stock/${r.symbol}`} />
          ))}
      {/* Phase 2 上线后此处追加：数据截至 {as_of}（Task 2.7） */}
    </SectionCard>
  );
}
```

（`useState`/`Skeleton` import 补齐；换手率榜在 Phase 2 之前**不上**——现有 API 不支持该排序。）

- [ ] **Step 4: 跑 e2e + tsc，Commit**

```bash
git add frontend/src/features/market/components/RankingMatrix.tsx frontend/src/shared/api/stocks.ts \
        frontend/src/pages/landing/index.tsx frontend/e2e/public-homepage.spec.ts
git commit -m "feat(landing): ranking matrix via cached enriched sort (temp path)"
```

---

### Task 1.7: SectorFlow（CSRC 临时口径 + 显式标注）

**Files:**
- Create: `frontend/src/features/market/components/SectorFlow.tsx`
- Modify: `frontend/src/pages/landing/index.tsx`
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: `fetchSectors`、`fetchSectorMoneyflow`（`@/shared/api/marketData`，均已有）
- Produces: SectorFlow 组件；Phase 3 Task 3.2 将左列切到 `/market/sw-industry/performance`（申万口径）

- [ ] **Step 1: 追加失败 e2e**

```ts
test("板块区：行业涨跌 + 资金流 + 口径标注", async ({ page }) => {
  await page.goto("/#sectors");
  const section = page.getByTestId("section-sectors");
  await expect(section).toBeVisible();
  await expect(section.getByText("行业口径：证监会")).toBeVisible();  // D6 临时口径必须可见
  await expect(section.getByText(/主力净流入|净流出/).first()).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 2: 实现组件**

布局两列（窄屏单列）：左列 `fetchSectors` 取前 8 行（按平均涨跌幅排序），每行 `DataRow` 变体（title=行业名、value=平均涨跌幅走 `DeltaText`）；右列 `fetchSectorMoneyflow(limit=8)`，红/绿双向条 + 净流入金额。两列头部固定一行小字：`行业口径：证监会（申万版即将上线）`。
React Query `staleTime: 60_000`；两列独立降级（沿用 MarketPulse 的静默降级文案模式）。

- [ ] **Step 3: 跑 e2e + tsc，Commit**

```bash
git commit -m "feat(landing): sector flow with CSRC basis label (SW version in Phase 3)"
```

---

### Task 1.8: MoneyAndSentiment（北向形态按 Task 0.2 结论）

**Files:**
- Create: `frontend/src/features/market/components/MoneySentiment.tsx`
- Modify: `frontend/src/pages/landing/index.tsx`
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: `fetchNorthbound`、`fetchMarketMoneyflow`（`@/shared/api/marketData`）

- [ ] **Step 1: 追加失败 e2e**

```ts
test("资金区：大盘资金流卡片可见", async ({ page }) => {
  await page.goto("/#money");
  await expect(page.getByTestId("section-money")).toBeVisible();
});
```

- [ ] **Step 2: 实现组件（按 Task 0.2 结论二选一）**

- **结论 = 北向净流入有数据**：左卡北向近 20 日净流入柱状（ECharts，颜色从 `useTheme().colors` 取——canvas 不吃 CSS var，沿用仓库约定）；右卡大盘资金流四档。
- **结论 = 断流**：北向卡改「沪深股通成交总额」口径或**整卡移除**（在 PR 描述记录原因），只留大盘资金流。
两卡独立降级；`staleTime: 60_000`。

- [ ] **Step 3: 跑 e2e + tsc，Commit**

```bash
git commit -m "feat(landing): money & sentiment cards (northbound per spike finding)"
```

---

### Task 1.9: MarketNews MVP（公告流，零新数据源）

**Files:**
- Create: `frontend/src/features/market/components/MarketNewsFeed.tsx`
- Modify: `frontend/src/pages/landing/index.tsx`
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: `fetchAnnouncements`（`@/shared/api/marketData`，已有；按 `category` 过滤 report/event）

- [ ] **Step 1: 追加失败 e2e**

```ts
test("快讯区：公告 Tab 可见且有条目", async ({ page }) => {
  await page.goto("/#news");
  const section = page.getByTestId("section-news");
  await expect(section.getByRole("tab", { name: /财报|公告/ }).first()).toBeVisible();
});
```

- [ ] **Step 2: 实现组件**

`SectionCard` 内 Tabs 两页（`report`=财报公告 / `event`=重大事项），每页 10 条时间倒序；行版式：左（标题，1 行截断 + 相对时间「2 小时前」）｜右（来源标签「巨潮」）。相对时间用本地工具函数（`Intl.RelativeTimeFormat("zh")`），复用/抽取自现有代码（grep `小时前` 先找有没有现成的）。分页 MVP 不做无限滚动，底部「查看全部 ›」链到 `/market`。

- [ ] **Step 3: 跑 e2e + tsc，Commit**

```bash
git commit -m "feat(landing): announcement-based news feed (no new source)"
```

---

### Task 1.10: 页脚数据来源署名 + e2e 收口（零 401 / 降级）

**Files:**
- Modify: `frontend/src/pages/landing/sections/LandingFooter.tsx`
- Test: `frontend/e2e/public-homepage.spec.ts`（追加两条关键收口用例）

- [ ] **Step 1: 追加失败 e2e**

```ts
test("未登录全链路零 401/403", async ({ page }) => {
  const denied: number[] = [];
  page.on("response", (r) => {
    if (r.status() === 401 || r.status() === 403) denied.push(r.status());
  });
  await page.context().clearCookies();
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(denied).toEqual([]);
});

test("单源故障不白屏（sectors 挂掉）", async ({ page }) => {
  await page.route("**/api/v1/market/sectors*", (r) => r.abort());
  await page.goto("/");
  await expect(page.getByTestId("section-sectors")).toBeVisible();
  await expect(page.getByTestId("section-rankings")).toBeVisible();
});

test("页脚含数据来源署名", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("数据来源")).toBeVisible();
});
```

- [ ] **Step 2: LandingFooter 加一行**

```tsx
<p className="landing-footer__sources">
  数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所
</p>
```

- [ ] **Step 3: 跑全部 e2e（含 landing.spec.ts 回归）+ Commit**

```bash
cd frontend && npx tsc -b && npm run test:e2e
git add frontend/src/pages/landing/sections/LandingFooter.tsx frontend/e2e/public-homepage.spec.ts
git commit -m "feat(landing): data-source attribution; e2e gate for anon access and degradation"
```

---

# Phase 2 — 榜单数据面（pct_chg 落库 + 索引 + rankings API）

### Task 2.1: Alembic 迁移（列 + 索引）

**Files:**
- Create: `backend/app/migrations/versions/<rev>_add_pct_chg_to_daily_quotes.py`

**Interfaces:**
- Produces: `daily_quotes.pre_close Numeric(12,4) NULL`、`daily_quotes.pct_chg Numeric(8,4) NULL`、索引 `idx_daily_quotes_date_pct(trade_date, pct_chg)`、`idx_daily_quotes_date_amount(trade_date, amount)`、`idx_daily_basic_date_turnover(trade_date, turnover_rate)`

- [ ] **Step 1: 确认分区状态（Task 0.2 已查，此处复核）**

```bash
docker exec -it postgres psql -U stockbot -d stockbot -c \
  "SELECT count(*) FROM pg_partitioned_table WHERE relid='daily_quotes'::regclass;"
```

- [ ] **Step 2: 生成并编写迁移**

```bash
cd backend && uv run alembic revision -m "add pct_chg to daily_quotes and ranking indexes"
```

```python
def upgrade() -> None:
    op.add_column("daily_quotes", sa.Column("pre_close", sa.Numeric(12, 4), nullable=True))
    op.add_column("daily_quotes", sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True))
    op.create_index("idx_daily_quotes_date_pct", "daily_quotes", ["trade_date", "pct_chg"])
    op.create_index("idx_daily_quotes_date_amount", "daily_quotes", ["trade_date", "amount"])
    op.create_index(
        "idx_daily_basic_date_turnover", "daily_basic_indicators", ["trade_date", "turnover_rate"]
    )


def downgrade() -> None:
    op.drop_index("idx_daily_basic_date_turnover", table_name="daily_basic_indicators")
    op.drop_index("idx_daily_quotes_date_amount", table_name="daily_quotes")
    op.drop_index("idx_daily_quotes_date_pct", table_name="daily_quotes")
    op.drop_column("daily_quotes", "pct_chg")
    op.drop_column("daily_quotes", "pre_close")
```

`Revision ID` / `Revises` 由 alembic 生成时自动填；**手动核对 `Revises` 指向当前 head**。

- [ ] **Step 3: 执行迁移并查多 head**

```bash
cd backend && uv run alembic upgrade head && uv run alembic heads
```
Expected: 迁移成功；`alembic heads` 只有 1 行。若多行：`uv run alembic merge -m "merge heads before pct_chg" <h1> <h2>` 再 `upgrade head`。

- [ ] **Step 4: Commit**

```bash
git add backend/app/migrations/versions/
git commit -m "feat(db): add pct_chg/pre_close columns and ranking indexes"
```

---

### Task 2.2: 模型 / ingest 映射 / upsert 补字段（TDD）

**Files:**
- Modify: `backend/app/models/quote.py`（加两列）
- Modify: `backend/app/services/tushare_ingest.py`（日线映射处，约 326–339 行）
- Modify: `backend/app/repositories/quote_repo.py:79`（`upsert_quotes` 的 values 与 `set_`）
- Test: `backend/tests/test_daily_ingest_pct_chg.py`

**Interfaces:**
- Produces: 入库后 `DailyQuote.pct_chg` / `.pre_close` 有值；`upsert_quotes` 冲突更新时同步覆盖这两列

- [ ] **Step 1: 写失败测试**

```python
"""pct_chg/pre_close must survive the ingest mapping (TuShare daily returns them natively)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.services.tushare_ingest import TuShareIngestService


@pytest.mark.asyncio
async def test_daily_ingest_maps_pct_chg_and_pre_close(monkeypatch) -> None:
    captured: list[SimpleNamespace] = []

    async def _upsert(_db, quotes):
        captured.extend(quotes)
        return len(quotes)

    async def _stock_id_map(_db):
        return {"600000.SH": 1}

    monkeypatch.setattr("app.repositories.quote_repo.upsert_quotes", _upsert)
    monkeypatch.setattr(
        TuShareIngestService, "_build_stock_id_map", _stock_id_map, raising=True
    )

    df = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260910", "open": 10.0, "high": 11.0,
         "low": 9.9, "close": 10.5, "pre_close": 10.0, "change": 0.5, "pct_chg": 5.0,
         "vol": 1000.0, "amount": 10500.0},
    ])

    service = TuShareIngestService(client=AsyncMock(), data_saver=AsyncMock())
    method = getattr(service, "ingest_daily")  # 以实际方法名为准（见 Step 2 注）
    await method(AsyncMock(), trade_date="20260910", df=df)

    assert captured[0].pct_chg == 5.0
    assert captured[0].pre_close == 10.0
```

⚠️ Step 2 第一步先 `grep -n "def ingest" backend/app/services/tushare_ingest.py` 确认日线入库方法的真实名字与签名（含是否接收现成 df），据此修正测试调用——**测试意图不变**：喂含 `pct_chg`/`pre_close` 的行，断言落库对象带这两个值。

- [ ] **Step 2: 实现三处修改**

模型（`models/quote.py`，`close` 列之后）：

```python
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 4))
```

ingest 映射（`tushare_ingest.py` 日线构造处）：

```python
                    close=close_val,
                    pre_close=_to_builtin(row.get("pre_close")),
                    pct_chg=_to_builtin(row.get("pct_chg")),
```

`upsert_quotes`（`quote_repo.py`）：values 字典加 `"pre_close": q.pre_close, "pct_chg": q.pct_chg`；`on_conflict_do_update` 的 `set_` 加 `"pre_close": insert(DailyQuote).excluded.pre_close, "pct_chg": insert(DailyQuote).excluded.pct_chg`。
（第二处 `DailyQuote(` 构造约在 `tushare_ingest.py:476`，同样补两字段。）

- [ ] **Step 3: 跑测试与回归**

Run: `cd backend && uv run pytest tests/test_daily_ingest_pct_chg.py tests/test_tushare_ingest_backfill.py -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(ingest): persist native pct_chg/pre_close from tushare daily"
```

---

### Task 2.3: 历史回填脚本（权威源重拉，禁止 LAG 现算）

**Files:**
- Create: `backend/scripts/backfill_pct_chg.py`
- Modify: `backend/app/repositories/quote_repo.py`（加 `update_pct_chg_for_date`）

**Interfaces:**
- Produces: `uv run python scripts/backfill_pct_chg.py --years 3` —— 幂等回填；`update_pct_chg_for_date(db, trade_date, rows: list[tuple[int, float | None, float | None]]) -> int`

- [ ] **Step 1: repo 函数**

```python
async def update_pct_chg_for_date(
    db: AsyncSession,
    trade_date: date,
    rows: list[tuple[int, float | None, float | None]],  # (stock_id, pre_close, pct_chg)
) -> int:
    """Bulk-update pre_close/pct_chg for one trade_date. Authoritative values only
    (re-fetched from TuShare) — never derive with LAG(close): ex-dividend days need
    the adjusted previous close, which TuShare's native pct_chg already accounts for."""
    if not rows:
        return 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    values = [
        {"stock_id": sid, "trade_date": trade_date, "pre_close": pc, "pct_chg": pct}
        for sid, pc, pct in rows
    ]
    stmt = (
        pg_insert(DailyQuote)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_daily_quotes_stock_date",
            set_={
                "pre_close": pg_insert(DailyQuote).excluded.pre_close,
                "pct_chg": pg_insert(DailyQuote).excluded.pct_chg,
            },
        )
    )
    await db.execute(stmt)
    return len(values)
```

（按批 500 提交，沿用 `tushare_ingest` 的批次风格；db session 走 `async_session_factory`。）

- [ ] **Step 2: 回填脚本**

```python
"""Backfill daily_quotes.pct_chg/pre_close from TuShare daily (authoritative).

Usage: cd backend && uv run python scripts/backfill_pct_chg.py --years 3
Cost note: ~250 trade dates/year of fetch_daily calls — plan for TuShare quota.
"""
import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import text

from app.core.database import async_session_factory
from app.core.providers.tushare_client import get_tushare_client
from app.repositories import quote_repo
from app.services.tushare_ingest import TuShareIngestService


async def main(years: int) -> None:
    client = get_tushare_client()
    service = TuShareIngestService(client=client, data_saver=None)
    start = (date.today() - timedelta(days=365 * years)).strftime("%Y%m%d")

    async with async_session_factory() as db:
        dates = (await db.execute(text(
            "SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date >= :s "
            "ORDER BY trade_date DESC"
        ), {"s": start})).scalars().all()
        id_map = await service._build_stock_id_map(db)

    for d in dates:
        ymd = d.strftime("%Y%m%d")
        df = await client.fetch_daily(trade_date=ymd)
        rows = []
        for r in df.to_dict("records"):
            sid = id_map.get(str(r.get("ts_code", "")))
            if sid is None or r.get("pct_chg") is None:
                continue
            rows.append((sid, r.get("pre_close"), r.get("pct_chg")))
        async with async_session_factory() as db:
            n = await quote_repo.update_pct_chg_for_date(db, d, rows)
            await db.commit()
        print(f"{ymd}: updated={n}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=3)
    asyncio.run(main(p.parse_args().years))
```

⚠️ `data_saver=None` 是否可行以构造函数为准（不可行则传 `AsyncMock()` 风格的空对象）；`_build_stock_id_map` 若是私有且依赖实例状态，保持同 Task 2.2 的调用方式。

- [ ] **Step 3: 实机运行 + 抽样一致性验证**

```bash
cd backend && uv run python scripts/backfill_pct_chg.py --years 3
# 抽样 20 行：库内 pct_chg 对 TuShare 原值
docker exec -it postgres psql -U stockbot -d stockbot -c \
  "SELECT count(*) FROM daily_quotes WHERE trade_date = (SELECT max(trade_date) FROM daily_quotes) AND pct_chg IS NULL;"
```
Expected: 回填日志逐日 `updated>0`；最新交易日 `pct_chg IS NULL` 占比 ≈ 停牌/新股比例（应 < 1%）。另抽 20 个 symbol 当日值与 TuShare 返回逐一对拍（脚本再跑一次同日，幂等不重复）。

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/backfill_pct_chg.py backend/app/repositories/quote_repo.py
git commit -m "feat(backfill): authoritative pct_chg backfill from tushare daily"
```

---

### Task 2.4: get_latest_trade_date（TDD）

**Files:**
- Modify: `backend/app/services/market_service.py`
- Test: `backend/tests/test_latest_trade_date.py`

**Interfaces:**
- Produces:
  - `get_latest_trade_date(db: AsyncSession, cache: CacheClient | None = None) -> date`（Redis key `market:latest_trade_date`，TTL 300s）
  - `last_weekday(d: date) -> date`（纯函数）——`is_latest_trading_day` 的 Phase 2 启发式，**TODO: Phase 4 接 trade_calendar 后替换实现、接口不变**

- [ ] **Step 1: 写失败测试（纯函数部分先行）**

```python
from datetime import date

from app.services.market_service import last_weekday


def test_last_weekday_friday_stays() -> None:
    assert last_weekday(date(2026, 9, 11)) == date(2026, 9, 11)  # 周五


def test_last_weekday_weekend_falls_back_to_friday() -> None:
    assert last_weekday(date(2026, 9, 12)) == date(2026, 9, 11)  # 周六
    assert last_weekday(date(2026, 9, 13)) == date(2026, 9, 11)  # 周日
```

Run: `cd backend && uv run pytest tests/test_latest_trade_date.py -v` → FAIL（函数不存在）。

- [ ] **Step 2: 实现**

```python
def last_weekday(d: date) -> date:
    """Most recent weekday <= d. Phase-2 heuristic ONLY — holidays are NOT handled;
    Phase 4 swaps this for a trade_calendar lookup (same signature)."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


async def get_latest_trade_date(db: AsyncSession, cache: CacheClient | None = None) -> date:
    cache_key = "market:latest_trade_date"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cast(date, cached)  # CacheClient JSON 序列化 → 可能回 str，须 date.fromisoformat 兜底
    async with db.begin():
        result = await db.execute(select(func.max(DailyQuote.trade_date)))
        as_of = result.scalar_one()
    if as_of is None:
        raise ValueError("daily_quotes is empty — run ingest first")
    if cache:
        await cache.set(cache_key, as_of.isoformat(), 300)
    return as_of
```

⚠️ `CacheClient` 是 JSON 序列化（`date` 不可 JSON 化）——缓存里存 `isoformat` 字符串，读出后 `date.fromisoformat(cached)`。注意 `cast` 处替换为显式转换。

- [ ] **Step 3: 跑测试 + Commit**

```bash
cd backend && uv run pytest tests/test_latest_trade_date.py -v
git add backend/app/services/market_service.py backend/tests/test_latest_trade_date.py
git commit -m "feat(market): get_latest_trade_date + weekday heuristic (trade_calendar TODO)"
```

---

### Task 2.5: rankings service + schema + API（TDD）

**Files:**
- Create: `backend/app/schemas/ranking.py`
- Modify: `backend/app/services/market_service.py`（`get_rankings`）
- Modify: `backend/app/api/v1/market.py`（`GET /rankings`）
- Test: `backend/tests/test_rankings.py`

**Interfaces:**
- Produces: `GET /api/v1/market/rankings?type=gainers|losers|amount|turnover_rate|volume&limit=20`
  → `{"as_of": "2026-09-10", "is_latest_trading_day": true, "type": "...", "items": [...]}`；
  item = `{symbol, name, exchange, close, pct_chg, amount, volume, turnover_rate, total_mv}`（量纲：amount 元、total_mv 万元——与 `StockEnrichedOut` 一致，前端沿用现有 mapper）

- [ ] **Step 1: 写失败测试（类型白名单 + 降级）**

```python
import pytest

from app.schemas.ranking import RankingType, RankingResponseOut
from app.services.market_service import _RANKING_ORDER


def test_ranking_type_whitelist() -> None:
    assert set(_RANKING_ORDER) == {"gainers", "losers", "amount", "turnover_rate", "volume"}
    assert _RANKING_ORDER["gainers"] == ("pct_chg", "DESC")


def test_ranking_response_schema_roundtrip() -> None:
    out = RankingResponseOut(
        as_of="2026-09-10", is_latest_trading_day=True, type="gainers", items=[]
    )
    assert out.model_dump(mode="json")["as_of"] == "2026-09-10"
```

Run: `cd backend && uv run pytest tests/test_rankings.py -v` → FAIL。

- [ ] **Step 2: schema**

```python
# backend/app/schemas/ranking.py
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

RankingType = Literal["gainers", "losers", "amount", "turnover_rate", "volume"]


class RankingItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    exchange: str | None = None
    close: float | None = None
    pct_chg: float | None = None
    amount: float | None = None      # 元
    volume: float | None = None      # 股
    turnover_rate: float | None = None
    total_mv: float | None = None    # 万元


class RankingResponseOut(BaseModel):
    as_of: date
    is_latest_trading_day: bool
    type: RankingType
    items: list[RankingItemOut]
```

- [ ] **Step 3: service（两阶段 SQL——排序走 CTE 索引，补字段不影响有序性）**

```python
# market_service.py 追加
_RANKING_ORDER: dict[str, tuple[str, str]] = {
    "gainers": ("pct_chg", "DESC"),
    "losers": ("pct_chg", "ASC"),
    "amount": ("amount", "DESC"),
    "volume": ("volume", "DESC"),
    "turnover_rate": ("turnover_rate", "DESC"),
}

_QUOTE_RANK_SQL = text("""
WITH topn AS (
    SELECT q.stock_id, q.close, q.pct_chg, q.amount, q.volume
    FROM daily_quotes q
    WHERE q.trade_date = :as_of AND q.pct_chg IS NOT NULL
      AND (:rank_col_expr)
    ORDER BY :order_col :order_dir
    LIMIT :limit
)
SELECT s.symbol, s.name, s.exchange, t.close, t.pct_chg, t.amount, t.volume,
       b.turnover_rate, b.total_mv
FROM topn t
JOIN stocks s ON s.id = t.stock_id
LEFT JOIN daily_basic_indicators b
       ON b.stock_id = t.stock_id AND b.trade_date = :as_of
""")

_TURNOVER_RANK_SQL = text("""
WITH topn AS (
    SELECT b.stock_id, b.turnover_rate, b.total_mv
    FROM daily_basic_indicators b
    WHERE b.trade_date = :as_of AND b.turnover_rate IS NOT NULL
    ORDER BY b.turnover_rate DESC
    LIMIT :limit
)
SELECT s.symbol, s.name, s.exchange, q.close, q.pct_chg, q.amount, q.volume,
       t.turnover_rate, t.total_mv
FROM topn t
JOIN stocks s ON s.id = t.stock_id
LEFT JOIN daily_quotes q ON q.stock_id = t.stock_id AND q.trade_date = :as_of
""")
```

⚠️ SQLAlchemy `text()` 里 `:order_col`/`:order_dir` **不能作参数绑定**（标识符/方向不是值）。实现时用 **`f-string` 拼接白名单字典的值**（`_RANKING_ORDER` 的键值全在本文件硬编码，无注入面），并删掉 `:rank_col_expr` 占位。SQL 模板写成按 rank_type 二选一 + order 列/方向插值的两个最终字符串。`stocks` 表名先 `grep __tablename__` 核对。

service 函数骨架：

```python
async def get_rankings(
    db: AsyncSession,
    cache: CacheClient,
    rank_type: str,
    limit: int,
) -> RankingResponseOut:
    if rank_type not in _RANKING_ORDER:
        raise ValueError(f"unknown ranking type: {rank_type}")
    cache_key = f"market:rankings:{rank_type}:{limit}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return RankingResponseOut.model_validate(cached)

    as_of = await get_latest_trade_date(db, cache)
    sql = _turnover_sql if rank_type == "turnover_rate" else _quote_sql(rank_type)
    rows = (await db.execute(sql, {"as_of": as_of, "limit": limit})).mappings().all()
    out = RankingResponseOut(
        as_of=as_of,
        is_latest_trading_day=(as_of >= last_weekday(date.today())),
        type=rank_type,  # type: ignore[arg-type]
        items=[RankingItemOut(**dict(r)) for r in rows],
    )
    await cache.set(cache_key, out.model_dump(mode="json"), _MARKET_CACHE_TTL)
    return out
```

- [ ] **Step 4: API 端点**（`market.py`，风格对齐既有）

```python
@router.get("/rankings", response_model=RankingResponseOut)
async def get_rankings(
    cache: CacheDep,
    db: DbDep,
    type: RankingTypeLit = Query("gainers"),
    limit: int = Query(20, ge=1, le=100),
) -> RankingResponseOut:
    """公开榜单。口径 T+1，见响应 as_of。"""
    return await market_service.get_rankings(db, cache, type, limit)
```

（`RankingTypeLit` 即 `RankingType`；`db: DbDep` 的注入名以本文件既有端点为准——本文件多数端点只注入 `cache`，service 内部自开 session，此处保持一致即可。）

- [ ] **Step 5: 跑测试 + 实机 curl**

```bash
cd backend && uv run pytest tests/test_rankings.py -v
curl -s "http://localhost:80/api/v1/market/rankings?type=gainers&limit=5" | head -c 400
```
Expected: 单测 PASS；curl 返回带 `as_of` 的 JSON（隐身窗口即可——匿名可达）。

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(market): public /market/rankings endpoint on indexed pct_chg path"
```

---

### Task 2.6: EXPLAIN 索引断言测试（替代未实施的 Tier 2）

**Files:**
- Test: `backend/tests/test_rankings_index.py`（`@pytest.mark.e2e`——需真实 DB，默认排除、CI/实机显式跑）

**Interfaces:**
- Produces: 防回归锁——gainers 查询计划在 `daily_quotes` 上必须是 `Index Scan`（不是 `Seq Scan` + Sort）

- [ ] **Step 1: 写测试**

```python
"""Rankings must ride the (trade_date, pct_chg) index — guards Task 2.1's design."""
import pytest
from sqlalchemy import text

from app.core.database import async_session_factory

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_gainers_query_plan_uses_index_scan() -> None:
    sql = (
        "EXPLAIN (FORMAT JSON) SELECT stock_id FROM daily_quotes "
        "WHERE trade_date = (SELECT max(trade_date) FROM daily_quotes) "
        "AND pct_chg IS NOT NULL ORDER BY pct_chg DESC LIMIT 20"
    )
    async with async_session_factory() as db:
        plan = (await db.execute(text(sql))).scalar_one()
    node_types: list[str] = []

    def walk(node: dict) -> None:
        node_types.append(node["Node Type"])
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    on_quotes = "Index Scan" in node_types or "Index Only Scan" in node_types
    assert on_quotes, f"expected index scan on daily_quotes, got: {node_types}"
```

- [ ] **Step 2: 实机验证**

Run: `cd backend && uv run pytest tests/test_rankings_index.py -v -m e2e`
Expected: PASS。若 FAIL（Seq Scan）：检查 Task 2.1 索引是否真的建在最新分区/统计信息过期（`ANALYZE daily_quotes;` 后重试）。

- [ ] **Step 3: Commit**

```bash
git commit -m "test(rankings): lock index-scan query plan (Tier2-equivalent guard)"
```

---

### Task 2.7: 前端切 rankings 端点 + 换手率榜 + as_of

**Files:**
- Modify: `frontend/src/shared/api/market.ts`（加 `fetchRankings`）
- Modify: `frontend/src/features/market/components/RankingMatrix.tsx`（四 Tab + as_of 行）
- Test: `frontend/e2e/public-homepage.spec.ts`（追加）

**Interfaces:**
- Consumes: Task 2.5 的 `GET /market/rankings`
- Produces: `fetchRankings(type: RankingType, limit = 10): Promise<RankingResponse>`；`RankingType = "gainers" | "losers" | "amount" | "turnover_rate" | "volume"`

- [ ] **Step 1: 追加失败 e2e**

```ts
test("榜单区：换手率榜可见且显示数据截至日期", async ({ page }) => {
  const section = page.getByTestId("section-rankings");
  await page.goto("/#rankings");
  await expect(section.getByRole("tab", { name: "换手率榜" })).toBeVisible();
  await expect(section.getByText(/数据截至/)).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 2: api 封装 + 组件改造**

```ts
// shared/api/market.ts
export type RankingType = "gainers" | "losers" | "amount" | "turnover_rate" | "volume";
export interface RankingItem {
  symbol: string; name: string; exchange?: string | null;
  close?: number | null; pct_chg?: number | null; amount?: number | null;
  volume?: number | null; turnover_rate?: number | null; total_mv?: number | null;
}
export interface RankingResponse {
  as_of: string; is_latest_trading_day: boolean; type: RankingType; items: RankingItem[];
}

export async function fetchRankings(type: RankingType, limit = 10): Promise<RankingResponse> {
  return apiGet<RankingResponse>(`/api/v1/market/rankings?type=${type}&limit=${limit}`);
}
```

RankingMatrix：TABS 增至四项（`{ key: "turnover_rate", label: "换手率榜" }`），数据源换成 `fetchRankings`，头部加一行 `<span className="ranking__asof">数据截至 {formatCnDate(as_of)}</span>`（`formatCnDate` 输出 `9月10日` 样式；本地工具函数，grep 已有日期格式化先复用）。成交额榜行 `value` 显示 `formatCap(amount)`（亿/万亿，grep `formatCap` 复用现有实现，注意单位口径 元→亿）。

- [ ] **Step 3: 跑 e2e + tsc，删除临时路径并 Commit**

确认 `fetchStockRanking`（Task 1.6 临时）已无引用后删除。

```bash
git commit -m "feat(landing): rankings on dedicated endpoint, add turnover tab and as_of"
```

---

# Phase 3 — 申万行业行情聚合（D6）

### Task 3.1: `GET /market/sw-industry/performance`（TDD）

**Files:**
- Modify: `backend/app/services/market_service.py`（`get_sw_industry_performance`）
- Modify: `backend/app/api/v1/market.py`
- Test: `backend/tests/test_sw_performance.py`

**Interfaces:**
- Produces: `GET /api/v1/market/sw-industry/performance?limit=31` →
  `{ "as_of": date, "items": [{ code, name, member_count, avg_pct_chg, total_amount, up_count, down_count }] }`（L1 一级行业，按 `avg_pct_chg` 降序；`total_amount` 元）

- [ ] **Step 0: 先核实 join 键**（写码前必做）

```sql
SELECT count(*) FROM sw_industry_members m JOIN stocks s ON s.symbol = m.symbol;
SELECT count(*) FROM sw_industry_members;
```
两数相近（>90%）→ 用 `symbol`；悬殊 → 改用 `m.stock_code` 与 `stocks` 的 ts_code 形态对齐（grep `__tablename__` + 看 `stock_code` 样例后调整 SQL）。

- [ ] **Step 1: 写失败测试（白名单与聚合口径）**

```python
def test_sw_performance_sql_aggregates_l1() -> None:
    sql = market_service._SW_PERF_SQL
    assert "sw_industry_members" in sql and "parent_code" in sql  # 两跳上卷 L3→L1
    assert "avg" in sql and "sum" in sql
```

- [ ] **Step 2: 实现（SQL + service + API）**

```python
_SW_PERF_SQL = text("""
WITH l1_members AS (
    SELECT c1.industry_code AS code, c1.industry_name AS name, m.symbol
    FROM sw_industry_members m
    JOIN sw_industry_classes c3 ON c3.industry_code = m.industry_code AND c3.level = 3
    JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
    JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
)
SELECT lm.code, lm.name,
       count(q.stock_id) AS member_count,
       avg(q.pct_chg) AS avg_pct_chg,
       sum(q.amount) AS total_amount,
       count(*) FILTER (WHERE q.pct_chg > 0) AS up_count,
       count(*) FILTER (WHERE q.pct_chg < 0) AS down_count
FROM l1_members lm
JOIN stocks s ON s.symbol = lm.symbol
JOIN daily_quotes q ON q.stock_id = s.id AND q.trade_date = :as_of
WHERE q.pct_chg IS NOT NULL
GROUP BY lm.code, lm.name
ORDER BY avg_pct_chg DESC
""")
```

service：`as_of` 走 `get_latest_trade_date`，Redis key `market:sw-performance`，TTL `_MARKET_CACHE_TTL`；`limit` 默认 31 截断。API 端点风格与 Task 2.5 相同。

- [ ] **Step 3: 实机验证（隐身窗口匿名可达 + 数字对拍）**

```bash
curl -s "http://localhost:80/api/v1/market/sw-industry/performance?limit=5"
# 对拍：任一 L1 的 avg_pct_chg 与 /market/sectors 同行业（CSRC 口径同名行业）差值应在口径差异内
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(market): SW L1 industry performance aggregation endpoint"
```

---

### Task 3.2: SectorFlow 切申万口径

**Files:**
- Modify: `frontend/src/shared/api/market.ts`（加 `fetchSwPerformance`）
- Modify: `frontend/src/features/market/components/SectorFlow.tsx`（左列数据源切换）
- Test: `frontend/e2e/public-homepage.spec.ts`（更新 Task 1.7 的口径断言）

- [ ] **Step 1: 更新 e2e（先红）**

把 Task 1.7 的 `await expect(section.getByText("行业口径：证监会")).toBeVisible();` 改为：

```ts
await expect(section.getByText("行业口径：申万一级")).toBeVisible();
```
Run → FAIL（仍是 CSRC）。

- [ ] **Step 2: 切换数据源 + 失败回退**

左列改 `fetchSwPerformance`（行版式：行业名 + `member_count` 小字 + `DeltaText(avg_pct_chg)`，红绿条按 `up_count`/`down_count` 比例）；**请求失败时回退 `fetchSectors`（CSRC）并把口径标注切回「证监会」**——降级不静默换口径，UI 必须如实标注。删除「申万版即将上线」文案。

- [ ] **Step 3: 跑 e2e + tsc，Commit**

```bash
git commit -m "feat(landing): sector flow on SW L1 basis with CSRC fallback"
```

---

# 收口（Phase 0–3 全部完成后）

### Task W: 文档同步与门禁

**Files:**
- Modify: `docs/Changelog.md`（按既有格式补记）
- Modify: `docs/references/best-practices.md`（如踩到新坑）
- Modify: `plans/2026-09-11-public-market-homepage.md`（风险表销项、Phase 0 结论落字）

- [ ] **Step 1**: `git status --porcelain` + `git diff --stat` 列改动；对照 best-practices 探测器映射表 grep diff
- [ ] **Step 2**: `bash scripts/self_review.sh`（后端改动较多 → `--full`，含 mypy/pytest；pytest 自动排除 e2e 与 bench）
- [ ] **Step 3**: `cd frontend && npx tsc -b && npm run test:e2e` 全量
- [ ] **Step 4**: Changelog 补记（涉及模块：backend/models|repositories|services|api、frontend/shared|features|pages/landing、e2e）
- [ ] **Step 5**: Commit + 推送；**stacked PR 不触发 CI**（D9）——`/c/Program\ Files/GitHub\ CLI/gh.exe workflow run` 手动触发或等栈底合并

---

## Self-Review 记录（写计划时已核对）

1. **Spec coverage**：Spec §0.3 缺口 A→Task 1.3/2.1–2.7；缺口 B/C→Phase 0 spike + Phase 4/5 另立计划（Spec 已声明）；§0.4 对比度→Task 1.1；§0.5 防护→Task 1.3 + Task 0.3；D1 区块→Task 1.4–1.10；D3/D4→Task 2.4/2.5；D6→Task 3.1/3.2；D7→Task 1.1；D8→Task 2.3；风险 ②③④→Task 0.2；风险 ⑥→Task 2.6；风险 ⑦→Task 0.3。**日历区块（Task 1.4 挂空骨架）在 Phase 4 前显示骨架 + 「即将上线」文案，已在 Task 1.4 隐含——执行时给该 SectionCard 加 `moreText` 为空与占位文案。**
2. **Placeholder scan**：无 TBD；两处「以实际为准」标注（`ingest_daily` 方法名、`StockListParams.exchange` 字段名）是**执行时对齐点**而非缺失内容——测试意图与实现代码均已给出。
3. **Type consistency**：`RankingType` 前后端字面量集合一致（5 值）；`fetchRankings`/`RankingResponse` 字段与 `RankingResponseOut` 对齐；`DataRow`/`DeltaText`/`SectionCard` 在消费任务中的 props 与 Task 1.2 定义一致；`get_latest_trade_date` 在 2.4 定义、2.5/3.1 消费。
