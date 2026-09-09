# 市场数据面（market-data-face）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为市场页/个股页接入五类三方数据（板块资金流、龙虎榜、北向、公告快讯、大宗/解禁/回购）与全球市场指数区块（亚洲/美洲 Tab + 增强 K 卡），后端双轨采集（APScheduler + RabbitMQ Worker），前端图形优先呈现。

**Architecture:** 新增 7 张表 + `EastmoneyClient`/`CninfoClient` 两个 HTTP 客户端 + TuShare 客户端 6 个方法；`market_data_service`（采集+读取）与 `announcement_service`（巨潮公告）；新增 `/market/*` 8 个只读端点、1 个手动触发任务队列 `market_data.fetch`；全球指数日线**复用现有 `index_dailies` 表**（ts_code 通用，`N225`/`HSI` 等裸代码不与 `000001.SH` 冲突）。前端新增 `shared/api/marketData.ts` + 市场页 5 个新组件 + 个股页相关数据卡，指数详情页零成本复用现有 `/index/:tsCode` + `KlineChart`。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（手写迁移）+ Redis CacheClient + APScheduler + aio-pika Worker + httpx；React 18 + antd 5 + ECharts + TanStack Query。

**Spec:** 本文档 §背景与设计（brainstorming 定稿 + 用户图片反馈修订版，已获批准）。

## Global Constraints

- 后端命令一律 `cd backend && uv run ...`；lint `uv run --extra dev ruff check .`、类型 `uv run --extra dev mypy app` 必须零报错（新代码）。
- 函数内局部 import 加 `# noqa: PLC0415`（仓库既有约定）。
- **不新增任何 pip/npm 依赖**（httpx、tushare、pandas 均已存在）。
- **单位铁律**（已在真实接口上验证，映射错一位数 UI 差四个数量级）：
  - 东财板块资金流：`f62/f66/f72` 单位**元**；
  - TuShare `top_list.amount/l_buy/...` 单位**元**；`block_trade`: price 元 / vol **万股** / amount **万元**；`share_float.float_share` **万股**、`float_ratio` %；`repurchase`: vol **股**、amount **元**；`moneyflow_hsgt.north_money` **万元**（验证过 hgt+sgt==north_money 恒等）；
  - 前端展示层统一换算：亿=÷1e8，亿股=万股÷1e4，北向亿=万元÷1e4。
- 东财外呼礼仪：仅用批量端点（ulist/clist），客户端内全局 ≥0.3s 间隔 + UA + Referer；ulist 用 `push2delay.eastmoney.com`（实测 push2 对 ulist 返回空，clist 用 push2 正常）。
- 巨潮单次 poll ≤5 页 ×30 条。
- TuShare 一律走现有 `TuShareClient`（RateLimitedSyncProvider 0.5s 限速重试）。
- Alembic 迁移**手写**（仓库约定），新 revision id `f7a8b9c0d1e2`，down_revision `e6f7a8b9c0d1`（当前 head）。
- Redis key 前缀 `market:`；实时类读取 TTL 60s。
- 新 ECharts 一律 `notMerge`（P1 教训）；颜色 import `COLORS` from `@/app/theme`（红涨绿跌）。
- 后端测试走仓库既有风格：纯单测（monkeypatch 假 repo/假 client + `_FakeCache`，参照 `tests/test_kline_adjust.py`），**不建 DB fixture**；活栈集成验证用 `docker exec backend_api python -m app.services.market_data_service <job>` + curl。
- 每个任务独立提交（`feat(market-data): ...` 中文说明）；AGENTS.md 文档三连（Changelog/best-practices/交叉引用）在 Task 15 统一收尾。

## 背景与设计（已批准）

1. **全球市场区块**（替换现有横滑指数条）：`Tabs 亚洲/美洲` + 一屏 6 卡栅格。亚洲=上证/深证成指/创业板指/恒生/日经225/KOSPI；美洲=道琼斯/标普500/纳斯达克。卡片=市场字母徽章(CN/HK/JP/KR/US 彩色圆标)+名称+代码+点位+涨跌额/幅+**近30日 sparkline**（复用 `sparkOption`，随涨跌着色）。实时点位：后端 `/market/global-indices` 服务端调东财 ulist 批量快照（60s Redis 缓存，全体用户共享 1 次/分钟外呼），DB 日线兜底（`source:"eod"`）。点击进 `/index/:tsCode` 详情页（K 线数据来自 `index_dailies`，现有 `KlineChart` fetcher 注入零改动）。欧非/外汇/债券 Tab 本期不做。
2. **板块资金流卡升级**（替换现 `CapitalFlowChart` 近似实现）：真实东财主力净流入，行业/概念 `Segmented` 切换，Top10 红绿横向柱状（正红负绿），前端 60s 轮询；后端盘中每 5 分钟 upsert 当日快照。
3. **北向资金独立折线卡**：近 30 日净流入折线 + 当日值大字（盘后 TuShare `moneyflow_hsgt`，交易所 2024.8 后仅盘后净额，如实呈现）。
4. **数据面 Tab 区块**：龙虎榜/大宗交易/解禁/回购 紧凑表格（Top 10-15，净额±着色、头部行加粗）+ 公告快讯时间流（巨潮，财报+重大事项两类）。
5. **个股详情页相关数据卡**：`Segmented` 龙虎榜/大宗/解禁/回购/公告，按 symbol 过滤。
6. 市场页新布局：全球市场 → 行情中心 Row1 涨跌分布+热力图 → Row2 板块资金流+北向 → Row3 热门板块(通栏) → Row4 数据面(通栏) → 行业分类。

## 已验证数据源事实（执行者直接使用，勿再猜测）

### 东财 push2delay `GET /api/qt/ulist.np/get`（指数实时快照，2026-09-03 实测）
参数：`ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&np=1&fields=f1,f2,f3,f4,f12,f13,f14&secids=1.000001,0.399001,0.399006,100.HSI,100.N225,100.KS11,100.DJIA,100.SPX,100.NDX`，Header 需 UA。响应 `data.diff[]`：`f2`=最新价(小数，停牌/无值为字符串`"-"`)、`f3`=涨跌幅、`f4`=涨跌额、`f12`=代码(`N225`)、`f13`=市场(100)、`f14`=名称(`日经225`/`韩国KOSPI`/`道琼斯`/`标普500`/`纳斯达克`)。**注意：push2 主站在本环境对 ulist 返回空，必须用 push2delay。**

### 东财 push2 `GET /api/qt/clist/get`（板块资金流，实测）
行业 `fs=m:90+t:2+f:!50`（total 496），概念 `fs=m:90+t:3+f:!50`（total 504）。参数 `pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f62&fields=f12,f14,f3,f62,f66,f72,f104,f105,f184`。`diff[]`：`f12`=板块代码(BK1203)、`f14`=名称、`f3`=涨跌幅、`f62`=主力净流入(**元**)、`f66`=超大单净额(元)、`f72`=大单净额(元)、`f104`/`f105`=上涨/下跌家数、`f184`=主力净占比(%)。按 f62 降序返回。

### TuShare（容器内实测，token 已在 backend/.env 的 `TUSHARE_TOKEN`）
- `index_global(ts_code=..., start_date=..., end_date=...)`：按代码拉全史（N225 两年 407 行）；也可 `trade_date=YYYYMMDD` 拉当日全部 22 指数。cols：`ts_code, trade_date, open, close, high, low, pre_close, change, pct_chg, swing, vol`。**ts_code 为裸代码**（`N225/KS11/HSI/DJI/SPX/IXIC/FTSE/GDAXI/...`，无 A 股）。`vol` 可为 NaN。
- `moneyflow_hsgt(start_date, end_date)`：cols `trade_date, ggt_ss, ggt_sz, hgt, sgt, north_money, south_money`，**全部为字符串**，north_money 单位**万元**（实测 hgt+sgt==north_money）。
- `top_list(trade_date=YYYYMMDD)`：cols `trade_date, ts_code, name, close, pct_change, turnover_rate, amount, l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason`，金额单位**元**，reason 为上榜原因长文本。
- `block_trade(trade_date)`：cols `ts_code, trade_date, price, vol, amount, buyer, seller`；price 元 / vol 万股 / amount 万元（实测 price×vol≈amount）。
- `share_float(start_date, end_date)`：cols `ts_code, ann_date, float_date, float_share, float_ratio, holder_name, share_type`；float_share 万股，float_ratio %，ann_date 可空。
- `repurchase(start_date, end_date)`（**接口名不是 share_repurchase**）：cols `ts_code, ann_date, end_date, proc, exp_date, vol, amount, high_limit, low_limit`；vol 股、amount 元、proc∈{实施,完成,...}、exp_date 常为 NaN。

### 巨潮 cninfo `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`（实测）
form 表单：`pageNum, pageSize, column=szse, tabName=fulltext, seDate=2026-08-20~2026-09-03, category=<;分隔类目>, isHLtitle=true`。**column=szse 即同时覆盖沪深**（实测返回 002xxx 与 601xxx 混合）。响应 `announcements[]`：`announcementId`(去重主键)、`secCode/secName`、`announcementTitle`(isHLtitle 时含 `<em>` 高亮标签，需 strip)、`announcementTime`(**毫秒** epoch)、`adjunctUrl`(拼 `http://static.cninfo.com.cn/` 前缀为 PDF)。类目：财报=`category_yjdbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_ndbg_szsh;category_yjygjxz_szsh;category_yjkb_szsh`，重大事项=`category_zf_szsh;category_pgjz_szsh;category_gqfpxzcs_szsh;category_lr_gqbl_szsh`。

### 代码库关键事实（探索代理核实）
- 迁移 head `e6f7a8b9c0d1`；`index_dailies` 表 `(ts_code String(16), trade_date)` OHLCV + `uq_index_dailies_code_date`，`index_repo.upsert_index_dailies(db, rows)` 可直接复用。
- repo 为模块级 async 函数，服务内 `async with async_session_factory() as db` 自开会话，repo 只 `flush()`、调用方 `commit()`。
- 调度：`app/scheduler/runner.py` 显式 `scheduler.add_job(func, CronTrigger(..., timezone="Asia/Shanghai"), id=..., replace_existing=True)`；job 函数体懒加载 service；`app/scheduler/jobs.py` 有 `_is_workday()/_in_trading_hours()` 可复用。
- Worker：继承 `BaseWorker`（`queue_key` + `async process(task_id, payload)`），`app/core/mq.py` `QUEUES` 注册，`app/workers/runner.py` 实例化列表；手动触发走 `POST /tasks/fetch-*`（`task_service` 建行+发 `{"task_id","type","payload"}`）。
- Redis：`app/core/redis.py` `CacheClient`（get/set(ttl)/delete/delete_pattern），端点用 `CacheDep`。
- 测试：`tests/` 集成风格（httpx 打活栈）+ 纯单测（monkeypatch）；无 DB fixture。
- 前端：市场页 `src/pages/market/index.tsx`（组件全在 `src/features/market/components/`，barrel `index.ts`）；`IndexCard`/`MarketOverview`（SSE 合并逻辑）将被替换删除；`CapitalFlowChart` 将被替换删除；`shared/ui/EChart.tsx` 有 `EChart`+`sparkOption`；`@/app/theme` 有 `COLORS`（up `#ef4444` / down `#22c55e` / flat `#9ca3af` / primary `#1677ff`）；指数详情 `src/pages/index-detail/index.tsx` 用 `fetchMarketIndices` 查找 + `KlineChart`（props: `fetcher(days, adjust)`）；antd Tabs 懒挂载（`items` 数组模式，参照 `pages/research-workbench/index.tsx`）；e2e：antd 5.24 用 `.ant-segmented-item`+selected class、`getByRole("tab")`。

---

### Task 1: 数据表 — 7 个模型 + 手写迁移

**Files:**
- Create: `backend/app/models/market_data.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/migrations/versions/f7a8b9c0d1e2_market_data_tables.py`

**Interfaces:**
- Produces: 模型类 `SectorMoneyflowSnapshot, DragonTigerEntry, NorthboundDaily, BlockTrade, ShareFloat, StockRepurchase, Announcement`（后续 repo/service/测试 import 用）；表 `sector_moneyflow_snapshots, dragon_tiger_entries, northbound_daily, block_trades, share_floats, stock_repurchases, announcements`。

- [ ] **Step 1: 写模型文件**

```python
"""Market-data face models: sector moneyflow / dragon tiger / northbound / block trades / share float / repurchase / announcements."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SectorMoneyflowSnapshot(Base):
    """东财板块主力资金流当日快照（盘中每 5 分钟 upsert 覆盖，跨日保留）。金额单位：元。"""

    __tablename__ = "sector_moneyflow_snapshots"
    __table_args__ = (
        UniqueConstraint("trade_date", "dimension", "board_code", name="uq_sector_moneyflow_dim_code_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)  # industry | concept
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    board_name: Mapped[str | None] = mapped_column(String(32))
    pct_change: Mapped[float | None] = mapped_column(Float)
    main_net_inflow: Mapped[float | None] = mapped_column(Float)  # 元
    super_large_net: Mapped[float | None] = mapped_column(Float)  # 元
    large_net: Mapped[float | None] = mapped_column(Float)  # 元
    main_net_ratio: Mapped[float | None] = mapped_column(Float)  # %
    up_count: Mapped[int | None] = mapped_column(Integer)
    down_count: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DragonTigerEntry(Base):
    """龙虎榜个股明细（TuShare top_list）。金额单位：元。"""

    __tablename__ = "dragon_tiger_entries"
    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", "reason", name="uq_dragon_tiger_date_code_reason"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    l_buy: Mapped[float | None] = mapped_column(Float)
    l_sell: Mapped[float | None] = mapped_column(Float)
    l_amount: Mapped[float | None] = mapped_column(Float)
    net_amount: Mapped[float | None] = mapped_column(Float)
    net_rate: Mapped[float | None] = mapped_column(Float)
    amount_rate: Mapped[float | None] = mapped_column(Float)
    float_values: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:top_list")


class NorthboundDaily(Base):
    """北向资金每日净流入（TuShare moneyflow_hsgt，盘后）。单位：万元。"""

    __tablename__ = "northbound_daily"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_northbound_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    net_amount: Mapped[float | None] = mapped_column(Float)  # 万元
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:moneyflow_hsgt")


class BlockTrade(Base):
    """大宗交易（TuShare block_trade）。price 元 / volume 万股 / amount 万元。"""

    __tablename__ = "block_trades"
    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", "buyer", "seller", "price", "volume", name="uq_block_trades_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)  # 万股
    amount: Mapped[float | None] = mapped_column(Float)  # 万元
    buyer: Mapped[str | None] = mapped_column(Text)
    seller: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:block_trade")


class ShareFloat(Base):
    """限售解禁（TuShare share_float）。float_share 万股 / float_ratio %。"""

    __tablename__ = "share_floats"
    __table_args__ = (
        UniqueConstraint("ann_date", "ts_code", "holder_name", "share_type", name="uq_share_floats_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ann_date: Mapped[date | None] = mapped_column(Date)
    float_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    float_share: Mapped[float | None] = mapped_column(Float)  # 万股
    float_ratio: Mapped[float | None] = mapped_column(Float)  # %
    holder_name: Mapped[str | None] = mapped_column(Text)
    share_type: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:share_float")


class StockRepurchase(Base):
    """股票回购（TuShare repurchase）。vol 股 / amount 元。"""

    __tablename__ = "stock_repurchases"
    __table_args__ = (
        UniqueConstraint("ann_date", "ts_code", "proc", name="uq_stock_repurchases_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ann_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    proc: Mapped[str] = mapped_column(String(16), nullable=False)  # 实施/完成/...
    exp_date: Mapped[date | None] = mapped_column(Date)
    vol: Mapped[float | None] = mapped_column(Float)  # 股
    amount: Mapped[float | None] = mapped_column(Float)  # 元
    high_limit: Mapped[float | None] = mapped_column(Float)
    low_limit: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:repurchase")


class Announcement(Base):
    """公告快讯（巨潮 cninfo，财报+重大事项两类）。"""

    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("announcement_id", name="uq_announcements_cninfo_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    announcement_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sec_code: Mapped[str] = mapped_column(String(12), nullable=False)
    sec_name: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    announce_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)  # report | event
    pdf_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- [ ] **Step 2: 注册到 `app/models/__init__.py`**（文件末尾既有 import 块后追加一行组、并加入 `__all__` 若存在）

```python
from app.models.market_data import (  # noqa: F401
    Announcement,
    BlockTrade,
    DragonTigerEntry,
    NorthboundDaily,
    SectorMoneyflowSnapshot,
    ShareFloat,
    StockRepurchase,
)
```

- [ ] **Step 3: 写迁移**（手写，先读 `app/migrations/versions/e6f7a8b9c0d1_add_industry_knowledge.py` 对齐风格）

```python
"""market-data face: sector moneyflow / dragon tiger / northbound / block trades / share float / repurchase / announcements

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-03

单位口径（已在真实数据源上验证）：
- 东财资金流、top_list、repurchase.amount：元
- block_trade：price 元 / volume 万股 / amount 万元
- share_float.float_share：万股；northbound.net_amount：万元
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sector_moneyflow_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("board_code", sa.String(length=16), nullable=False),
        sa.Column("board_name", sa.String(length=32), nullable=True),
        sa.Column("pct_change", sa.Float(), nullable=True),
        sa.Column("main_net_inflow", sa.Float(), nullable=True),
        sa.Column("super_large_net", sa.Float(), nullable=True),
        sa.Column("large_net", sa.Float(), nullable=True),
        sa.Column("main_net_ratio", sa.Float(), nullable=True),
        sa.Column("up_count", sa.Integer(), nullable=True),
        sa.Column("down_count", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "dimension", "board_code", name="uq_sector_moneyflow_dim_code_date"),
    )
    op.create_index("ix_sector_moneyflow_date_dim", "sector_moneyflow_snapshots", ["trade_date", "dimension"])

    op.create_table(
        "dragon_tiger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_change", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("l_buy", sa.Float(), nullable=True),
        sa.Column("l_sell", sa.Float(), nullable=True),
        sa.Column("l_amount", sa.Float(), nullable=True),
        sa.Column("net_amount", sa.Float(), nullable=True),
        sa.Column("net_rate", sa.Float(), nullable=True),
        sa.Column("amount_rate", sa.Float(), nullable=True),
        sa.Column("float_values", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "ts_code", "reason", name="uq_dragon_tiger_date_code_reason"),
    )
    op.create_index("ix_dragon_tiger_date", "dragon_tiger_entries", ["trade_date"])

    op.create_table(
        "northbound_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("net_amount", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", name="uq_northbound_date"),
    )

    op.create_table(
        "block_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("buyer", sa.Text(), nullable=True),
        sa.Column("seller", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "ts_code", "buyer", "seller", "price", "volume", name="uq_block_trades_dedupe"),
    )
    op.create_index("ix_block_trades_date", "block_trades", ["trade_date"])
    op.create_index("ix_block_trades_code", "block_trades", ["ts_code"])

    op.create_table(
        "share_floats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("float_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("float_share", sa.Float(), nullable=True),
        sa.Column("float_ratio", sa.Float(), nullable=True),
        sa.Column("holder_name", sa.Text(), nullable=True),
        sa.Column("share_type", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ann_date", "ts_code", "holder_name", "share_type", name="uq_share_floats_dedupe"),
    )
    op.create_index("ix_share_floats_date", "share_floats", ["float_date"])
    op.create_index("ix_share_floats_code", "share_floats", ["ts_code"])

    op.create_table(
        "stock_repurchases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("proc", sa.String(length=16), nullable=False),
        sa.Column("exp_date", sa.Date(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("high_limit", sa.Float(), nullable=True),
        sa.Column("low_limit", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ann_date", "ts_code", "proc", name="uq_stock_repurchases_dedupe"),
    )
    op.create_index("ix_stock_repurchases_date", "stock_repurchases", ["ann_date"])
    op.create_index("ix_stock_repurchases_code", "stock_repurchases", ["ts_code"])

    op.create_table(
        "announcements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("announcement_id", sa.String(length=32), nullable=False),
        sa.Column("sec_code", sa.String(length=12), nullable=False),
        sa.Column("sec_name", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("announce_time", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_id", name="uq_announcements_cninfo_id"),
    )
    op.create_index("ix_announcements_time", "announcements", ["announce_time"])
    op.create_index("ix_announcements_code", "announcements", ["sec_code"])


def downgrade() -> None:
    op.drop_table("announcements")
    op.drop_table("stock_repurchases")
    op.drop_table("share_floats")
    op.drop_table("block_trades")
    op.drop_table("northbound_daily")
    op.drop_table("dragon_tiger_entries")
    op.drop_table("sector_moneyflow_snapshots")
```

- [ ] **Step 4: 验证** — `cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app`；`uv run pytest tests/test_kline_adjust.py -q`（确认 import 链未破坏）；活栈升级 `docker compose run --rm migrate && docker compose up -d --build api worker scheduler`，然后 `docker exec postgres psql -U stock_user -d stock_bot -c "\dt sector_moneyflow_snapshots; \dt announcements"`（7 张表都应在）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/market_data.py backend/app/models/__init__.py backend/app/migrations/versions/f7a8b9c0d1e2_market_data_tables.py
git commit -m "feat(market-data): 7 张数据表模型与迁移（资金流/龙虎榜/北向/大宗/解禁/回购/公告）"
```

---

### Task 2: EastmoneyClient（ulist 快照 + clist 板块资金流）

**Files:**
- Create: `backend/app/core/providers/eastmoney_client.py`
- Test: `backend/tests/test_eastmoney_client.py`

**Interfaces:**
- Produces: `EastmoneyClient.fetch_index_snapshot(secids: list[str]) -> list[dict]`（元素 `{code, name, price, pct_change, change}`，值 `float | None`，`"-"`→None）；`EastmoneyClient.fetch_sector_moneyflow(dimension: str) -> list[dict]`（元素 `{board_code, board_name, pct_change, main_net_inflow, super_large_net, large_net, up_count, down_count, main_net_ratio}`，元）；模块单例 `get_eastmoney_client() -> EastmoneyClient`；可覆写钩子 `_get_json(base: str, path: str, params: dict) -> dict`。

- [ ] **Step 1: 写失败测试**

```python
"""EastmoneyClient 解析单测（不打真实网络，monkeypatch _get_json）。"""

from app.core.providers.eastmoney_client import EastmoneyClient


class _FakeEM:
    """预置响应的假客户端：按 (path 末段) 返回 canned json。"""

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def _get_json(self, base: str, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        return self._responses[path.rsplit("/", 1)[-1]]


async def test_fetch_index_snapshot_parses_and_handles_dash():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM({
        "get": {
            "rc": 0,
            "data": {
                "diff": [
                    {"f2": 64214.48, "f3": -0.17, "f4": -111.16, "f12": "N225", "f13": 100, "f14": "日经225"},
                    {"f2": "-", "f3": "-", "f4": "-", "f12": "KS11", "f13": 100, "f14": "韩国KOSPI"},
                ]
            },
        }
    })._get_json
    rows = await client.fetch_index_snapshot(["100.N225", "100.KS11"])
    assert rows[0] == {"code": "N225", "name": "日经225", "price": 64214.48, "pct_change": -0.17, "change": -111.16}
    assert rows[1]["price"] is None and rows[1]["pct_change"] is None and rows[1]["change"] is None


async def test_fetch_sector_moneyflow_maps_fields_yuan():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM({
        "get": {
            "rc": 0,
            "data": {
                "diff": [
                    {"f12": "BK1203", "f14": "非银金融", "f3": 0.28, "f62": 2151238400.0,
                     "f66": 1925688320.0, "f72": 225550080.0, "f104": 48, "f105": 26, "f184": 4.15},
                ]
            },
        }
    })._get_json
    rows = await client.fetch_sector_moneyflow("industry")
    assert rows[0] == {
        "board_code": "BK1203", "board_name": "非银金融", "pct_change": 0.28,
        "main_net_inflow": 2151238400.0, "super_large_net": 1925688320.0, "large_net": 225550080.0,
        "up_count": 48, "down_count": 26, "main_net_ratio": 4.15,
    }
    assert "m:90+t:2" in rows and False or True  # dimension 校验见下一断言


async def test_fetch_sector_moneyflow_concept_uses_t3():
    client = EastmoneyClient()
    fake = _FakeEM({"get": {"rc": 0, "data": {"diff": []}}})
    client.__dict__["_get_json"] = fake._get_json
    await client.fetch_sector_moneyflow("concept")
    assert "m:90+t:3" in fake.calls[0][1]["fs"]
```

（注：第三条测试末行 `assert "m:90+t:2" in rows and False or True` 写错即删——直接删掉该行，保留两条核心断言测试即可。最终文件只保留 `test_fetch_index_snapshot_parses_and_handles_dash`、`test_fetch_sector_moneyflow_maps_fields_yuan`（去掉末行）、`test_fetch_sector_moneyflow_concept_uses_t3`。）

- [ ] **Step 2: 运行确认失败** — `cd backend && uv run pytest tests/test_eastmoney_client.py -q` → ModuleNotFoundError。

- [ ] **Step 3: 实现**

```python
"""Eastmoney push2 HTTP client (batch-only endpoints, throttled).

Verified endpoints (2026-09-03):
- ulist.np/get on push2delay (push2 proper returns empty in this environment)
- clist/get on push2 for sector money flow
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_SNAPSHOT_BASE = "https://push2delay.eastmoney.com"
_CLIST_BASE = "https://push2.eastmoney.com"
_MIN_INTERVAL = 0.3


def _num(v: Any) -> float | None:
    """东财 fltt=2 下无效值为字符串 '-'。"""
    if v is None or isinstance(v, str):
        return None
    return float(v)


class EastmoneyClient:
    """节流 + UA 的东财只读客户端；仅批量端点，杜绝逐股轮询。"""

    def __init__(self) -> None:
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=httpx.Timeout(10.0),
        )

    async def _get_json(self, base: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        resp = await self._client.get(base + path, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") not in (0, None):
            raise RuntimeError(f"eastmoney rc={data.get('rc')} path={path}")
        return data

    async def fetch_index_snapshot(self, secids: list[str]) -> list[dict[str, Any]]:
        data = await self._get_json(
            _SNAPSHOT_BASE,
            "/api/qt/ulist.np/get",
            {
                "ut": _EM_UT, "fltt": 2, "invt": 2, "np": 1,
                "fields": "f2,f3,f4,f12,f13,f14",
                "secids": ",".join(secids),
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        return [
            {
                "code": d.get("f12"),
                "name": d.get("f14"),
                "price": _num(d.get("f2")),
                "pct_change": _num(d.get("f3")),
                "change": _num(d.get("f4")),
            }
            for d in diff
        ]

    async def fetch_sector_moneyflow(self, dimension: str) -> list[dict[str, Any]]:
        if dimension not in ("industry", "concept"):
            raise ValueError(f"dimension must be industry|concept, got {dimension}")
        fs = "m:90+t:2+f:!50" if dimension == "industry" else "m:90+t:3+f:!50"
        data = await self._get_json(
            _CLIST_BASE,
            "/api/qt/clist/get",
            {
                "pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f62", "fs": fs,
                "fields": "f12,f14,f3,f62,f66,f72,f104,f105,f184",
            },
        )
        diff = (data.get("data") or {}).get("diff") or []
        return [
            {
                "board_code": str(d.get("f12")),
                "board_name": d.get("f14"),
                "pct_change": _num(d.get("f3")),
                "main_net_inflow": _num(d.get("f62")),
                "super_large_net": _num(d.get("f66")),
                "large_net": _num(d.get("f72")),
                "up_count": d.get("f104"),
                "down_count": d.get("f105"),
                "main_net_ratio": _num(d.get("f184")),
            }
            for d in diff
        ]


_client: EastmoneyClient | None = None


def get_eastmoney_client() -> EastmoneyClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = EastmoneyClient()
    return _client
```

- [ ] **Step 4: 测试通过** — `uv run pytest tests/test_eastmoney_client.py -q` → 3 passed。
- [ ] **Step 5: 活体冒烟**（可选但推荐）— `docker exec backend_api python -c "import asyncio;from app.core.providers.eastmoney_client import get_eastmoney_client as g;print(asyncio.run(g().fetch_index_snapshot(['100.N225','100.SPX'])))"`（需先重建镜像：`docker compose up -d --build api`）。
- [ ] **Step 6: Commit** — `git commit -m "feat(market-data): EastmoneyClient（ulist 指数快照 + clist 板块资金流，节流+UA）"`

---

### Task 3: TuShare 扩展 + 全球指数日线 ingest（复用 index_dailies）+ CLI 入口

**Files:**
- Modify: `backend/app/core/providers/tushare_client.py`（新增 6 个 fetch 方法）
- Create: `backend/app/services/market_data_service.py`
- Modify: `backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`
- Test: `backend/tests/test_market_data_mapping.py`（本任务建文件，后续任务追加）

**Interfaces:**
- Produces（`market_data_service` 模块级）：
  - `GLOBAL_INDICES: list[dict]`（9 指数注册表，含 ts_code/name/market/region/em_secid/source）
  - `ingest_global_index_daily(db: AsyncSession, lookback_days: int = 14) -> dict`（返回 `{"upserted": int}`）
  - `backfill_global_index_history(db: AsyncSession, years: int = 2) -> dict`
  - `python -m app.services.market_data_service <job>` CLI（job: `global_index_daily|backfill_global_index`，后续任务扩展）
- TuShare 新方法：`fetch_index_global(ts_code, start_date, end_date)`、`fetch_moneyflow_hsgt(start_date, end_date)`、`fetch_top_list(trade_date)`、`fetch_block_trade(trade_date)`、`fetch_share_float(start_date, end_date)`、`fetch_repurchase(start_date, end_date)` — 全部返回 `pd.DataFrame`。

- [ ] **Step 1: 先读两个文件核对签名** — `app/models/index_daily.py`（确认 `IndexDaily` 列名：预期 `ts_code/trade_date/open/high/low/close/volume/amount`）与 `app/repositories/index_repo.py`（确认 `upsert_index_dailies(db, rows: list[IndexDaily])` 与 `get_kline` 签名；下面代码按此预期写，若列名/参数名不同以实际为准微调）。

- [ ] **Step 2: TuShare 客户端追加方法**（在 `tushare_client.py` 既有 fetch 方法区域末尾追加，模式照抄 `fetch_index_daily`）

```python
    async def fetch_index_global(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """全球指数日线（裸代码如 N225/KS11/HSI/DJI/SPX/IXIC）。"""
        return await self._query(
            "index_global",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_moneyflow_hsgt(self, start_date: str, end_date: str) -> pd.DataFrame:
        """沪深港通资金（north_money 为字符串、单位万元，映射层处理）。"""
        return await self._query("moneyflow_hsgt", start_date=start_date, end_date=end_date)

    async def fetch_top_list(self, trade_date: str) -> pd.DataFrame:
        return await self._query("top_list", trade_date=trade_date)

    async def fetch_block_trade(self, trade_date: str) -> pd.DataFrame:
        return await self._query("block_trade", trade_date=trade_date)

    async def fetch_share_float(self, start_date: str, end_date: str) -> pd.DataFrame:
        return await self._query("share_float", start_date=start_date, end_date=end_date)

    async def fetch_repurchase(self, start_date: str, end_date: str) -> pd.DataFrame:
        return await self._query("repurchase", start_date=start_date, end_date=end_date)
```

- [ ] **Step 3: 写失败测试**（`tests/test_market_data_mapping.py`）

```python
"""market_data_service 纯映射/组装单测（monkeypatch，不触 DB/网络）。"""

from datetime import date

import pytest

from app.services import market_data_service as mds


def test_global_indices_registry_shape():
    assert len(mds.GLOBAL_INDICES) == 9
    asia = [g for g in mds.GLOBAL_INDICES if g["region"] == "asia"]
    americas = [g for g in mds.GLOBAL_INDICES if g["region"] == "americas"]
    assert [g["ts_code"] for g in asia] == ["000001.SH", "399001.SZ", "399006.SZ", "HSI", "N225", "KS11"]
    assert [g["ts_code"] for g in americas] == ["DJI", "SPX", "IXIC"]
    assert {g["em_secid"] for g in americas} == {"100.DJIA", "100.SPX", "100.NDX"}


def test_map_index_global_row_nan_vol_to_none():
    row = {
        "ts_code": "KS11", "trade_date": "20260902", "open": 6625.47, "close": 6562.72,
        "high": 6694.57, "low": 6558.3, "vol": float("nan"),
    }
    mapped = mds._map_index_global_row(row)
    assert mapped == {
        "ts_code": "KS11", "trade_date": date(2026, 9, 2),
        "open": 6625.47, "high": 6694.57, "low": 6558.3, "close": 6562.72,
        "volume": None,
    }


@pytest.mark.asyncio
async def test_ingest_global_index_daily_filters_and_upserts(monkeypatch):
    calls: list = []

    async def fake_fetch_index_global(ts_code, start_date, end_date):
        calls.append(ts_code)
        if ts_code != "N225":
            return mds.pd.DataFrame()
        return mds.pd.DataFrame(
            [{"ts_code": "N225", "trade_date": "20260902", "open": 65195.43, "close": 64325.64,
              "high": 65195.43, "low": 64215.47, "vol": 1724660.8}]
        )

    async def fake_fetch_index_daily(ts_code, start_date, end_date):
        return mds.pd.DataFrame()

    upserted: list = []

    async def fake_upsert(db, rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(mds, "_get_tushare", lambda: type("C", (), {
        "fetch_index_global": staticmethod(fake_fetch_index_global),
        "fetch_index_daily": staticmethod(fake_fetch_index_daily),
    })())
    monkeypatch.setattr(mds.index_repo, "upsert_index_dailies", fake_upsert)

    result = await mds.ingest_global_index_daily(db=None, lookback_days=14)
    assert result == {"upserted": 1}
    assert upserted[0].ts_code == "N225" and upserted[0].volume == 1724660.8
    assert set(calls) == {"HSI", "N225", "KS11", "DJI", "SPX", "IXIC"}  # 只拉 index_global 源
```

- [ ] **Step 4: 运行确认失败** — `uv run pytest tests/test_market_data_mapping.py -q` → AttributeError（模块不存在）。

- [ ] **Step 5: 实现 `market_data_service.py`**

```python
"""Market-data face: ingest + read services.

数据源（字段/单位见 plans/2026-09-03-market-data-face.md「已验证数据源事实」）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import index_repo

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")

GLOBAL_INDICES: list[dict[str, str]] = [
    {"ts_code": "000001.SH", "name": "上证指数", "market": "CN", "region": "asia", "em_secid": "1.000001", "source": "index_daily"},
    {"ts_code": "399001.SZ", "name": "深证成指", "market": "CN", "region": "asia", "em_secid": "0.399001", "source": "index_daily"},
    {"ts_code": "399006.SZ", "name": "创业板指", "market": "CN", "region": "asia", "em_secid": "0.399006", "source": "index_daily"},
    {"ts_code": "HSI", "name": "恒生指数", "market": "HK", "region": "asia", "em_secid": "100.HSI", "source": "index_global"},
    {"ts_code": "N225", "name": "日经225", "market": "JP", "region": "asia", "em_secid": "100.N225", "source": "index_global"},
    {"ts_code": "KS11", "name": "韩国KOSPI", "market": "KR", "region": "asia", "em_secid": "100.KS11", "source": "index_global"},
    {"ts_code": "DJI", "name": "道琼斯", "market": "US", "region": "americas", "em_secid": "100.DJIA", "source": "index_global"},
    {"ts_code": "SPX", "name": "标普500", "market": "US", "region": "americas", "em_secid": "100.SPX", "source": "index_global"},
    {"ts_code": "IXIC", "name": "纳斯达克", "market": "US", "region": "americas", "em_secid": "100.NDX", "source": "index_global"},
]


def _today_sh() -> date:
    return datetime.now(_SH).date()


def _f(v: Any) -> float | None:
    """tushare 返回 NaN 表示缺值。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _d(v: str) -> date:
    return datetime.strptime(str(v), "%Y%m%d").date()


def _map_index_global_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": row["ts_code"],
        "trade_date": _d(row["trade_date"]),
        "open": _f(row.get("open")),
        "high": _f(row.get("high")),
        "low": _f(row.get("low")),
        "close": _f(row.get("close")),
        "volume": _f(row.get("vol")),
    }


def _get_tushare():
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    return get_tushare_client()


async def ingest_global_index_daily(db: AsyncSession, lookback_days: int = 14) -> dict[str, int]:
    """全球指数 + A股三大指数近 N 日日线 → index_dailies（幂等 upsert）。"""
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    client = _get_tushare()
    start = (_today_sh() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        if g["source"] == "index_global":
            df = await client.fetch_index_global(g["ts_code"], start, end)
        else:
            df = await client.fetch_index_daily(g["ts_code"], start, end)
        for rec in df.to_dict("records"):
            mapped = _map_index_global_row(rec)
            rows.append(IndexDaily(**mapped, amount=None))
    upserted = await index_repo.upsert_index_dailies(db, rows)
    logger.info("ingest_global_index_daily lookback=%s upserted=%s", lookback_days, upserted)
    return {"upserted": upserted}


async def backfill_global_index_history(db: AsyncSession, years: int = 2) -> dict[str, int]:
    """一次性回补全球指数历史（供 spark30 与指数详情 K 线）。"""
    from app.models.index_daily import IndexDaily  # noqa: PLC0415

    client = _get_tushare()
    start = (_today_sh() - timedelta(days=365 * years)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    rows: list[IndexDaily] = []
    for g in GLOBAL_INDICES:
        if g["source"] == "index_global":
            df = await client.fetch_index_global(g["ts_code"], start, end)
        else:
            df = await client.fetch_index_daily(g["ts_code"], start, end)
        for rec in df.to_dict("records"):
            rows.append(IndexDaily(**_map_index_global_row(rec), amount=None))
    upserted = await index_repo.upsert_index_dailies(db, rows)
    return {"upserted": upserted}


async def _main() -> None:
    from app.core.database import async_session_factory  # noqa: PLC0415

    args = sys.argv[1:]
    job = args[0] if args else ""
    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await backfill_global_index_history(db, years=int(args[1]) if len(args) > 1 else 2)
        else:
            raise SystemExit(f"unknown job: {job}; available: global_index_daily, backfill_global_index")
        await db.commit()
    print(job, "->", result)


if __name__ == "__main__":
    asyncio.run(_main())
```

（实现时注意：`IndexDaily` 列名以 Step 1 读到的实际模型为准，例如无 `amount` 列则去掉该参数；`fetch_index_daily` 参数名以 `tushare_client.py` 现有签名为准。）

- [ ] **Step 6: 测试通过** — `uv run pytest tests/test_market_data_mapping.py -q` → 3 passed；`ruff check . && mypy app`。
- [ ] **Step 7: 调度接线** — `jobs.py` 追加：

```python
async def global_index_daily_job() -> None:
    """全球+A股指数日线刷新（每日 17:30，覆盖美盘前一日与亚欧当日）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    logger.info("Global index daily job triggered")
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_global_index_daily(db)
            await db.commit()
        logger.info("Global index daily done: %s", result)
    except Exception:
        logger.exception("Global index daily job failed")
```

`runner.py` 追加注册（模仿 `industry_metrics_refresh` 块）：

```python
    scheduler.add_job(
        global_index_daily_job,
        CronTrigger(hour=17, minute=30, timezone="Asia/Shanghai"),
        id="global_index_daily",
        name="Global index daily refresh",
        replace_existing=True,
    )
```

- [ ] **Step 8: 活栈验证** — `docker compose up -d --build api worker scheduler`，然后 `docker exec backend_api python -m app.services.market_data_service backfill_global_index 2`，随后 `docker exec postgres psql -U stock_user -d stock_bot -c "SELECT ts_code, count(*), max(trade_date) FROM index_dailies WHERE ts_code IN ('N225','KS11','HSI','DJI','SPX','IXIC') GROUP BY ts_code;"` → 6 行、N225 约 400+、max≈昨日。
- [ ] **Step 9: Commit** — `git commit -m "feat(market-data): 全球指数日线采集（TuShare index_global/index_daily 复用 index_dailies）+ CLI 入口 + 17:30 调度"`

---

### Task 4: `/market/global-indices` 卡片组装（实时快照 + spark30 + EOD 兜底）

**Files:**
- Modify: `backend/app/services/market_data_service.py`
- Create: `backend/app/schemas/market_data.py`、`backend/app/api/v1/market_data.py`
- Modify: `backend/app/api/v1/__init__.py`
- Test: `backend/tests/test_market_data_mapping.py`（追加）

**Interfaces:**
- Produces: `GET /api/v1/market/global-indices` → `list[GlobalIndexCardOut]`；`market_data_service.get_global_index_cards(cache: CacheClient | None) -> list[dict]`（元素含 `ts_code/name/market/region/price/change/pct_change/spark(list[float])/updated_at/source("realtime"|"eod")`，按 `GLOBAL_INDICES` 顺序）。

- [ ] **Step 1: 追加失败测试**

```python
@pytest.mark.asyncio
async def test_get_global_index_cards_merges_realtime_and_spark(monkeypatch):
    class _FakeCache:
        def __init__(self) -> None:
            self.store: dict = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ttl=None):
            self.store[key] = value

    async def fake_snapshot(secids):
        return [
            {"code": "N225", "name": "日经225", "price": 64214.48, "pct_change": -0.17, "change": -111.16},
            {"code": "KS11", "name": "韩国KOSPI", "price": None, "pct_change": None, "change": None},
        ]

    kline_calls: list = []

    async def fake_kline(db, ts_code):
        kline_calls.append(ts_code)
        return [
            type("R", (), {"trade_date": date(2026, 8, i + 1), "close": 60000.0 + i})()
            for i in range(35)
        ]

    monkeypatch.setattr(mds, "_get_eastmoney", lambda: type("C", (), {"fetch_index_snapshot": staticmethod(fake_snapshot)}))
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = await mds.get_global_index_cards(cache=_FakeCache())
    by_code = {c["ts_code"]: c for c in cards}
    assert len(cards) == 9
    assert by_code["N225"]["price"] == 64214.48 and by_code["N225"]["source"] == "realtime"
    assert len(by_code["N225"]["spark"]) == 30  # 35 行裁到 30
    # KS11 实时缺失 → 用日线最后一根 close 兜底
    assert by_code["KS11"]["price"] == 60034.0 and by_code["KS11"]["source"] == "eod"
    assert set(kline_calls) == {g["ts_code"] for g in mds.GLOBAL_INDICES}
```

（`fake_kline` 返回对象需带 `close` 属性；若 `index_repo.get_kline` 实际签名带 `start_date/end_date` 参数，monkeypatch 的 fake 同步加 `**kwargs`。）

- [ ] **Step 2: 确认失败** — `uv run pytest tests/test_market_data_mapping.py -q` → AttributeError: get_global_index_cards。

- [ ] **Step 3: 实现**（`market_data_service.py` 追加）

```python
GLOBAL_INDICES_CACHE_KEY = "market:global-indices"
GLOBAL_INDICES_TTL = 60


def _get_eastmoney():
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return get_eastmoney_client()


async def get_global_index_cards(cache: Any | None = None) -> list[dict[str, Any]]:
    """全球市场卡片：东财实时快照（60s 共享缓存）+ 近 30 日 spark + EOD 兜底。"""
    if cache is not None:
        cached = await cache.get(GLOBAL_INDICES_CACHE_KEY)
        if cached:
            return cached

    quotes: dict[str, dict[str, Any]] = {}
    try:
        em = _get_eastmoney()
        snap = await em.fetch_index_snapshot([g["em_secid"] for g in GLOBAL_INDICES])
        quotes = {q["code"]: q for q in snap if q.get("code")}
    except Exception:
        logger.warning("global index snapshot fetch failed, falling back to EOD", exc_info=True)

    cards: list[dict[str, Any]] = []
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        for g in GLOBAL_INDICES:
            spark: list[float] = []
            last_close: float | None = None
            try:
                rows = await index_repo.get_kline(db, g["ts_code"])
                spark = [float(r.close) for r in rows[-30:] if r.close is not None]
                last_close = spark[-1] if spark else None
            except Exception:
                logger.warning("spark fetch failed for %s", g["ts_code"], exc_info=True)

            q = quotes.get(_em_code(g["em_secid"]))
            now = datetime.now(_SH).isoformat(timespec="seconds")
            if q and q.get("price") is not None:
                cards.append({
                    "ts_code": g["ts_code"], "name": q.get("name") or g["name"],
                    "market": g["market"], "region": g["region"],
                    "price": q["price"], "change": q.get("change"), "pct_change": q.get("pct_change"),
                    "spark": spark, "updated_at": now, "source": "realtime",
                })
            else:
                prev = spark[-2] if len(spark) >= 2 else None
                change = round(last_close - prev, 2) if (last_close is not None and prev is not None) else None
                pct = round(change / prev * 100, 2) if (change is not None and prev) else None
                cards.append({
                    "ts_code": g["ts_code"], "name": g["name"],
                    "market": g["market"], "region": g["region"],
                    "price": last_close, "change": change, "pct_change": pct,
                    "spark": spark, "updated_at": now, "source": "eod",
                })

    if cache is not None and any(c["price"] is not None for c in cards):
        await cache.set(GLOBAL_INDICES_CACHE_KEY, cards, ttl=GLOBAL_INDICES_TTL)
    return cards


def _em_code(secid: str) -> str:
    return secid.split(".", 1)[1]
```

- [ ] **Step 4: schema + 路由**（`app/schemas/market_data.py` 先建文件，本任务只放 GlobalIndexCardOut，后续任务追加）

```python
"""Market-data face response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GlobalIndexCardOut(BaseModel):
    ts_code: str
    name: str
    market: str
    region: str
    price: float | None = None
    change: float | None = None
    pct_change: float | None = None
    spark: list[float] = Field(default_factory=list)
    updated_at: datetime
    source: str
```

`app/api/v1/market_data.py`：

```python
"""Market-data face endpoints (global indices / moneyflow / dragon tiger / northbound / block / float / repurchase / announcements)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep
from app.schemas.market_data import GlobalIndexCardOut
from app.services import market_data_service

router = APIRouter(prefix="/market", tags=["market-data"])


@router.get("/global-indices", response_model=list[GlobalIndexCardOut])
async def get_global_indices() -> list[GlobalIndexCardOut]:
    cards = await market_data_service.get_global_index_cards()
    return [GlobalIndexCardOut(**c) for c in cards]
```

（注意：`get_global_index_cards` 内部不注入 cache 时端点可自管缓存——直接传 `cache` 需要 `CacheDep`；上面为简化版。实现时改为 `async def get_global_indices(cache: CacheDep) -> ...: cards = await market_data_service.get_global_index_cards(cache)`。）

`app/api/v1/__init__.py` 追加：

```python
from app.api.v1 import market_data  # noqa: E402  (与既有 import 区并列)

router.include_router(market_data.router, prefix="/market", tags=["market-data"])
```

（照既有 include 排版合并；`market.router` 已占 `/market` 前缀，两者共存无冲突。）

- [ ] **Step 5: 测试 + 活体验证** — `uv run pytest tests/test_market_data_mapping.py -q` 全绿；`docker compose up -d --build api` 后 `curl -s localhost:8000/api/v1/market/global-indices | python3 -m json.tool | head -40` → 9 张卡、N225 有 price、spark 数组非空。
- [ ] **Step 6: Commit** — `git commit -m "feat(market-data): GET /market/global-indices（实时快照+30日spark+EOD兜底，60s共享缓存）"`

---

### Task 5: 板块资金流（盘中轮询 ingest + 读取端点）

**Files:**
- Modify: `backend/app/services/market_data_service.py`、`backend/app/schemas/market_data.py`、`backend/app/api/v1/market_data.py`
- Create: `backend/app/repositories/market_data_repo.py`
- Modify: `backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`、`market_data_service.py` 的 `_main`
- Test: `backend/tests/test_market_data_mapping.py`（追加）、新建 `backend/tests/test_market_data_repo.py`

**Interfaces:**
- Produces: `market_data_repo.upsert_sector_moneyflow(db, trade_date, dimension, rows: list[dict]) -> int`；`market_data_repo.list_sector_moneyflow(db, trade_date, dimension, limit) -> list[SectorMoneyflowSnapshot]`（按 main_net_inflow DESC NULLS LAST）；service `ingest_sector_moneyflow(db) -> dict`（拉 industry+concept 两维）、`get_sector_moneyflow(cache, dimension, limit=15) -> list[dict]`（当日，Redis `market:sector-moneyflow:{dim}` TTL 60）；`GET /api/v1/market/sector-moneyflow?dimension=industry|concept&limit=15` → `list[SectorMoneyflowOut]`。

- [ ] **Step 1: 追加失败测试**

```python
@pytest.mark.asyncio
async def test_ingest_sector_moneyflow_uses_today_and_both_dims(monkeypatch):
    fetched: list[str] = []

    async def fake_flow(dimension):
        fetched.append(dimension)
        if dimension != "industry":
            return []
        return [{"board_code": "BK1203", "board_name": "非银金融", "pct_change": 0.28,
                 "main_net_inflow": 2151238400.0, "super_large_net": 1925688320.0,
                 "large_net": 225550080.0, "up_count": 48, "down_count": 26, "main_net_ratio": 4.15}]

    upserts: list = []

    async def fake_upsert(db, trade_date, dimension, rows):
        upserts.append((trade_date, dimension, rows))
        return len(rows)

    monkeypatch.setattr(mds, "_get_eastmoney", lambda: type("C", (), {"fetch_sector_moneyflow": staticmethod(fake_flow)}))
    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", fake_upsert)

    result = await mds.ingest_sector_moneyflow(db=None)
    assert result == {"industry": 1, "concept": 0}
    assert set(fetched) == {"industry", "concept"}
    assert upserts[0][0] == mds._today_sh() and upserts[0][1] == "industry"
```

- [ ] **Step 2: 确认失败** → AttributeError。
- [ ] **Step 3: 实现 repo**（`market_data_repo.py` 新建，模块函数风格照 `quote_repo.py`）

```python
"""Repositories for market_data face tables."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import SectorMoneyflowSnapshot


async def upsert_sector_moneyflow(
    db: AsyncSession, trade_date: date, dimension: str, rows: list[dict[str, Any]]
) -> int:
    """当日快照幂等覆盖（盘中每次轮询 upsert）。"""
    if not rows:
        return 0
    values = [
        {
            "trade_date": trade_date,
            "dimension": dimension,
            "board_code": r["board_code"],
            "board_name": r.get("board_name"),
            "pct_change": r.get("pct_change"),
            "main_net_inflow": r.get("main_net_inflow"),
            "super_large_net": r.get("super_large_net"),
            "large_net": r.get("large_net"),
            "main_net_ratio": r.get("main_net_ratio"),
            "up_count": r.get("up_count"),
            "down_count": r.get("down_count"),
        }
        for r in rows
    ]
    stmt = (
        pg_insert(SectorMoneyflowSnapshot)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_sector_moneyflow_dim_code_date",
            set_={
                "board_name": pg_insert(SectorMoneyflowSnapshot).excluded.board_name,
                "pct_change": pg_insert(SectorMoneyflowSnapshot).excluded.pct_change,
                "main_net_inflow": pg_insert(SectorMoneyflowSnapshot).excluded.main_net_inflow,
                "super_large_net": pg_insert(SectorMoneyflowSnapshot).excluded.super_large_net,
                "large_net": pg_insert(SectorMoneyflowSnapshot).excluded.large_net,
                "main_net_ratio": pg_insert(SectorMoneyflowSnapshot).excluded.main_net_ratio,
                "up_count": pg_insert(SectorMoneyflowSnapshot).excluded.up_count,
                "down_count": pg_insert(SectorMoneyflowSnapshot).excluded.down_count,
                "updated_at": None,
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
```

（`"updated_at": None` 会置空，改为省略该键让 onupdate 生效——实现时删掉这行，保留其余列。）

读取函数：

```python
async def list_sector_moneyflow(
    db: AsyncSession, trade_date: date, dimension: str, limit: int = 15
) -> list[SectorMoneyflowSnapshot]:
    stmt = (
        select(SectorMoneyflowSnapshot)
        .where(
            SectorMoneyflowSnapshot.trade_date == trade_date,
            SectorMoneyflowSnapshot.dimension == dimension,
        )
        .order_by(desc(SectorMoneyflowSnapshot.main_net_inflow))
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())
```

- [ ] **Step 4: 实现 service + 端点 + 调度 + CLI**

service 追加：

```python
from app.repositories import market_data_repo  # noqa: E402 (文件顶部 import 区)

SECTOR_MONEYFLOW_CACHE_KEY = "market:sector-moneyflow:{dimension}"
SECTOR_MONEYFLOW_TTL = 60


async def ingest_sector_moneyflow(db: AsyncSession) -> dict[str, int]:
    """盘中轮询：industry/concept 两维当日快照 upsert。"""
    em = _get_eastmoney()
    today = _today_sh()
    result: dict[str, int] = {}
    for dimension in ("industry", "concept"):
        rows = await em.fetch_sector_moneyflow(dimension)
        result[dimension] = await market_data_repo.upsert_sector_moneyflow(db, today, dimension, rows)
    logger.info("ingest_sector_moneyflow %s", result)
    return result


async def get_sector_moneyflow(cache: Any | None, dimension: str = "industry", limit: int = 15) -> list[dict[str, Any]]:
    key = SECTOR_MONEYFLOW_CACHE_KEY.format(dimension=dimension)
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            return cached
    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for snap in await market_data_repo.list_sector_moneyflow(db, _today_sh(), dimension, limit):
            rows.append({
                "board_code": snap.board_code, "board_name": snap.board_name,
                "pct_change": snap.pct_change, "main_net_inflow": snap.main_net_inflow,
                "super_large_net": snap.super_large_net, "large_net": snap.large_net,
                "main_net_ratio": snap.main_net_ratio, "up_count": snap.up_count, "down_count": snap.down_count,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=SECTOR_MONEYFLOW_TTL)
    return rows
```

`_main` 追加分支 `elif job == "sector_moneyflow": result = await ingest_sector_moneyflow(db)`。

schema 追加：

```python
class SectorMoneyflowOut(BaseModel):
    board_code: str
    board_name: str | None = None
    pct_change: float | None = None
    main_net_inflow: float | None = None  # 元
    super_large_net: float | None = None  # 元
    large_net: float | None = None  # 元
    main_net_ratio: float | None = None  # %
    up_count: int | None = None
    down_count: int | None = None
```

路由追加：

```python
@router.get("/sector-moneyflow", response_model=list[SectorMoneyflowOut])
async def get_sector_moneyflow_endpoint(
    cache: CacheDep,
    dimension: Literal["industry", "concept"] = "industry",
    limit: int = Query(default=15, ge=1, le=100),
) -> list[SectorMoneyflowOut]:
    rows = await market_data_service.get_sector_moneyflow(cache, dimension, limit)
    return [SectorMoneyflowOut(**r) for r in rows]
```

jobs.py 追加（复用 `_is_workday`/`_in_trading_hours` 守卫，函数名以 jobs.py 实际为准）：

```python
async def sector_moneyflow_job() -> None:
    """板块资金流盘中轮询（交易日 9:00-15:55 每 5 分钟，job 内交易时段守卫）。"""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import market_data_service  # noqa: PLC0415

    if not _is_workday() or not _in_trading_hours():
        return
    try:
        async with async_session_factory() as db:
            result = await market_data_service.ingest_sector_moneyflow(db)
            await db.commit()
        logger.info("Sector moneyflow poll done: %s", result)
    except Exception:
        logger.exception("Sector moneyflow poll failed")
```

runner.py 注册：

```python
    scheduler.add_job(
        sector_moneyflow_job,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone="Asia/Shanghai"),
        id="sector_moneyflow_poll",
        name="Sector moneyflow intraday poll",
        replace_existing=True,
    )
```

- [ ] **Step 5: 验证** — 单测全绿 + ruff/mypy；`docker compose up -d --build api scheduler`；`docker exec backend_api python -m app.services.market_data_service sector_moneyflow` → `{'industry': 100, 'concept': 100}`（数量可能略少）；`curl -s "localhost:8000/api/v1/market/sector-moneyflow?dimension=industry&limit=5"` → 非银金融等，金额 ~2e9 量级（元）。
- [ ] **Step 6: Commit** — `git commit -m "feat(market-data): 板块资金流盘中采集与读取端点（东财行业/概念，5分钟轮询）"`

---

### Task 6: 北向资金（盘后净流入序列）

**Files:**
- Modify: `market_data_service.py`、`market_data_repo.py`、`schemas/market_data.py`、`api/v1/market_data.py`、`scheduler/jobs.py`、`scheduler/runner.py`、`_main`
- Test: `tests/test_market_data_mapping.py`（追加）

**Interfaces:**
- Produces: `market_data_repo.upsert_northbound(db, rows: list[dict]) -> int`（rows 元素 `{trade_date, net_amount}` 万元，ON CONFLICT uq_northbound_date DO UPDATE net_amount）；`list_northbound(db, days) -> list[NorthboundDaily]` 升序；service `ingest_northbound(db, days=30) -> dict`、`get_northbound_series(cache, days=30) -> list[dict]`（`{date, net_amount}` 万元，Redis `market:northbound:{days}` TTL 300）；`GET /api/v1/market/northbound?days=30` → `list[NorthboundPointOut]`。

- [ ] **Step 1: 追加失败测试**

```python
def test_map_hsgt_rows_string_to_float():
    df = mds.pd.DataFrame([
        {"trade_date": "20260902", "north_money": "244809.28"},
        {"trade_date": "20260901", "north_money": "273259.26"},
    ])
    rows = mds._map_hsgt_rows(df)
    assert rows == [
        {"trade_date": date(2026, 9, 2), "net_amount": 244809.28},
        {"trade_date": date(2026, 9, 1), "net_amount": 273259.26},
    ]
```

- [ ] **Step 2: 确认失败** → AttributeError。

- [ ] **Step 3: 实现**（要点代码）

service：

```python
def _map_hsgt_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        raw = rec.get("north_money")
        net = float(raw) if raw not in (None, "", "nan") else None
        rows.append({"trade_date": _d(rec["trade_date"]), "net_amount": net})
    return rows


async def ingest_northbound(db: AsyncSession, days: int = 30) -> dict[str, int]:
    client = _get_tushare()
    start = (_today_sh() - timedelta(days=days)).strftime("%Y%m%d")
    end = _today_sh().strftime("%Y%m%d")
    df = await client.fetch_moneyflow_hsgt(start_date=start, end_date=end)
    upserted = await market_data_repo.upsert_northbound(db, _map_hsgt_rows(df))
    logger.info("ingest_northbound upserted=%s", upserted)
    return {"upserted": upserted}


NORTHBOUND_CACHE_KEY = "market:northbound:{days}"
NORTHBOUND_TTL = 300


async def get_northbound_series(cache: Any | None, days: int = 30) -> list[dict[str, Any]]:
    key = NORTHBOUND_CACHE_KEY.format(days=days)
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            return cached
    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for n in await market_data_repo.list_northbound(db, days):
            rows.append({"date": n.trade_date.isoformat(), "net_amount": n.net_amount})
    if cache is not None and rows:
        await cache.set(key, rows, ttl=NORTHBOUND_TTL)
    return rows
```

repo（同文件追加，模式同 Task5）：

```python
from app.models.market_data import NorthboundDaily


async def upsert_northbound(db: AsyncSession, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = (
        pg_insert(NorthboundDaily)
        .values([{**r, "source": "tushare:moneyflow_hsgt"} for r in rows])
        .on_conflict_do_update(
            constraint="uq_northbound_date",
            set_={"net_amount": pg_insert(NorthboundDaily).excluded.net_amount},
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount


async def list_northbound(db: AsyncSession, days: int) -> list[NorthboundDaily]:
    cutoff = datetime.now().date() - timedelta(days=days)  # repo 顶部补 from datetime import datetime, timedelta
    stmt = select(NorthboundDaily).where(NorthboundDaily.trade_date >= cutoff).order_by(NorthboundDaily.trade_date)
    return list((await db.execute(stmt)).scalars().all())
```

schema/路由/jobs/`_main` 分支 `northbound`：调度 `CronTrigger(day_of_week="mon-fri", hour=16, minute=10)`，id `northbound_daily`。端点：

```python
class NorthboundPointOut(BaseModel):
    date: str
    net_amount: float | None = None  # 万元


@router.get("/northbound", response_model=list[NorthboundPointOut])
async def get_northbound(cache: CacheDep, days: int = Query(default=30, ge=1, le=180)) -> list[NorthboundPointOut]:
    rows = await market_data_service.get_northbound_series(cache, days)
    return [NorthboundPointOut(**r) for r in rows]
```

- [ ] **Step 4: 验证** — 单测绿；`docker compose up -d --build api scheduler`；`docker exec backend_api python -m app.services.market_data_service northbound`；`curl -s "localhost:8000/api/v1/market/northbound?days=30" | head -c 300` → `[{"date":"2026-08-...","net_amount":244809.28},...]`。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 北向资金盘后净流入序列（moneyflow_hsgt，16:10 调度）"`

---

### Task 7: 龙虎榜 + 大宗交易

**Files:**
- Modify: 同 Task 6 的七个文件
- Test: `tests/test_market_data_mapping.py`（追加）

**Interfaces:**
- Produces:
  - `ingest_dragon_tiger(db, trade_date: date | None = None) -> dict`（None=今日上海日期）；`get_dragon_tiger(cache, date_iso | None, limit=15)`（None→表内最大交易日）；`GET /market/dragon-tiger?date=&limit=`
  - `ingest_block_trades(db, trade_date=None)`；`get_block_trades(cache, date_iso|None, symbol|None, limit=15)`（symbol 为 6 位代码，过滤 `ts_code LIKE '{symbol}.%'`，并 LEFT JOIN `stocks` 取 name——join 键 `func.split_part(ts_code, '.', 1) == Stock.symbol`，stocks 表 symbol 全市场唯一）；`GET /market/block-trades?date=&symbol=&limit=`
  - 返回元素均带 `symbol`（split_part 结果）与 `name`（dragon 用 top_list 自带，block 用 join）。

- [ ] **Step 1: 追加失败测试**

```python
def test_map_top_list_rows():
    df = mds.pd.DataFrame([{
        "trade_date": "20260902", "ts_code": "000019.SZ", "name": "深粮控股", "close": 7.18,
        "pct_change": -9.3434, "turnover_rate": 14.8, "amount": 453755737.0, "l_sell": 112970021.2,
        "l_buy": 30878730.2, "l_amount": 143848751.4, "net_amount": -82091291.0, "net_rate": -18.09,
        "amount_rate": 31.7, "float_values": 3153460155.46,
        "reason": "日跌幅偏离值达到7%的前5只证券",
    }])
    rows = mds._map_top_list_rows(df)
    r = rows[0]
    assert r["ts_code"] == "000019.SZ" and r["symbol"] == "000019"
    assert r["trade_date"] == date(2026, 9, 2) and r["net_amount"] == -82091291.0
    assert r["reason"].startswith("日跌幅偏离值")


def test_map_block_trade_rows():
    df = mds.pd.DataFrame([{
        "ts_code": "000488.SZ", "trade_date": "20260902", "price": 1.88, "vol": 50.0,
        "amount": 94.0, "buyer": "机构专用", "seller": "机构专用",
    }])
    rows = mds._map_block_trade_rows(df)
    assert rows[0] == {"trade_date": date(2026, 9, 2), "ts_code": "000488.SZ", "symbol": "000488",
                       "price": 1.88, "volume": 50.0, "amount": 94.0, "buyer": "机构专用", "seller": "机构专用"}
```

- [ ] **Step 2: 确认失败** → 两个 `_map_*` 不存在。

- [ ] **Step 3: 实现**（service 映射 + ingest + 读取；repo `upsert_dragon_tiger`（constraint `uq_dragon_tiger_date_code_reason`，set 全部行情列）、`list_dragon_tiger(db, trade_date, limit)`、`upsert_block_trades`（constraint `uq_block_trades_dedupe` DO NOTHING——大宗行无稳定业务主键，重复采集直接跳过）、`list_block_trades(db, trade_date, symbol, limit)`（symbol 过滤 + LEFT JOIN stocks 取 name，返回 dict 列表）。）

service 映射函数：

```python
def _map_top_list_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "trade_date": _d(rec["trade_date"]), "ts_code": rec["ts_code"], "symbol": rec["ts_code"].split(".")[0],
            "name": rec.get("name"), "close": _f(rec.get("close")), "pct_change": _f(rec.get("pct_change")),
            "turnover_rate": _f(rec.get("turnover_rate")), "amount": _f(rec.get("amount")),
            "l_buy": _f(rec.get("l_buy")), "l_sell": _f(rec.get("l_sell")), "l_amount": _f(rec.get("l_amount")),
            "net_amount": _f(rec.get("net_amount")), "net_rate": _f(rec.get("net_rate")),
            "amount_rate": _f(rec.get("amount_rate")), "float_values": _f(rec.get("float_values")),
            "reason": str(rec.get("reason") or ""),
        })
    return rows


def _map_block_trade_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        rows.append({
            "trade_date": _d(rec["trade_date"]), "ts_code": rec["ts_code"], "symbol": rec["ts_code"].split(".")[0],
            "price": _f(rec.get("price")), "volume": _f(rec.get("vol")), "amount": _f(rec.get("amount")),
            "buyer": rec.get("buyer"), "seller": rec.get("seller"),
        })
    return rows
```

ingest/读取函数骨架（照 Task 6 模式；dragon `trade_date or _today_sh()`；读取端点 `date` 参数 `datetime.fromisoformat` 解析可选；dragon 读取 Redis key `market:dragon-tiger:{date}` TTL 300；block `market:block-trades:{date}:{symbol}` TTL 300）。repo `list_block_trades` 的 join 版：

```python
from sqlalchemy import literal_column, and_  # 按需
from app.models.stock import Stock


async def list_block_trades(
    db: AsyncSession, trade_date: date, symbol: str | None, limit: int = 15
) -> list[dict]:
    from sqlalchemy import func, literal

    sym = func.split_part(BlockTrade.ts_code, ".", 1)
    stmt = (
        select(BlockTrade, Stock.name.label("stock_name"))
        .outerjoin(Stock, sym == Stock.symbol)
        .where(BlockTrade.trade_date == trade_date)
    )
    if symbol:
        stmt = stmt.where(sym == symbol)
    stmt = stmt.order_by(BlockTrade.amount.desc().nulls_last()).limit(limit)
    rows: list[dict] = []
    for trade, stock_name in (await db.execute(stmt)).all():
        rows.append({
            "trade_date": trade.trade_date.isoformat(), "ts_code": trade.ts_code,
            "symbol": trade.ts_code.split(".")[0], "name": stock_name,
            "price": trade.price, "volume": trade.volume, "amount": trade.amount,
            "buyer": trade.buyer, "seller": trade.seller,
        })
    return rows
```

（`literal` import 不需要则删；dragon 读取直接 select 模型转 dict，symbol 同法 split。）

调度：`dragon_tiger_daily` mon-fri 18:00；`block_trade_daily` mon-fri 17:00。`_main` 分支 `dragon_tiger [yyyymmdd]`、`block_trades [yyyymmdd]`（带参时用 `_d()` 解析覆盖）。

schema：

```python
class DragonTigerOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    close: float | None = None
    pct_change: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None      # 元
    l_buy: float | None = None       # 元
    l_sell: float | None = None      # 元
    l_amount: float | None = None    # 元
    net_amount: float | None = None  # 元
    reason: str


class BlockTradeOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    price: float | None = None   # 元
    volume: float | None = None  # 万股
    amount: float | None = None  # 万元
    buyer: str | None = None
    seller: str | None = None
```

- [ ] **Step 4: 验证** — 单测绿 + ruff/mypy；活栈：`docker exec backend_api python -m app.services.market_data_service dragon_tiger 20260902` 与 `block_trades 20260902`；curl 两端点（dragon-tiger 默认最新日）返回 77/85 行级别数据。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 龙虎榜与大宗交易采集/读取（top_list 18:00、block_trade 17:00）"`

---

### Task 8: 解禁 + 回购

**Files:** 同 Task 7 模式（service/repo/schema/router/jobs/runner/_main/tests）
**Interfaces:**
- `ingest_share_floats(db, days=7)`（按 ann_date 窗口拉 `share_float`）；`get_share_floats(cache, start_iso|None, end_iso|None, symbol|None, limit=30)`（默认窗口=近 30 天未来亦含——按 `float_date` 过滤，无行时返回空）；`GET /market/share-floats?start=&end=&symbol=&limit=`
- `ingest_repurchases(db, days=7)`（`repurchase` 接口）；`get_repurchases(cache, start_iso|None, end_iso|None, symbol|None, limit=30)`；`GET /market/repurchases?start=&end=&symbol=&limit=`
- 两者读取均 join stocks 取 name（同 Task 7 的 split_part join），symbol 过滤同法。

- [ ] **Step 1: 失败测试**（照 Task 7 结构）

```python
def test_map_share_float_rows():
    df = mds.pd.DataFrame([{
        "ts_code": "002747.SZ", "ann_date": "20260901", "float_date": "20260902",
        "float_share": 60000.0, "float_ratio": 0.0069, "holder_name": "朱樟兴",
        "share_type": "股权激励限售流通",
    }])
    rows = mds._map_share_float_rows(df)
    r = rows[0]
    assert r["float_date"] == date(2026, 9, 2) and r["ann_date"] == date(2026, 9, 1)
    assert r["float_share"] == 60000.0 and r["symbol"] == "002747"


def test_map_repurchase_rows_nan_exp_date():
    df = mds.pd.DataFrame([{
        "ts_code": "002120.SZ", "ann_date": "20260902", "end_date": "20260831", "proc": "完成",
        "exp_date": float("nan"), "vol": 12074600.0, "amount": 87945900.0,
        "high_limit": 8.05, "low_limit": 6.17,
    }])
    rows = mds._map_repurchase_rows(df)
    r = rows[0]
    assert r["proc"] == "完成" and r["exp_date"] is None and r["amount"] == 87945900.0
    assert r["ann_date"] == date(2026, 9, 2) and r["end_date"] == date(2026, 8, 31)
```

- [ ] **Step 2: 确认失败** → 映射函数不存在。
- [ ] **Step 3: 实现** — 映射（`_f` 处理 NaN；日期字段 `_d`，可空日期先判 `pd.isna`）；repo：`upsert_share_floats`（constraint `uq_share_floats_dedupe` DO NOTHING）、`list_share_floats(db, start, end, symbol, limit)`（按 float_date 过滤+倒序，join stocks）、`upsert_repurchases`（constraint `uq_stock_repurchases_dedupe`，set vol/amount/proc 相关列）、`list_repurchases`（按 ann_date 倒序，join stocks）。service ingest 窗口 `(_today_sh()-timedelta(days=days)).strftime("%Y%m%d")`。调度 `share_float_daily` mon-fri 17:30、`repurchase_daily` mon-fri 17:40。`_main` 分支 `share_floats`、`repurchases`。schema：

```python
class ShareFloatOut(BaseModel):
    ann_date: str | None = None
    float_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    float_share: float | None = None  # 万股
    float_ratio: float | None = None  # %
    holder_name: str | None = None
    share_type: str | None = None


class RepurchaseOut(BaseModel):
    ann_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    proc: str
    end_date: str | None = None
    exp_date: str | None = None
    vol: float | None = None    # 股
    amount: float | None = None  # 元
```

- [ ] **Step 4: 验证** — 单测绿；活栈 `docker exec backend_api python -m app.services.market_data_service share_floats` / `repurchases`；curl 两端点各返回行数据（share_floats 近期窗口可能有数据，无数据换 `start=2026-08-01`）。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 解禁与回购采集/读取（share_float 17:30、repurchase 17:40）"`

---

### Task 9: 巨潮公告（CninfoClient + announcement_service + 端点 + 轮询）

**Files:**
- Create: `backend/app/core/providers/cninfo_client.py`、`backend/app/services/announcement_service.py`
- Modify: `market_data_repo.py`、`schemas/market_data.py`、`api/v1/market_data.py`、`scheduler/jobs.py`、`scheduler/runner.py`
- Test: `backend/tests/test_announcement.py`

**Interfaces:**
- Produces: `CninfoClient.fetch_announcements(se_date: str, category_key: Literal["report","event"], page_size=30, max_pages=5) -> list[dict]`（元素 `{announcement_id, sec_code, sec_name, title, announce_time: datetime, category, pdf_url}`，`_post_json` 可覆写）；service `ingest_announcements(db, days=3) -> dict`（两类目各拉一次、announcement_id 内存去重、repo ON CONFLICT DO NOTHING）、`get_announcements(cache, symbol|None, limit=30)`；`GET /market/announcements?symbol=&limit=`。

- [ ] **Step 1: 失败测试**

```python
"""公告：cninfo 映射 + service 去重（monkeypatch）。"""

from datetime import datetime

import pytest

from app.services import announcement_service as anns


def test_client_maps_cninfo_record():
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()
    canned = {
        "announcements": [
            {
                "announcementId": "1225542181", "secCode": "002762", "secName": "金发拉比",
                "announcementTitle": "<em>金发拉比</em>2026年半年度报告",
                "announcementTime": 1788278400000,
                "adjunctUrl": "finalpage/2026-09-02/1225542181.PDF",
            }
        ]
    }

    class _Fake:
        async def _post_json(self, url, data):
            return canned

    client._post_json = _Fake()._post_json  # type: ignore[method-assign]
    rows = client.fetch_announcements_sync_shape() if False else None  # 占位防误用，见下


async def test_client_fetch_maps(monkeypatch):
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()

    async def fake_post(url, data):
        assert data["category"].startswith("category_yjdbg_szsh;")
        return {"announcements": [
            {"announcementId": "1225542181", "secCode": "002762", "secName": "金发拉比",
             "announcementTitle": "<em>金发拉比</em>2026年半年度报告",
             "announcementTime": 1788278400000,
             "adjunctUrl": "finalpage/2026-09-02/1225542181.PDF"}
        ]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    rows = await client.fetch_announcements("2026-08-20~2026-09-03", "report", page_size=30, max_pages=1)
    assert rows == [{
        "announcement_id": "1225542181", "sec_code": "002762", "sec_name": "金发拉比",
        "title": "金发拉比2026年半年度报告",
        "announce_time": datetime.fromtimestamp(1788278400000 / 1000),
        "category": "report",
        "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-09-02/1225542181.PDF",
    }]


@pytest.mark.asyncio
async def test_ingest_announcements_dedupes(monkeypatch):
    seen_pages: list[list] = []

    async def fake_fetch(self, se_date, category_key, page_size=30, max_pages=5):
        if category_key == "report":
            return [
                {"announcement_id": "A1", "sec_code": "000001", "sec_name": "X",
                 "title": "t1", "announce_time": datetime(2026, 9, 2, 8, 0),
                 "category": "report", "pdf_url": None},
                {"announcement_id": "A1", "sec_code": "000001", "sec_name": "X",
                 "title": "t1", "announce_time": datetime(2026, 9, 2, 8, 0),
                 "category": "report", "pdf_url": None},
            ]
        return []

    inserted: list = []

    async def fake_upsert(db, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(anns.CninfoClient, "fetch_announcements", fake_fetch)
    monkeypatch.setattr(anns.market_data_repo, "upsert_announcements", fake_upsert)

    result = await anns.ingest_announcements(db=None, days=3)
    assert result == {"report": 1, "event": 0}
    assert len(inserted) == 1
```

（第一条 `test_client_maps_cninfo_record` 是草稿——**删掉它**，最终文件只保留 `test_client_fetch_maps`（补 `@pytest.mark.asyncio`）与 `test_ingest_announcements_dedupes`。）

- [ ] **Step 2: 确认失败** → 模块不存在。

- [ ] **Step 3: 实现 CninfoClient**

```python
"""Cninfo (巨潮资讯) announcement search client."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

import httpx

_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_PDF_BASE = "http://static.cninfo.com.cn/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

CATEGORY_QUERY: dict[str, str] = {
    "report": "category_yjdbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_ndbg_szsh;"
              "category_yjygjxz_szsh;category_yjkb_szsh",
    "event": "category_zf_szsh;category_pgjz_szsh;category_gqfpxzcs_szsh;category_lr_gqbl_szsh",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _map_record(a: dict[str, Any], category: str) -> dict[str, Any]:
    title = a.get("announcementTitle") or ""
    return {
        "announcement_id": str(a["announcementId"]),
        "sec_code": a.get("secCode") or "",
        "sec_name": a.get("secName"),
        "title": _TAG_RE.sub("", title),
        "announce_time": datetime.fromtimestamp(int(a["announcementTime"]) / 1000),
        "category": category,
        "pdf_url": _PDF_BASE + a["adjunctUrl"] if a.get("adjunctUrl") else None,
    }


class CninfoClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _UA, "Referer": "http://www.cninfo.com.cn/"},
            timeout=httpx.Timeout(15.0),
        )

    async def _post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()

    async def fetch_announcements(
        self,
        se_date: str,
        category_key: Literal["report", "event"],
        page_size: int = 30,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            data = await self._post_json(
                _QUERY_URL,
                {
                    "pageNum": page,
                    "pageSize": page_size,
                    "column": "szse",
                    "tabName": "fulltext",
                    "seDate": se_date,
                    "category": CATEGORY_QUERY[category_key],
                    "isHLtitle": "true",
                },
            )
            anns = data.get("announcements") or []
            rows.extend(_map_record(a, category_key) for a in anns)
            if len(anns) < page_size:
                break
        return rows


_client: CninfoClient | None = None


def get_cninfo_client() -> CninfoClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = CninfoClient()
    return _client
```

`announcement_service.py`：

```python
"""公告快讯：巨潮采集（准实时积累）+ 读取。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import market_data_repo

logger = logging.getLogger(__name__)


def _se_date(days: int) -> str:
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    start = _today_sh() - timedelta(days=days)
    return f"{start.isoformat()}~{_today_sh().isoformat()}"


async def ingest_announcements(db: AsyncSession, days: int = 3) -> dict[str, int]:
    from app.core.providers.cninfo_client import get_cninfo_client  # noqa: PLC0415

    client = get_cninfo_client()
    se_date = _se_date(days)
    result: dict[str, int] = {}
    for category_key in ("report", "event"):
        rows = await client.fetch_announcements(se_date, category_key)  # type: ignore[arg-type]
        seen: set[str] = set()
        unique = [r for r in rows if not (r["announcement_id"] in seen or seen.add(r["announcement_id"]))]
        result[category_key] = await market_data_repo.upsert_announcements(db, unique)
        logger.info("ingest_announcements %s -> %s", category_key, result[category_key])
    return result


ANNOUNCEMENTS_CACHE_KEY = "market:announcements:{symbol}"
ANNOUNCEMENTS_TTL = 300


async def get_announcements(cache: Any | None, symbol: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    key = ANNOUNCEMENTS_CACHE_KEY.format(symbol=symbol or "all")
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            return cached
    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for a in await market_data_repo.list_announcements(db, symbol, limit):
            rows.append({
                "announcement_id": a.announcement_id, "sec_code": a.sec_code, "sec_name": a.sec_name,
                "title": a.title, "announce_time": a.announce_time.isoformat(),
                "category": a.category, "pdf_url": a.pdf_url,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=ANNOUNCEMENTS_TTL)
    return rows
```

repo 追加（DO NOTHING 幂等）：

```python
from app.models.market_data import Announcement


async def upsert_announcements(db: AsyncSession, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(Announcement).values(rows).on_conflict_do_nothing(constraint="uq_announcements_cninfo_id")
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount


async def list_announcements(db: AsyncSession, symbol: str | None, limit: int) -> list[Announcement]:
    stmt = select(Announcement).order_by(Announcement.announce_time.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Announcement.sec_code == symbol)
    return list((await db.execute(stmt)).scalars().all())
```

（注意 `list_announcements` 里 `limit` 要在 `where` 之后 `order_by` 前后均可，链式顺序：`select(...).where(...).order_by(...).limit(...)`——实现时按此顺序写。）

schema + 端点：

```python
class AnnouncementOut(BaseModel):
    announcement_id: str
    sec_code: str
    sec_name: str | None = None
    title: str
    announce_time: str
    category: str
    pdf_url: str | None = None


@router.get("/announcements", response_model=list[AnnouncementOut])
async def get_announcements_endpoint(
    cache: CacheDep, symbol: str | None = None, limit: int = Query(default=30, ge=1, le=100)
) -> list[AnnouncementOut]:
    from app.services import announcement_service  # noqa: PLC0415

    rows = await announcement_service.get_announcements(cache, symbol, limit)
    return [AnnouncementOut(**r) for r in rows]
```

jobs/runner：`announcements_poll` `CronTrigger(hour="8-22", minute="*/10", timezone="Asia/Shanghai")`，job 无交易时段守卫（公告发布含非交易日）。

- [ ] **Step 4: 验证** — `uv run pytest tests/test_announcement.py -q` 绿；活栈 `docker compose up -d --build api scheduler`，手动触发一次：`docker exec backend_api python -c "import asyncio;from app.core.database import async_session_factory;from app.services.announcement_service import ingest_announcements
async def m():
    async with async_session_factory() as db:
        print(await ingest_announcements(db, days=3)); await db.commit()
asyncio.run(m())"`（多行 `-c` 用 bash `$'...'` 或写临时文件方式执行，效果等价）；curl `/market/announcements?limit=5` → 金发拉比等条目、pdf_url 可打开。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 巨潮公告采集与快讯端点（财报+重大事项，10分钟轮询）"`

---

### Task 10: RabbitMQ Worker + 手动触发端点

**Files:**
- Modify: `backend/app/core/mq.py`、`backend/app/workers/runner.py`、`backend/app/api/v1/tasks.py`、`backend/app/schemas/task.py`（若请求体 schema 集中在此）
- Create: `backend/app/workers/market_data_worker.py`
- Test: `backend/tests/test_market_data_worker.py`

**Interfaces:**
- Produces: 队列 `market_data.fetch`；`MarketDataWorker.process(task_id, payload)` 按 `payload["type"]` 分发：`global_index_daily|backfill_global_index{years}|sector_moneyflow|northbound{days}|dragon_tiger{trade_date}|block_trades{trade_date}|share_floats{days}|repurchases{days}|announcements{days}`；`POST /api/v1/tasks/fetch-market-data` body `{"type": "<上述之一>", "params": {...}}` → 202 `TaskOut`。

- [ ] **Step 1: 失败测试**

```python
"""MarketDataWorker 分发单测。"""

from app.workers.market_data_worker import MarketDataWorker


async def test_worker_dispatches_northbound(monkeypatch):
    called: list = []

    async def fake_ingest(db, days=30):
        called.append(days)
        return {"upserted": 3}

    from app.services import market_data_service as mds

    monkeypatch.setattr(mds, "ingest_northbound", fake_ingest)
    worker = MarketDataWorker()
    result = await worker.process(task_id=None, payload={"type": "northbound", "days": 7})
    assert result["status"] == "completed" and result["type"] == "northbound"
    assert called == [7]


async def test_worker_unknown_type_fails_cleanly():
    worker = MarketDataWorker()
    result = await worker.process(task_id=None, payload={"type": "nope"})
    assert result["status"] == "failed"
```

- [ ] **Step 2: 确认失败** → 模块不存在。
- [ ] **Step 3: 实现**（worker；注意 process 内不开真 session 的路径——测试里 monkeypatch 掉 service 后 session 工厂照常开（连不上库会炸），因此 session 的开与commit 只在 handler 内做：把 `async with async_session_factory()` 移进每个 handler？不行，同样炸。**测试注入方案**：worker 的 `_session_factory` 为模块级变量 `async_session_factory`，测试 monkeypatch `market_data_worker.async_session_factory` 为返回 NullSession 的 async context manager。实现：

```python
"""Market-data ingestion worker (manual trigger via /tasks/fetch-market-data)."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from app.core.database import async_session_factory
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


def _opt_date(v: Any) -> date | None:
    from app.services.market_data_service import _d  # noqa: PLC0415

    return _d(str(v)) if v else None


async def _run(job: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import announcement_service, market_data_service  # noqa: PLC0415

    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await market_data_service.ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await market_data_service.backfill_global_index_history(
                db, years=int(params.get("years", 2))
            )
        elif job == "sector_moneyflow":
            result = await market_data_service.ingest_sector_moneyflow(db)
        elif job == "northbound":
            result = await market_data_service.ingest_northbound(db, days=int(params.get("days", 30)))
        elif job == "dragon_tiger":
            result = await market_data_service.ingest_dragon_tiger(db, trade_date=_opt_date(params.get("trade_date")))
        elif job == "block_trades":
            result = await market_data_service.ingest_block_trades(db, trade_date=_opt_date(params.get("trade_date")))
        elif job == "share_floats":
            result = await market_data_service.ingest_share_floats(db, days=int(params.get("days", 7)))
        elif job == "repurchases":
            result = await market_data_service.ingest_repurchases(db, days=int(params.get("days", 7)))
        elif job == "announcements":
            result = await announcement_service.ingest_announcements(db, days=int(params.get("days", 3)))
        else:
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        await db.commit()
    return {"status": "completed", "type": job, **result}


class MarketDataWorker(BaseWorker):
    """市场数据面采集任务（全球指数/资金流/北向/龙虎榜/大宗/解禁/回购/公告）。"""

    queue_key = "market_data.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        job = str(payload.get("type") or "")
        params = payload.get("params") or {}
        try:
            return await _run(job, {**payload, **params})
        except Exception as exc:  # noqa: BLE001
            logger.exception("market_data task %s failed", job)
            return {"status": "failed", "error": str(exc)}
```

（测试里 monkeypatch `market_data_worker.async_session_factory` 为 async context manager 返回 `None` db——service 被 patch 后不触 db，安全。payload 展开使 `days` 等参数既可放 payload 顶层也可放 params。）

`mq.py` QUEUES 追加 `"market_data.fetch": "stock_bot.market_data.fetch",`；`workers/runner.py` 实例化列表加 `MarketDataWorker()`。tasks 端点：先读 `app/api/v1/tasks.py` 既有 `fetch-securities` 处理函数与其请求 schema 写法，**逐行照抄结构**新增：

```python
@tasks_router.post("/fetch-market-data", status_code=202, response_model=TaskOut)
async def trigger_fetch_market_data(request: MarketDataFetchRequest) -> TaskOut:
    """手动触发市场数据采集（type 见 plans/2026-09-03-market-data-face.md Task 10）。"""
    payload = {"type": request.type, **(request.params or {})}
    return await task_service.dispatch_task(
        task_type="fetch_market_data",
        routing_key="market_data.fetch",
        payload=payload,
    )
```

（`task_service.dispatch_task` 的实际签名/参数名以既有 fetch-securities 端点调用为准对齐；`MarketDataFetchRequest` pydantic 模型：`type: str`、`params: dict[str, Any] | None = None`，放 `schemas/task.py` 并用 `Literal` 枚举 9 个 type。）

- [ ] **Step 4: 验证** — `uv run pytest tests/test_market_data_worker.py -q` 绿 + ruff/mypy；活栈 `docker compose up -d --build worker api`；`curl -s -X POST localhost:8000/api/v1/tasks/fetch-market-data -H 'Content-Type: application/json' -d '{"type":"northbound","params":{"days":30}}'` → 202 TaskOut；10s 后 `curl -s localhost:8000/api/v1/tasks/<task_id>`（按 tasks 路由实际查询路径）status=completed；`docker logs stock-bot-worker-1 --tail 20` 可见处理日志。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): market_data.fetch 队列与 Worker + POST /tasks/fetch-market-data 手动触发"`

---

### Task 11: 前端 API 层 `marketData.ts`

**Files:**
- Create: `frontend/src/shared/api/marketData.ts`

**Interfaces:**
- Produces（camelCase 映射，snake→camel 手写映射，参照 `market.ts` 风格）：类型 `GlobalIndexCard, SectorMoneyflowItem, NorthboundPoint, DragonTigerItem, BlockTradeItem, ShareFloatItem, RepurchaseItem, AnnouncementItem`；函数 `fetchGlobalIndices(), fetchSectorMoneyflow(dimension, limit=15), fetchNorthbound(days=30), fetchDragonTiger(limit=15), fetchBlockTrades(symbol?, limit=15), fetchShareFloats(symbol?, limit=30), fetchRepurchases(symbol?, limit=30), fetchAnnouncements(symbol?, limit=30)`。

- [ ] **Step 1: 实现**（完整文件）

```ts
import { apiGet } from "./client";

export interface GlobalIndexCard {
  tsCode: string;
  name: string;
  market: string;
  region: "asia" | "americas";
  price: number | null;
  change: number | null;
  pctChange: number | null;
  spark: number[];
  updatedAt: string;
  source: "realtime" | "eod";
}

export interface SectorMoneyflowItem {
  boardCode: string;
  boardName: string | null;
  pctChange: number | null;
  mainNetInflow: number | null; // 元
  superLargeNet: number | null;
  largeNet: number | null;
  mainNetRatio: number | null;
  upCount: number | null;
  downCount: number | null;
}

export interface NorthboundPoint {
  date: string;
  netAmount: number | null; // 万元
}

export interface DragonTigerItem {
  tradeDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  close: number | null;
  pctChange: number | null;
  turnoverRate: number | null;
  netAmount: number | null; // 元
  reason: string;
}

export interface BlockTradeItem {
  tradeDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  price: number | null; // 元
  volume: number | null; // 万股
  amount: number | null; // 万元
  buyer: string | null;
  seller: string | null;
}

export interface ShareFloatItem {
  annDate: string | null;
  floatDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  floatShare: number | null; // 万股
  floatRatio: number | null; // %
  holderName: string | null;
  shareType: string | null;
}

export interface RepurchaseItem {
  annDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  proc: string;
  endDate: string | null;
  vol: number | null; // 股
  amount: number | null; // 元
}

export interface AnnouncementItem {
  announcementId: string;
  secCode: string;
  secName: string | null;
  title: string;
  announceTime: string;
  category: "report" | "event";
  pdfUrl: string | null;
}

export function fetchGlobalIndices(): Promise<GlobalIndexCard[]> {
  return apiGet<GlobalIndexCard[]>("/api/v1/market/global-indices");
}

export function fetchSectorMoneyflow(dimension: "industry" | "concept", limit = 15): Promise<SectorMoneyflowItem[]> {
  return apiGet<SectorMoneyflowItem[]>("/api/v1/market/sector-moneyflow", { dimension, limit });
}

export function fetchNorthbound(days = 30): Promise<NorthboundPoint[]> {
  return apiGet<NorthboundPoint[]>("/api/v1/market/northbound", { days });
}

export function fetchDragonTiger(limit = 15): Promise<DragonTigerItem[]> {
  return apiGet<DragonTigerItem[]>("/api/v1/market/dragon-tiger", { limit });
}

export function fetchBlockTrades(symbol?: string, limit = 15): Promise<BlockTradeItem[]> {
  return apiGet<BlockTradeItem[]>("/api/v1/market/block-trades", { symbol, limit });
}

export function fetchShareFloats(symbol?: string, limit = 30): Promise<ShareFloatItem[]> {
  return apiGet<ShareFloatItem[]>("/api/v1/market/share-floats", { symbol, limit });
}

export function fetchRepurchases(symbol?: string, limit = 30): Promise<RepurchaseItem[]> {
  return apiGet<RepurchaseItem[]>("/api/v1/market/repurchases", { symbol, limit });
}

export function fetchAnnouncements(symbol?: string, limit = 30): Promise<AnnouncementItem[]> {
  return apiGet<AnnouncementItem[]>("/api/v1/market/announcements", { symbol, limit });
}
```

（注意：`apiGet` 的 params 需跳过 `undefined`——`client.ts` 已实现，直接传即可；后端返回 snake_case，**必须逐字段映射**为 camel（照 `market.ts` 的 map 函数写法补 map 层：`const mapCard = (b: any): GlobalIndexCard => ({...})`，每个 fetch 函数内 `apiGet<any[]>(...).then(rows => rows.map(mapX))`。实现时把上述直返改成显式映射版，杜绝 any 外漏。）

- [ ] **Step 2: 验证** — `cd frontend && npm run lint && npm run build`（TS 零错误）。
- [ ] **Step 3: Commit** — `git commit -m "feat(market-data): 前端市场数据 API 层（8 端点 camelCase 映射）"`

---

### Task 12: 全球市场区块（Tabs + 徽章卡 + sparkline）+ 指数详情全球支持

**Files:**
- Create: `frontend/src/features/market/components/GlobalMarketBoard.tsx`、`frontend/src/features/market/components/GlobalIndexCardView.tsx`
- Delete: `frontend/src/features/market/components/MarketOverview.tsx`、`frontend/src/features/market/components/IndexCard.tsx`（删前 grep 确认无其他引用）
- Modify: `frontend/src/features/market/components/index.ts`（barrel）、`frontend/src/pages/market/index.tsx`、`frontend/src/pages/index-detail/index.tsx`

**Interfaces:**
- Consumes: `fetchGlobalIndices`（Task 11）、`sparkOption`/`EChart`、`COLORS`、`ChangeText`。
- Produces: `<GlobalMarketBoard />`（自取数：`["global-indices"]` staleTime 60s + refetchInterval 60s）。

- [ ] **Step 1: GlobalIndexCardView.tsx**

```tsx
import { Card, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { EChart, sparkOption } from "@/shared/ui/EChart";
import { COLORS } from "@/app/theme";
import type { GlobalIndexCard } from "@/shared/api/marketData";

const MARKET_BADGE: Record<string, { label: string; color: string }> = {
  CN: { label: "CN", color: "#ef4444" },
  HK: { label: "HK", color: "#f59e0b" },
  JP: { label: "JP", color: "#1677ff" },
  KR: { label: "KR", color: "#6366f1" },
  US: { label: "US", color: "#0ea5e9" },
};

export function GlobalIndexCardView({ index }: { index: GlobalIndexCard }) {
  const navigate = useNavigate();
  const badge = MARKET_BADGE[index.market] ?? { label: index.market, color: COLORS.flat };
  const up = (index.pctChange ?? 0) > 0;
  const down = (index.pctChange ?? 0) < 0;
  const color = up ? COLORS.up : down ? COLORS.down : COLORS.flat;
  const sparkColor = index.spark.length > 1 ? ((index.spark[index.spark.length - 1] - index.spark[0]) >= 0 ? COLORS.up : COLORS.down) : COLORS.flat;

  return (
    <Card hoverable size="small" onClick={() => navigate(`/index/${index.tsCode}`)} styles={{ body: { padding: "12px 16px" } }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: "50%", fontSize: 10, fontWeight: 600,
              color: "#fff", backgroundColor: badge.color, flexShrink: 0,
            }}>{badge.label}</span>
            <span style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{index.name}</span>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 10, fontFamily: "monospace" }}>
            {index.market} {index.tsCode.split(".")[0]}
          </Typography.Text>
          <div style={{ fontSize: 20, fontWeight: 600, color, fontVariantNumeric: "tabular-nums", marginTop: 4 }}>
            {index.price == null ? "—" : index.price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: 12, color, fontVariantNumeric: "tabular-nums" }}>
            {index.change == null ? "—" : `${index.change > 0 ? "+" : ""}${index.change.toFixed(2)}`}
            {"  "}
            {index.pctChange == null ? "" : `${index.pctChange > 0 ? "+" : ""}${index.pctChange.toFixed(2)}%`}
          </div>
        </div>
        {index.spark.length > 2 && (
          <div style={{ width: 72, flexShrink: 0 }}>
            <EChart option={sparkOption(index.spark, sparkColor)} height={56} silent />
          </div>
        )}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: GlobalMarketBoard.tsx**

```tsx
import { Col, Row, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;

const REGIONS = [
  { key: "asia", label: "亚洲" },
  { key: "americas", label: "美洲" },
] as const;

export function GlobalMarketBoard() {
  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });

  const items = REGIONS.map((r) => ({
    key: r.key,
    label: r.label,
    children: (
      <Row gutter={[12, 12]}>
        {indices.filter((i) => i.region === r.key).map((i) => (
          <Col key={i.tsCode} xs={12} sm={8} xl={4}>
            <GlobalIndexCardView index={i} />
          </Col>
        ))}
      </Row>
    ),
  }));

  return (
    <Tabs
      defaultActiveKey="asia"
      items={isLoading ? [{ key: "asia", label: "亚洲", children: <Row gutter={[12, 12]}>{Array.from({ length: 6 }, (_, i) => (<Col key={i} xs={12} sm={8} xl={4}><Skeleton active paragraph={{ rows: 2 }} /></Col>))}</Row> }] : items}
    />
  );
}
```

- [ ] **Step 3: 接线与删除** — barrel `index.ts`：移除 `MarketOverview`/`IndexCard` 导出、加入 `GlobalMarketBoard`（`GlobalIndexCardView` 不进 barrel，内部引用）；`pages/market/index.tsx` 把 `<MarketOverview />` 换 `<GlobalMarketBoard />`；`grep -rn "MarketOverview\|IndexCard" frontend/src` 确认无残留引用后删除两个旧文件。`pages/index-detail/index.tsx`：在 `fetchMarketIndices` 查找失败的分支增加全球卡兜底——

```tsx
const { data: globalCards = [] } = useQuery({
  queryKey: ["global-indices"],
  queryFn: fetchGlobalIndices,
  staleTime: 60 * 1000,
});
// 原 const index = indices.find(...) 改为：
const found = indices.find((i) => i.tsCode === tsCode);
const index = found ?? (() => {
  const card = globalCards.find((c) => c.tsCode === tsCode);
  if (!card) return undefined;
  return {
    code: card.tsCode.split(".")[0],
    tsCode: card.tsCode,
    name: card.name,
    value: card.price ?? 0,
    change: card.change ?? 0,
    changePercent: card.pctChange ?? 0,
    exchange: "Global",
    asof: card.updatedAt,
  };
})();
```

（`MarketIndex` 字段名以 `shared/types/index.ts` 实际为准对齐；import `useQuery`/`fetchGlobalIndices` 按文件现有 import 风格追加。）

- [ ] **Step 4: 验证** — `npm run lint && npm run build`；活栈 `docker compose up -d --build frontend` 后打开 `/market`：亚洲 Tab 6 卡（上证/深成/创业板/恒生/日经/KOSPI），卡片有徽章与 sparkline；切美洲 Tab 3 卡；点日经225 → `/index/N225` 详情页 K 线渲染（数据来自 Task 3 回补）。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 全球市场区块（亚洲/美洲 Tab+市场徽章+30日sparkline），指数详情支持全球代码"`

---

### Task 13: 板块资金流卡 + 北向折线卡 + 市场页布局

**Files:**
- Create: `frontend/src/features/market/components/SectorMoneyflowCard.tsx`、`NorthboundCard.tsx`、`format.ts`
- Delete: `frontend/src/features/market/components/CapitalFlowChart.tsx`（grep 确认无其他引用）
- Modify: barrel `index.ts`、`pages/market/index.tsx`

**Interfaces:**
- Consumes: `fetchSectorMoneyflow/fetchNorthbound`（Task 11）、`COLORS`、`Segmented`。
- Produces: `<SectorMoneyflowCard />`（`["sector-moneyflow", dimension]` 60s 轮询）、`<NorthboundCard />`（`["northbound", 30]` staleTime 5min）、`format.ts` 导出 `fmtYi(v, digits=2): string`（元→亿）、`fmtSignedYi(v): string`（带±）、`fmtWanGu(v): string`（万股原样）、`fmtYiGu(v): string`（万股→亿股）、`fmtNorthYi(v): string`（万元→亿）。

- [ ] **Step 1: format.ts**

```ts
const DASH = "—";

export const fmtYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${(v / 1e8).toFixed(digits)}亿`;

export const fmtSignedYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e8).toFixed(digits)}亿`;

export const fmtWanGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${v.toFixed(0)}万股`;

export const fmtYiGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿股`;

export const fmtNorthYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e4).toFixed(2)}亿`;
```

- [ ] **Step 2: SectorMoneyflowCard.tsx**

```tsx
import { Card, Segmented, Spin } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { fetchSectorMoneyflow, type SectorMoneyflowItem } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtYi } from "./format";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;
const TOP_N = 10;

function buildOption(items: SectorMoneyflowItem[]) {
  const top = items.slice(0, TOP_N);
  const names = top.map((i) => i.boardName ?? i.boardCode).reverse();
  const bars = top
    .map((i) => ({
      value: (i.mainNetInflow ?? 0) / 1e8,
      pct: i.pctChange,
      ratio: i.mainNetRatio,
      itemStyle: { color: (i.mainNetInflow ?? 0) >= 0 ? COLORS.up : COLORS.down, borderRadius: 2 },
    }))
    .reverse();
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = (params as Array<{ name: string; data: { value: number; pct: number | null; ratio: number | null } }>)[0];
        const d = p?.data;
        if (!d) return "";
        return `<div style="font-weight:600">${p.name}</div>` +
          `<div>主力净流入：<b style="color:${d.value >= 0 ? COLORS.up : COLORS.down}">${d.value.toFixed(2)}亿</b></div>` +
          `<div>板块涨跌幅：${d.pct == null ? "—" : `${d.pct.toFixed(2)}%`}</div>` +
          `<div>主力净占比：${d.ratio == null ? "—" : `${d.ratio.toFixed(2)}%`}</div>`;
      },
    },
    xAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` }, splitLine: { lineStyle: { color: "#f0f0f0" } } },
    yAxis: { type: "category", data: names, axisLabel: { width: 76, overflow: "truncate" } },
    series: [{ type: "bar", data: bars, barMaxWidth: 14 }],
  };
}

export function SectorMoneyflowCard() {
  const [dimension, setDimension] = useState<"industry" | "concept">("industry");
  const { data = [], isLoading } = useQuery({
    queryKey: ["sector-moneyflow", dimension],
    queryFn: () => fetchSectorMoneyflow(dimension),
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });
  return (
    <Card
      title="板块主力资金流"
      size="small"
      extra={
        <Segmented
          size="small"
          value={dimension}
          onChange={(v) => setDimension(v as "industry" | "concept")}
          options={[
            { label: "行业", value: "industry" },
            { label: "概念", value: "concept" },
          ]}
        />
      }
    >
      <Spin spinning={isLoading}>
        {data.length > 0 ? (
          <ReactECharts option={buildOption(data)} notMerge lazyUpdate style={{ height: 260 }} />
        ) : (
          <div style={{ height: 260, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.flat }}>
            暂无资金流数据（交易日盘中自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
```

- [ ] **Step 3: NorthboundCard.tsx**

```tsx
import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { fetchNorthbound } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtNorthYi } from "./format";

const STALE_TIME = 5 * 60 * 1000;

function buildOption(points: Array<{ date: string; netAmount: number | null }>) {
  const dates = points.map((p) => p.date.slice(5));
  const values = points.map((p) => (p.netAmount == null ? null : p.netAmount / 1e4));
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: (v: number | null) => (v == null ? "—" : `${v.toFixed(2)}亿`) },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` }, splitLine: { lineStyle: { color: "#f0f0f0" } } },
    series: [
      {
        type: "line",
        data: values,
        symbol: "circle",
        symbolSize: 4,
        connectNulls: true,
        lineStyle: { width: 2, color: COLORS.primary },
        itemStyle: { color: COLORS.primary },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#c9cdd4", type: "dashed" },
          data: [{ yAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  };
}

export function NorthboundCard() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["northbound", 30],
    queryFn: () => fetchNorthbound(30),
    staleTime: STALE_TIME,
  });
  const last = data.length > 0 ? data[data.length - 1] : undefined;
  const total = data.reduce((acc, p) => acc + (p.netAmount ?? 0), 0);
  const lastColor = (last?.netAmount ?? 0) > 0 ? COLORS.up : (last?.netAmount ?? 0) < 0 ? COLORS.down : COLORS.flat;
  const totalColor = total > 0 ? COLORS.up : total < 0 ? COLORS.down : COLORS.flat;
  return (
    <Card
      title="北向资金"
      size="small"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>盘后净流入 · 亿元</Typography.Text>}
    >
      <Spin spinning={isLoading}>
        <div style={{ display: "flex", gap: 24, marginBottom: 4, fontSize: 12 }}>
          <span>当日 <b style={{ color: lastColor, fontSize: 16 }}>{fmtNorthYi(last?.netAmount)}</b></span>
          <span>近30日累计 <b style={{ color: totalColor }}>{fmtNorthYi(total)}</b></span>
        </div>
        {data.length > 0 ? (
          <ReactECharts option={buildOption(data)} notMerge lazyUpdate style={{ height: 216 }} />
        ) : (
          <div style={{ height: 216, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.flat }}>
            暂无北向数据（盘后自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
```

- [ ] **Step 4: 布局接线** — barrel 换出 `CapitalFlowChart` 换入 `SectorMoneyflowCard`/`NorthboundCard`；`pages/market/index.tsx` 调整为：

```tsx
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}><SectorMoneyflowCard /></Col>
        <Col xs={24} lg={12}><NorthboundCard /></Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}><HotSectors /></Col>
      </Row>
```

（Row1 涨跌分布+热力图不动；`CapitalFlowChart.tsx` 删除。）

- [ ] **Step 5: 验证** — lint/build；活栈开 `/market`：资金流卡有行业/概念切换且柱图正红负绿、60s 自动刷新（Network 面板确认）；北向卡折线 + 当日/累计值。
- [ ] **Step 6: Commit** — `git commit -m "feat(market-data): 板块主力资金流卡（行业/概念+盘中轮询）与北向折线卡替换近似实现"`

---

### Task 14: 数据面 Tab 区块（龙虎榜/大宗/解禁/回购/公告快讯）

**Files:**
- Create: `frontend/src/features/market/components/MarketDataBoard.tsx`、`dataFace/` 子目录五个组件 `DragonTigerTable.tsx`、`BlockTradeTable.tsx`、`ShareFloatTable.tsx`、`RepurchaseTable.tsx`、`AnnouncementFeed.tsx`
- Modify: barrel、`pages/market/index.tsx`

**Interfaces:**
- Consumes: Task 11 fetch 函数、Task 13 `format.ts`、`COLORS`。
- Produces: `<MarketDataBoard />`（antd Tabs 懒挂载 5 pane）。

- [ ] **Step 1: 五个 pane 组件**（同构模式，各文件完整代码；以 DragonTigerTable 为例，其余类推）

```tsx
import { Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDragonTiger, type DragonTigerItem } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtSignedYi } from "../format";

const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

export function DragonTigerTable() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["dragon-tiger"],
    queryFn: () => fetchDragonTiger(15),
    staleTime: 5 * 60 * 1000,
  });

  const columns: ColumnsType<DragonTigerItem> = [
    { title: "代码", dataIndex: "symbol", width: 80, render: (_, r) => <span style={NUM_FONT}>{r.symbol}</span> },
    { title: "名称", dataIndex: "name", width: 90, render: (_, r) => r.name ?? "—" },
    {
      title: "涨跌幅", dataIndex: "pctChange", width: 80, align: "right",
      render: (_, r) => (
        <span style={{ ...NUM_FONT, color: (r.pctChange ?? 0) > 0 ? COLORS.up : (r.pctChange ?? 0) < 0 ? COLORS.down : COLORS.flat }}>
          {r.pctChange == null ? "—" : `${r.pctChange > 0 ? "+" : ""}${r.pctChange.toFixed(2)}%`}
        </span>
      ),
    },
    {
      title: "龙虎榜净买额", dataIndex: "netAmount", width: 110, align: "right",
      render: (_, r) => (
        <span style={{ ...NUM_FONT, fontWeight: 600, color: (r.netAmount ?? 0) > 0 ? COLORS.up : (r.netAmount ?? 0) < 0 ? COLORS.down : COLORS.flat }}>
          {fmtSignedYi(r.netAmount)}
        </span>
      ),
    },
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];

  return (
    <Table<DragonTigerItem>
      rowKey={(r) => `${r.tsCode}-${r.reason}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 320 }}
      locale={{ emptyText: "暂无龙虎榜数据（盘后自动更新）" }}
      onRow={(r) => ({ onClick: () => navigate(`/stock/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
```

其余四个按同构模式 + 下列列定义：
- `BlockTradeTable`（query `["block-trades"]`）：代码/名称/成交价(`price.toFixed(2)`)/成交量(`fmtWanGu`)/成交额(`fmtYi(amount)`，**注意 amount 是万元**——新增 `fmtWanYi = (v) => v==null?"—":`${(v/1e4).toFixed(2)}亿`` 到 format.ts)/买方营业部(ellipsis)/卖方营业部(ellipsis)；rowKey `${tsCode}-${buyer}-${price}`。
- `ShareFloatTable`（`["share-floats"]`）：解禁日(`floatDate`)/代码/名称/解禁数量(`fmtYiGu`)/占总股本(`floatRatio?.toFixed(2)%`)/类型(`shareType`)。
- `RepurchaseTable`（`["repurchases"]`）：公告日/代码/名称/进度(Tag，实施=blue 完成=green 其他=default)/回购金额(`fmtYi(amount)`)/回购数量(`vol/1e4 万股`，即 `${(r.vol/1e4).toFixed(0)}万股`)。
- `AnnouncementFeed`（`["announcements"]`，antd `List size="small"`）：

```tsx
import { List, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchAnnouncements } from "@/shared/api/marketData";

const CATEGORY_META: Record<string, { label: string; color: string }> = {
  report: { label: "财报", color: "blue" },
  event: { label: "事项", color: "orange" },
};

export function AnnouncementFeed() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["announcements"],
    queryFn: () => fetchAnnouncements(undefined, 30),
    staleTime: 5 * 60 * 1000,
  });
  return (
    <List
      size="small"
      loading={isLoading}
      dataSource={data}
      style={{ maxHeight: 360, overflowY: "auto" }}
      locale={{ emptyText: "暂无公告快讯" }}
      renderItem={(a) => {
        const meta = CATEGORY_META[a.category] ?? { label: a.category, color: "default" };
        return (
          <List.Item style={{ padding: "6px 0" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
                {a.announceTime.slice(5, 16).replace("T", " ")}
              </Typography.Text>
              <Tag color={meta.color} style={{ marginRight: 0, flexShrink: 0 }}>{meta.label}</Tag>
              <Typography.Text style={{ fontSize: 13, flexShrink: 0 }}>{a.secName ?? a.secCode}</Typography.Text>
              <a href={a.pdfUrl ?? undefined} target="_blank" rel="noreferrer" style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.title}
              </a>
            </div>
          </List.Item>
        );
      }}
    />
  );
}
```

- [ ] **Step 2: MarketDataBoard.tsx**

```tsx
import { Card, Tabs } from "antd";
import { DragonTigerTable } from "./dataFace/DragonTigerTable";
import { BlockTradeTable } from "./dataFace/BlockTradeTable";
import { ShareFloatTable } from "./dataFace/ShareFloatTable";
import { RepurchaseTable } from "./dataFace/RepurchaseTable";
import { AnnouncementFeed } from "./dataFace/AnnouncementFeed";

export function MarketDataBoard() {
  return (
    <Card title="数据面" size="small">
      <Tabs
        defaultActiveKey="dragon-tiger"
        items={[
          { key: "dragon-tiger", label: "龙虎榜", children: <DragonTigerTable /> },
          { key: "block-trades", label: "大宗交易", children: <BlockTradeTable /> },
          { key: "share-floats", label: "解禁", children: <ShareFloatTable /> },
          { key: "repurchases", label: "回购", children: <RepurchaseTable /> },
          { key: "announcements", label: "公告快讯", children: <AnnouncementFeed /> },
        ]}
      />
    </Card>
  );
}
```

- [ ] **Step 3: 接线** — barrel 导出；`pages/market/index.tsx` 在 HotSectors 行后追加：

```tsx
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}><MarketDataBoard /></Col>
      </Row>
```

- [ ] **Step 4: 验证** — lint/build；活栈 `/market`：数据面 5 个 Tab 切换正常、龙虎榜净买额±着色、公告条目点开 PDF 新窗口。
- [ ] **Step 5: Commit** — `git commit -m "feat(market-data): 数据面 Tab 区块（龙虎榜/大宗/解禁/回购/公告快讯）"`

---

### Task 15: 个股详情页相关数据卡

**Files:**
- Create: `frontend/src/features/stock-detail/components/RelatedEvents.tsx`
- Modify: `frontend/src/features/stock-detail/components/index.ts`、`frontend/src/pages/stock-detail/index.tsx`

**Interfaces:**
- Consumes: `fetchDragonTiger/fetchBlockTrades/fetchShareFloats/fetchRepurchases/fetchAnnouncements`（symbol 过滤）、Task 14 的表格列风格。
- Produces: `<RelatedEvents symbol={string} />`。

- [ ] **Step 1: RelatedEvents.tsx**

```tsx
import { Card, Segmented, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  fetchAnnouncements, fetchBlockTrades, fetchDragonTiger,
  fetchRepurchases, fetchShareFloats,
  type AnnouncementItem, type BlockTradeItem, type DragonTigerItem,
  type RepurchaseItem, type ShareFloatItem,
} from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtSignedYi, fmtWanGu, fmtYi, fmtYiGu, fmtWanYi } from "@/features/market/components/format";

const STALE_TIME = 5 * 60 * 1000;
const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

type Kind = "announcements" | "dragon-tiger" | "block-trades" | "share-floats" | "repurchases";

const KIND_OPTIONS = [
  { label: "公告", value: "announcements" },
  { label: "龙虎榜", value: "dragon-tiger" },
  { label: "大宗", value: "block-trades" },
  { label: "解禁", value: "share-floats" },
  { label: "回购", value: "repurchases" },
] as const;

export function RelatedEvents({ symbol }: { symbol: string }) {
  const [kind, setKind] = useState<Kind>("announcements");

  const ann = useQuery({
    queryKey: ["related-events", symbol, "announcements"],
    queryFn: () => fetchAnnouncements(symbol, 10),
    staleTime: STALE_TIME, enabled: kind === "announcements",
  });
  const dragon = useQuery({
    queryKey: ["related-events", symbol, "dragon-tiger"],
    queryFn: () => fetchDragonTiger(50).then((rows) => rows.filter((r) => r.symbol === symbol)),
    staleTime: STALE_TIME, enabled: kind === "dragon-tiger",
  });
  const block = useQuery({
    queryKey: ["related-events", symbol, "block-trades"],
    queryFn: () => fetchBlockTrades(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "block-trades",
  });
  const floats = useQuery({
    queryKey: ["related-events", symbol, "share-floats"],
    queryFn: () => fetchShareFloats(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "share-floats",
  });
  const repo = useQuery({
    queryKey: ["related-events", symbol, "repurchases"],
    queryFn: () => fetchRepurchases(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "repurchases",
  });

  const dragonCols: ColumnsType<DragonTigerItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "净买额", dataIndex: "netAmount", width: 100, align: "right",
      render: (_, r) => <span style={{ ...NUM_FONT, color: (r.netAmount ?? 0) > 0 ? COLORS.up : COLORS.down }}>{fmtSignedYi(r.netAmount)}</span> },
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];
  const blockCols: ColumnsType<BlockTradeItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "成交价", dataIndex: "price", width: 80, align: "right", render: (v) => <span style={NUM_FONT}>{v?.toFixed(2) ?? "—"}</span> },
    { title: "成交量", dataIndex: "volume", width: 90, align: "right", render: (v) => fmtWanGu(v) },
    { title: "成交额", dataIndex: "amount", width: 90, align: "right", render: (v) => fmtWanYi(v) },
    { title: "买方", dataIndex: "buyer", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const floatCols: ColumnsType<ShareFloatItem> = [
    { title: "解禁日", dataIndex: "floatDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "数量", dataIndex: "floatShare", width: 90, align: "right", render: (v) => fmtYiGu(v) },
    { title: "占比", dataIndex: "floatRatio", width: 70, align: "right", render: (v) => (v == null ? "—" : `${v.toFixed(2)}%`) },
    { title: "类型", dataIndex: "shareType", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const repoCols: ColumnsType<RepurchaseItem> = [
    { title: "公告日", dataIndex: "annDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "进度", dataIndex: "proc", width: 70, render: (v: string) => <Typography.Text>{v}</Typography.Text> },
    { title: "回购金额", dataIndex: "amount", width: 100, align: "right", render: (v) => fmtYi(v) },
  ];

  const navigate = useNavigate();

  return (
    <Card
      title="相关数据"
      size="small"
      extra={
        <Segmented size="small" value={kind} onChange={(v) => setKind(v as Kind)} options={[...KIND_OPTIONS]} />
      }
    >
      {kind === "announcements" && (
        <Table<AnnouncementItem>
          rowKey="announcementId" size="small" loading={ann.isLoading} pagination={false}
          dataSource={ann.data ?? []} locale={{ emptyText: "暂无相关公告" }}
          columns={[
            { title: "时间", dataIndex: "announceTime", width: 130, render: (v: string) => <span style={NUM_FONT}>{v.slice(0, 16).replace("T", " ")}</span> },
            { title: "标题", dataIndex: "title", ellipsis: true,
              render: (_, r) => <a href={r.pdfUrl ?? undefined} target="_blank" rel="noreferrer">{r.title}</a> },
          ]}
        />
      )}
      {kind === "dragon-tiger" && (
        <Table<DragonTigerItem> rowKey={(r) => `${r.tradeDate}-${r.reason}`} size="small" loading={dragon.isLoading}
          pagination={false} dataSource={dragon.data ?? []} columns={dragonCols} locale={{ emptyText: "暂无上榜记录" }} />
      )}
      {kind === "block-trades" && (
        <Table<BlockTradeItem> rowKey={(r) => `${r.tradeDate}-${r.buyer}-${r.price}`} size="small" loading={block.isLoading}
          pagination={false} dataSource={block.data ?? []} columns={blockCols} locale={{ emptyText: "暂无大宗交易记录" }} />
      )}
      {kind === "share-floats" && (
        <Table<ShareFloatItem> rowKey={(r) => `${r.floatDate}-${r.shareType}`} size="small" loading={floats.isLoading}
          pagination={false} dataSource={floats.data ?? []} columns={floatCols} locale={{ emptyText: "暂无解禁记录" }} />
      )}
      {kind === "repurchases" && (
        <Table<RepurchaseItem> rowKey={(r) => `${r.annDate}-${r.proc}`} size="small" loading={repo.isLoading}
          pagination={false} dataSource={repo.data ?? []} columns={repoCols} locale={{ emptyText: "暂无回购记录" }} />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
        数据源：TuShare / 巨潮资讯 · 龙虎榜为全市场最新日筛选本股
      </Typography.Text>
      <span hidden onClick={() => navigate(`/stock/${symbol}`)} />
    </Card>
  );
}
```

（末行 `<span hidden .../>` 是防 navigate 未使用报错的草稿残留——**删掉它和 `useNavigate` import**，本卡不需要跳转。）

- [ ] **Step 2: 接线** — `features/stock-detail/components/index.ts` 导出；打开 `pages/stock-detail/index.tsx`，在 K 线 Card 区块之后（基础信息/行情卡之后、下一个 Divider 之前）插入：

```tsx
<Row gutter={[16, 16]} style={{ marginTop: 16 }}>
  <Col span={24}>
    <RelatedEvents symbol={symbol} />
  </Col>
</Row>
```

（`symbol` 取该页既有的股票代码变量名；若页面无 Row/Col import 则补。）

- [ ] **Step 3: 验证** — lint/build；活栈 `/stock/600519`：相关数据卡默认公告 Tab；切龙虎榜/大宗等 Tab 表格出现或显示"暂无"。
- [ ] **Step 4: Commit** — `git commit -m "feat(market-data): 个股详情页相关数据卡（公告/龙虎榜/大宗/解禁/回购）"`

---

### Task 16: e2e + 文档收尾 + 全量验证

**Files:**
- Create: `frontend/e2e/marketDataFace.spec.ts`
- Modify: `docs/Changelog.md`、`docs/references/best-practices.md`、`docs/design/data-source.md`（若存在；否则跳过该文件）

**Interfaces:** 无代码接口；交付验证与文档。

- [ ] **Step 1: e2e spec**

```ts
import { expect, test } from "@playwright/test";

const expect15s = expect.configure({ timeout: 15_000 });

test.describe("市场数据面", () => {
  test("全球市场：亚洲/美洲 Tab 与指数卡", async ({ page }) => {
    await page.goto("/market");
    await expect15s(page.getByRole("tab", { name: "亚洲" })).toBeVisible();
    await expect15s(page.getByText("日经225")).toBeVisible();
    await page.getByRole("tab", { name: "美洲" }).click();
    await expect15s(page.getByText("道琼斯")).toBeVisible();
    await expect15s(page.getByText("日经225")).toHaveCount(0);
  });

  test("全球指数详情页", async ({ page }) => {
    await page.goto("/index/N225");
    const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
    await expect15s(card).toBeVisible();
  });

  test("板块资金流：行业/概念切换", async ({ page }) => {
    await page.goto("/market");
    const card = page.locator(".ant-card").filter({ hasText: "板块主力资金流" });
    await expect15s(card).toBeVisible();
    await card.locator(".ant-segmented-item").filter({ hasText: "概念" }).click();
    await expect15s(card.locator(".ant-segmented-item").filter({ hasText: "概念" })).toHaveClass(/ant-segmented-item-selected/);
  });

  test("数据面：Tab 表格", async ({ page }) => {
    await page.goto("/market");
    await page.getByRole("tab", { name: "龙虎榜", exact: true }).click();
    const board = page.locator(".ant-card").filter({ hasText: "数据面" });
    await expect15s(board.locator("thead th").filter({ hasText: "上榜原因" })).toBeVisible();
    await page.getByRole("tab", { name: "公告快讯" }).click();
    await expect15s(board.locator(".ant-list")).toBeVisible();
  });

  test("个股相关数据卡", async ({ page }) => {
    await page.goto("/stock/600519");
    const card = page.locator(".ant-card").filter({ hasText: "相关数据" });
    await expect15s(card).toBeVisible();
    await card.locator(".ant-segmented-item").filter({ hasText: "回购" }).click();
    await expect15s(card.locator("table")).toBeVisible();
  });
});
```

（若某数据源活栈无数据导致断言超时，按既有惯例在断言前加数据存在性检查并 `test.skip()`，勿删用例。）

- [ ] **Step 2: 文档三连**

`docs/Changelog.md` 追加：

```markdown
- 2026-09-03 市场数据面：全球市场指数区块（亚洲/美洲 Tab、徽章卡+30日sparkline）、板块主力资金流盘中轮询卡、北向资金折线卡、数据面五类榜单（龙虎榜/大宗/解禁/回购/公告快讯，TuShare+东财+巨潮）、个股相关数据卡；新增 7 表与 `market_data.fetch` 队列。
```

`docs/references/best-practices.md` 追加：

```markdown
- 接三方行情先 curl 实测定字段与单位再写映射：东财 f62 是元、TuShare block_trade 是万元/万股、north_money 是万元、巨潮 announcementTime 是毫秒——单位/时间戳错一档，UI 就差四个数量级或 1970 年。
```

`docs/design/data-source.md`（存在时）追加「市场数据面数据源」小节，粘贴本文档「已验证数据源事实」要点（端点、参数、字段、单位、限频策略）。

- [ ] **Step 3: 全量验证清单**

```bash
cd backend && uv run --extra dev ruff check . && uv run --extra dev mypy app
uv run pytest -q                      # 已知 19 个环境性 httpx.ConnectError 失败可接受，不得新增其他失败
cd ../frontend && npm run lint && npm run build
docker compose build && docker compose up -d
docker exec postgres psql -U stock_user -d stock_bot -c "SELECT count(*) FROM announcements;"
npm run test:e2e                      # E2E_BASE_URL 默认 localhost:3000
```

- [ ] **Step 4: 最终 Commit** — `git commit -m "feat(market-data): e2e 用例与文档收尾（市场数据面 P1-P10 完成）"`

---

## Self-Review 记录

- **Spec 覆盖**：设计 6 点 → Task 4/12（全球市场+详情）、Task 5/13（资金流）、Task 6/13（北向）、Task 7/8/14（数据面）、Task 9/14（公告）、Task 15（个股卡）、Task 3（指数日线底座）、Task 10（手动触发双轨）。布局调整 → Task 13 Step 4。✓
- **占位符扫描**：Task 9/15 内标注了「草稿行删除」的说明行属执行指令而非占位；Task 4 Step 4 端点注明的简化版→实现版转换是明确指令。无 TBD/TODO。✓
- **类型一致性**：`get_global_index_cards` 返回 dict 键与 `GlobalIndexCardOut`/前端 `GlobalIndexCard` 字段一一对应（snake↔camel 映射在 Task 11 map 层）；`_map_*` 系列返回键与 repo upsert values 键、Out schema 字段一致；`fmtWanYi` 在 Task 14 定义、Task 15 消费（Task 13 Step 1 需同步加入——**已并入 Task 14 Step 1 的说明**，执行 Task 13 时先不加、Task 14 补入 format.ts）。✓
