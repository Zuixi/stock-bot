# Wave 2 Plan 1：内容事实层 + 公告切片

status: active
scope: 落地 S3.1 四表、cninfo 公告迁移到内容层、定期报告财务数字的结构化 join、`/news` 公告 Tab；不接快讯/研报/政策，不上 LLM
touches: backend/app/{models,repositories,services,api/v1,migrations}, backend/tests, frontend/src/{shared/api,features,pages,app/router}, docs/specs, docs/features.md, docs/architecture/, plans/index.md
updated: 2026-11-11
next-action: Task 1（四表迁移 + 模型 + schema 不变量测试）

> 契约：[docs/specs/S3-content.md](../docs/specs/S3-content.md)（含 2026-11-11 三处 owner 裁定）。
> 本计划只实现 S3.1 / S3.2 / S3.4 的**定期报告分支** / S3.6 的**公告 Tab**。
> 快讯、研报、政策、LLM `core_view`、人工裁定队列分别留给 Plan 2–4，不在本计划偷跑。

## 0. 三条硬约束（来自 owner 裁定）

1. **只存链接**：`content_items` 不落正文/PDF；`url` 是唯一原文入口。抽取只允许内存态读取，`source_span.quote` ≤500 字符。
2. **定期报告数字走结构化 join**：`revenue` / `net_profit` 从 `financial_report_versions` + `income_statement_facts` 取，不解析 PDF；PIT 用 `f_ann_date <= content.published_at`。
3. **`source_span` 两种形态**：`pdf`（page/char/quote）与 `structured`（table/key/ann_date/quote）；数值型抽取 `source_span` 非空率 100%。

## 1. 目标与架构

**Goal**：让「公告」成为内容层的第一条完整链路：cninfo 采集 → `content_items`（事实层）→ 实体关联 → 定期报告财务数（结构化）→ `/news` 公告 Tab；同时把旧 `announcements` 表切成只读投影，保证只有一份真相。

**Architecture**：

```
cninfo_client.fetch_announcements()
  → announcement_service.ingest_announcements_window()
  → content_repo.upsert_content_items() + insert_content_entities()
  → extraction.financial_join.upsert_financial_metrics_for_content()
  → /api/v1/content（列表/详情/用户状态）
  → /news（公告 Tab）
```

旧 `announcements` 表在迁移末尾替换为**只读视图**，列与原表一致；现有 `journal_service` / `market_data_repo.list_announcements` 不改 SQL 也能继续读。

**Tech Stack**：FastAPI · SQLAlchemy 2.0 async · Alembic · PostgreSQL 15 · pytest · React/TS/AntD/TanStack Query · Playwright。

## 2. 文件结构（先锁定边界）

| 文件 | 职责 |
|---|---|
| `backend/app/models/content.py` | 四张内容表的 ORM；**不出现任何用户状态列** |
| `backend/app/repositories/content_repo.py` | 内容 upsert / 实体写入 / 列表查询 / 用户状态 |
| `backend/app/services/content_ingest/entity_resolver.py` | 股票代码/名称 → `content_entities` |
| `backend/app/services/extraction/financial_join.py` | 定期报告标题 → 报告期 → 财务版本 → `content_metrics` |
| `backend/app/services/announcement_service.py` | 采集入口改为写内容层；保留对账/缓存语义 |
| `backend/app/api/v1/content.py` | `/api/v1/content` 读面 + 用户状态写面 |
| `frontend/src/shared/api/content.ts` | 前端 API 客户端与 DTO 映射 |
| `frontend/src/features/content/ContentList.tsx` | 公告列表（四类 Tab 的公告分支） |
| `frontend/src/pages/news/index.tsx` | `/news` 真页面（替换占位） |

## 3. Tasks

### Task 1：四表迁移 + 模型 + schema 不变量

**Files**
- Create: `backend/app/models/content.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/migrations/versions/a1b2c3d4e5f6_add_content_foundation.py`
- Test: `backend/tests/test_content_schema.py`

**Interfaces（后续任务依赖）**
- `ContentItem`, `ContentEntity`, `ContentMetric`, `UserContentState`
- `content_items.id: int`；`unique(source_name, raw_hash)`
- `content_entities(content_id, entity_type, entity_id)` 复合主键
- `content_metrics(content_id, entity_id, metric_key, period)` 唯一

- [ ] **Step 1：写失败测试**

```python
# backend/tests/test_content_schema.py
from sqlalchemy import inspect

from app.core.database import engine


def test_content_items_has_no_user_state_columns():
    cols = {c["name"] for c in inspect(engine).get_columns("content_items")}
    assert "status" not in cols
    assert "read_at" not in cols
    assert "body_ref" not in cols  # 2026-11-11 裁定：只存链接


def test_content_items_unique_source_hash():
    insp = inspect(engine)
    uniques = {u["name"] for u in insp.get_unique_constraints("content_items")}
    assert "uq_content_items_source_hash" in uniques


def test_content_entities_cascade():
    fks = inspect(engine).get_foreign_keys("content_entities")
    assert any(fk["referred_table"] == "content_items" and fk["options"].get("ondelete") == "CASCADE" for fk in fks)
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_content_schema.py -v`
Expected: FAIL（表不存在）

- [ ] **Step 3：写模型与迁移**

`content.py` 核心列（与 S3.1 一致，删 `body_ref`）：

```python
class ContentItem(Base):
    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("source_name", "raw_hash", name="uq_content_items_source_hash"),
        Index("ix_content_items_type_published", "content_type", "published_at"),
        Index("ix_content_items_published", "published_at"),
        Index("ix_content_items_review", "review_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_name: Mapped[str] = mapped_column(String(32), nullable=False)
    publisher_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    extract_version: Mapped[int | None] = mapped_column(Integer)
    extract_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

迁移 `down_revision = "d5e6f7a8b9c0"`；四表 + 索引；`review_status` 加 `CHECK IN ('auto','pending','confirmed','rejected')`。

- [ ] **Step 4：跑测试与迁移**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/test_content_schema.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add backend/app/models/content.py backend/app/models/__init__.py \
  backend/app/migrations/versions/a1b2c3d4e5f6_add_content_foundation.py \
  backend/tests/test_content_schema.py
git commit -m "feat(content): add content foundation tables"
```

### Task 2：实体解析 + 内容仓库

**Files**
- Create: `backend/app/services/content_ingest/__init__.py`
- Create: `backend/app/services/content_ingest/entity_resolver.py`
- Create: `backend/app/repositories/content_repo.py`
- Test: `backend/tests/test_content_entity_resolver.py`

**Interfaces**
- `resolve_stock_entities(text: str, name_to_symbol: Mapping[str, str]) -> list[tuple[str, str]]` → `[("stock", "002956")]`
- `upsert_content_items(db, rows: list[dict]) -> int`（`ON CONFLICT (source_name, raw_hash) DO NOTHING`）
- `insert_content_entities(db, content_id: int, entities: Sequence[tuple[str, str]]) -> int`

- [ ] **Step 1：写失败测试**

```python
# backend/tests/test_content_entity_resolver.py
from app.services.content_ingest.entity_resolver import resolve_stock_entities

NAMES = {"西麦食品": "002956", "贵州茅台": "600519"}


def test_resolve_by_code():
    assert resolve_stock_entities("西麦食品(002956)发布三季报", NAMES) == [("stock", "002956")]


def test_resolve_by_name():
    assert resolve_stock_entities("贵州茅台发布公告", NAMES) == [("stock", "600519")]


def test_ignore_non_ashare_numbers():
    assert resolve_stock_entities("合同金额 123456 万元", NAMES) == []
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_content_entity_resolver.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3：实现解析器**

```python
_CODE_RE = re.compile(r"(?<!\d)((?:60|68|00|30|43|83|87|92)\d{4})(?!\d)")

def resolve_stock_entities(text: str, name_to_symbol: Mapping[str, str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _CODE_RE.finditer(text):
        sym = m.group(1)
        if sym not in seen:
            seen.add(sym); found.append(("stock", sym))
    for name, sym in name_to_symbol.items():
        if len(name) >= 2 and name in text and sym not in seen:
            seen.add(sym); found.append(("stock", sym))
    return found
```

`content_repo.py` 用 `pg_insert(...).on_conflict_do_nothing(constraint="uq_content_items_source_hash")`；实体写入用 `ON CONFLICT DO NOTHING`。

- [ ] **Step 4：跑测试**

Run: `cd backend && uv run pytest tests/test_content_entity_resolver.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add backend/app/services/content_ingest/ backend/app/repositories/content_repo.py \
  backend/tests/test_content_entity_resolver.py
git commit -m "feat(content): entity resolver and content repository"
```

### Task 3：cninfo 公告改写入内容层

**Files**
- Modify: `backend/app/services/announcement_service.py`
- Modify: `backend/app/repositories/market_data_repo.py`（删除 `upsert_announcements` 的写路径，保留读路径直到 Task 7）
- Test: `backend/tests/test_content_announcement_ingest.py`

**Interfaces**
- `ingest_announcements_window(db, se_date, categories=None) -> dict[str, int]` 语义不变，但写 `content_items`
- `extracted = {"source_item_id": ..., "subtype": "report|event|equity_change", "sec_code": ..., "sec_name": ...}`
- `raw_hash = sha256(f"cninfo:{announcement_id}")`
- `publisher_tier="legal"`；`content_type="announcement"`；`url=pdf_url`

- [ ] **Step 1：写失败测试**

```python
# backend/tests/test_content_announcement_ingest.py
async def test_ingest_writes_content_items_and_is_idempotent(db, monkeypatch):
    rows = [{
        "announcement_id": "1225434550",
        "sec_code": "002956",
        "sec_name": "西麦食品",
        "title": "2026年第三季度报告",
        "announce_time": datetime(2026, 10, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        "category": "report",
        "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-10-30/1225434550.PDF",
    }]
    # monkeypatch client.fetch_announcements 返回 rows
    first = await ingest_announcements_window(db, "2026-10-30~2026-10-30", ("report",))
    second = await ingest_announcements_window(db, "2026-10-30~2026-10-30", ("report",))
    assert first["report"] == 1
    assert second["report"] == 0
    item = (await db.execute(select(ContentItem))).scalar_one()
    assert item.content_type == "announcement"
    assert item.publisher_tier == "legal"
    assert item.extracted["source_item_id"] == "1225434550"
    assert item.url.endswith("1225434550.PDF")
    assert "body" not in item.extracted
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_content_announcement_ingest.py -v`
Expected: FAIL（仍写旧表）

- [ ] **Step 3：实现**

在 `ingest_announcements_window` 内把 `market_data_repo.upsert_announcements` 换成：

```python
content_rows = [{
    "content_type": "announcement",
    "source_name": "cninfo",
    "publisher_tier": "legal",
    "publisher": r.get("sec_name"),
    "title": r["title"],
    "published_at": r["announce_time"],
    "url": r["pdf_url"],
    "raw_hash": hashlib.sha256(f"cninfo:{r['announcement_id']}".encode()).hexdigest(),
    "extracted": {
        "source_item_id": r["announcement_id"],
        "subtype": r["category"],
        "sec_code": r["sec_code"],
        "sec_name": r.get("sec_name"),
    },
} for r in unique]
accepted = await content_repo.upsert_content_items(db, content_rows)
# 对 accepted 行按 raw_hash 回查 id，写 content_entities(stock, sec_code)
```

实体写入：`resolve_stock_entities(r["title"], {r["sec_name"]: r["sec_code"]})`，并强制把 `sec_code` 作为 stock 实体写入（公告自带代码，优先于标题解析）。

- [ ] **Step 4：跑测试**

Run: `cd backend && uv run pytest tests/test_content_announcement_ingest.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add backend/app/services/announcement_service.py backend/tests/test_content_announcement_ingest.py
git commit -m "feat(content): write cninfo announcements into content layer"
```

### Task 4：定期报告财务数字结构化 join

**Files**
- Create: `backend/app/services/extraction/__init__.py`
- Create: `backend/app/services/extraction/financial_join.py`
- Test: `backend/tests/test_content_financial_join.py`

**Interfaces**
- `parse_report_period(title: str) -> date | None`：`2026年第三季度报告` → `2026-09-30`；`2026年半年度报告` → `2026-06-30`；`2026年年度报告` → `2026-12-31`；`2026年第一季度报告` → `2026-03-31`
- `upsert_financial_metrics_for_content(db, content_id: int) -> int`

**规则**
1. 只处理 `content_type='announcement'` 且 `extracted.subtype='report'` 且标题能解析出报告期。
2. 用 `content_entities` 的 `stock` → `stocks.id`；按 `stock_id + end_date` 查 `financial_report_versions`。
3. **PIT**：`f_ann_date <= content.published_at.date()`，按 `f_ann_date desc, id desc` 取第一条；`comp_type='1'`。
4. 读 `income_statement_facts.revenue / n_income_attr_p`。
5. 写 `content_metrics`：
   - `metric_key='revenue'` / `'net_profit'`
   - `period=f"{year}Q{quarter}"` 或 `{year}H1` / `{year}FY`
   - `unit='CNY'`
   - `source_span={"kind":"structured","table":"financial_report_versions","key":version.id,"ann_date":version.f_ann_date.isoformat(),"quote":f"revenue={value}"}`
6. 没有版本/没有科目：不写 0，写 `content_items.extracted["missing_reason"]`（如 `no_pit_financial_version`）。

- [ ] **Step 1：写失败测试**

```python
def test_parse_report_period():
    assert parse_report_period("2026年第三季度报告") == date(2026, 9, 30)
    assert parse_report_period("2026年半年度报告") == date(2026, 6, 30)
    assert parse_report_period("2026年年度报告") == date(2026, 12, 31)
    assert parse_report_period("关于回购的公告") is None


async def test_pit_join_ignores_future_version(db):
    # version A: f_ann_date=2026-10-30; version B: f_ann_date=2026-11-05
    # content.published_at=2026-10-30 -> 只能取 A
    ...
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_content_financial_join.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3：实现**

```python
_REPORT_PERIOD_RE = re.compile(r"(\d{4})\s*年\s*(第一季度|一季度|半年度|第三季度|三季度|年度)")

def parse_report_period(title: str) -> date | None:
    m = _REPORT_PERIOD_RE.search(title)
    if not m:
        return None
    year, kind = int(m.group(1)), m.group(2)
    if kind in {"第一季度", "一季度"}: return date(year, 3, 31)
    if kind == "半年度": return date(year, 6, 30)
    if kind in {"第三季度", "三季度"}: return date(year, 9, 30)
    return date(year, 12, 31)
```

查询用 `select(FinancialReportVersion).where(..., FinancialReportVersion.f_ann_date <= published_date).order_by(desc(f_ann_date), desc(id)).limit(1)`。

- [ ] **Step 4：跑测试**

Run: `cd backend && uv run pytest tests/test_content_financial_join.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add backend/app/services/extraction/ backend/tests/test_content_financial_join.py
git commit -m "feat(content): structured financial join for periodic reports"
```

### Task 5：内容 API + 用户状态

**Files**
- Create: `backend/app/schemas/content.py`
- Create: `backend/app/api/v1/content.py`
- Modify: `backend/app/api/v1/__init__.py`
- Test: `backend/tests/test_content_api.py`

**Interfaces**
- `GET /api/v1/content?content_type=announcement&subtype=report&entity_type=stock&entity_id=002956&status=new&limit=30&offset=0`
- `GET /api/v1/content/{id}` 返回 `metrics[]` 与 `entities[]`
- `PATCH /api/v1/content/{id}/state` body `{"status":"read|processed|ignored|archived","note":null}`；未登录 401
- 列表排序：`published_at desc`（公告阶段还没有跨 tier 混排）

- [ ] **Step 1：写失败测试**

```python
async def test_content_list_filters_and_user_state_isolation(client, seed_two_users):
    r = await client.get("/api/v1/content", params={"content_type": "announcement", "limit": 10})
    assert r.status_code == 200
    item_id = r.json()[0]["id"]

    r = await client.patch(f"/api/v1/content/{item_id}/state", json={"status": "read"}, headers=user_a)
    assert r.status_code == 200

    r = await client.get(f"/api/v1/content/{item_id}", headers=user_b)
    assert r.json()["user_state"] is None  # 另一个用户仍是未读
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_content_api.py -v`
Expected: FAIL（404）

- [ ] **Step 3：实现**

列表查询 join `user_content_state`（当前 `user_id`）；无行时 `user_state=null`。`PATCH` 用 `pg_insert(...).on_conflict_do_update` 写 `(user_id, content_id)`。详情返回 `content_metrics` 按 `metric_key, period` 排序。

- [ ] **Step 4：跑测试**

Run: `cd backend && uv run pytest tests/test_content_api.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add backend/app/schemas/content.py backend/app/api/v1/content.py \
  backend/app/api/v1/__init__.py backend/tests/test_content_api.py
git commit -m "feat(content): content list/detail/state API"
```

### Task 6：`/news` 公告 Tab

**Files**
- Create: `frontend/src/shared/api/content.ts`
- Create: `frontend/src/features/content/ContentList.tsx`
- Create: `frontend/src/features/content/ContentList.css`
- Modify: `frontend/src/pages/news/index.tsx`
- Modify: `frontend/src/app/router/route-manifest.ts`（去掉 `news` 的 `isPlaceholder`）
- Test: `frontend/e2e/newsAnnouncements.spec.ts`

**UI 契约**
- 四类 Tab：公告 / 政策 / 研报 / 快讯；本期只有「公告」有数据，其余显示空态，不伪造。
- 公告行：标题（点原文新窗口）· 公司名/代码 · `published_at` · 子类徽章（财报/重大事项/股本变动）· 右侧「已读/忽略」。
- 定期报告行展开显示 `revenue` / `net_profit` 与 `period`；没有结构化数字显示 `—` + `missing_reason`，不显示 0。
- 失败态与空态分离；列表加载用骨架，不整页 spinner。

- [ ] **Step 1：写失败 e2e**

```ts
// frontend/e2e/newsAnnouncements.spec.ts
test("news announcements tab renders content items", async ({ page }) => {
  await page.goto("/news");
  await expect(page.getByRole("tab", { name: "公告" })).toBeVisible();
  await expect(page.getByTestId("content-row").first()).toBeVisible();
  await expect(page.getByTestId("content-original-link").first()).toHaveAttribute("href", /^https?:/);
});
```

- [ ] **Step 2：跑 e2e 确认失败**

Run: `cd frontend && npx playwright test e2e/newsAnnouncements.spec.ts`
Expected: FAIL（仍渲染占位）

- [ ] **Step 3：实现页面与客户端**

`content.ts` 调 `/api/v1/content`；`ContentList` 用 TanStack Query，`queryKey: ["content", filters]`；状态写回后 `invalidateQueries`。`pages/news/index.tsx` 用 `Tabs` + `ContentList`；`route-manifest.ts` 删除 `isPlaceholder: true`。

- [ ] **Step 4：跑静态检查与 e2e**

Run: `cd frontend && npx tsc --noEmit && npm run build && npx playwright test e2e/newsAnnouncements.spec.ts`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git add frontend/src/shared/api/content.ts frontend/src/features/content/ \
  frontend/src/pages/news/index.tsx frontend/src/app/router/route-manifest.ts \
  frontend/e2e/newsAnnouncements.spec.ts
git commit -m "feat(news): announcements tab on content API"
```

### Task 7：旧 `announcements` 切成只读投影 + 文档/门禁

**Files**
- Create: `backend/app/migrations/versions/b2c3d4e5f6a7_announcements_compat_view.py`
- Modify: `backend/app/services/announcement_service.py`（读路径保留，写路径已切）
- Modify: `backend/app/repositories/market_data_repo.py`（删除 `upsert_announcements`）
- Modify: `docs/features.md`（行情与市场表加一行 `/news` 公告；数据管道表加内容层）
- Modify: `docs/architecture/database-architecture.md`（四表 + 视图）
- Modify: `plans/index.md`（登记本计划）
- Test: `backend/tests/test_announcement_view.py`

**迁移逻辑**
1. 把旧 `announcements` 剩余行按 Task 3 的映射补入 `content_items`（`ON CONFLICT DO NOTHING`）。
2. `DROP TABLE announcements`。
3. `CREATE VIEW announcements AS SELECT ... FROM content_items LEFT JOIN content_entities ... WHERE content_type='announcement'`，列：`id, announcement_id, sec_code, sec_name, title, announce_time, category, pdf_url, created_at`。
4. 视图只读：测试断言 `INSERT INTO announcements ...` 抛错。

- [ ] **Step 1：写失败测试**

```python
async def test_announcements_view_is_read_only(db):
    with pytest.raises(Exception):
        await db.execute(text("INSERT INTO announcements (announcement_id, sec_code, title) VALUES ('x','002956','x')"))
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_announcement_view.py -v`
Expected: FAIL（仍是表，insert 成功）

- [ ] **Step 3：迁移 + 清理**

删除 `market_data_repo.upsert_announcements` 与 `Announcement` 的写用法；`list_announcements` 仍读视图（或改为 `content_repo`，二选一，保持单一实现）。**不保留双写。**

- [ ] **Step 4：跑全量门禁**

Run:
```bash
cd backend && uv run pytest && uv run --extra dev ruff check . && uv run --extra dev mypy app
cd frontend && npx tsc --noEmit && npm run build
bash scripts/self_review.sh --full
bash scripts/doc_gate.sh
```
Expected: 全绿；`doc_gate` 认可 `features.md` 新行。

- [ ] **Step 5：提交**

```bash
git add backend/app/migrations/versions/b2c3d4e5f6a7_announcements_compat_view.py \
  backend/app/services/announcement_service.py backend/app/repositories/market_data_repo.py \
  backend/tests/test_announcement_view.py docs/features.md \
  docs/architecture/database-architecture.md plans/index.md
git commit -m "refactor(content): single source of truth for announcements"
```

## 4. 验收（本计划完成判据）

- [ ] `content_items/entities/metrics/user_state` 四表上线；`content_items` 无 `body_ref`、无用户状态列。
- [ ] cninfo 连抓 5 个交易日无缺口；重跑幂等新增 0。
- [ ] 公告 `url` 抽样 30 条可达率 100%。
- [ ] 定期报告抽 10 条，`revenue/net_profit` 与 `financial_report_versions` 对应版本一致；`source_span.kind='structured'` 非空率 100%。
- [ ] `/news` 公告 Tab 可用；失败态/空态分离；`npx tsc --noEmit` + `npm run build` 通过。
- [ ] 旧 `announcements` 为只读视图；全仓无第二条公告写路径。
- [ ] `bash scripts/self_review.sh --full` + `bash scripts/doc_gate.sh` 全绿。

## 5. 后续计划（不在本计划执行）

| 计划 | 范围 | 关键源/约束 |
|---|---|---|
| Plan 2：研报 | S3.4 `core_view` + S3.7 有效研报 | 东财研报元数据（HTTP 实测可用）；评级/EPS/PE/目标价直接入库，LLM 只抽核心观点；quote ≤500 字 |
| Plan 3：快讯 | S3.3 + 跨源事件去重 | 新浪 7×24、东财快讯（均实测 200）；SimHash + 实体集合 + ±24h 合并 |
| Plan 4：政策 + 人审 | S3.5 + 政策标签 | 官方源（gov.cn/央行/证监会/交易所）当前容器不可达，做成可开关适配器；先由媒体政策内容承载，`publisher_tier` 不得冒充 official |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 旧 `announcements` 读者漏改 | 用只读视图保持列兼容；全量 `pytest` + `journal_service` 回归 |
| 标题解析报告期失败 | 解析失败不写 metric，写 `missing_reason`；不猜报告期 |
| PIT 取到未来修订版 | `f_ann_date <= published_at.date()`；单测构造未来版本反例 |
| 名称匹配误挂实体 | 公告自带 `sec_code` 优先；标题解析只做补充；歧义不挂 |
| 只存链接导致抽取不可复现 | `source_span.quote` + `url` + 结构化 key；不落正文，但可回源复核 |
| 公告轮询高频、失败重试 | 公告轮询不加入 `RETRYABLE_JOBS`；下一 tick 自然重试，失败告警沿用 `_alert_job_failure` |
