---
status:      completed
scope:       实现 S9 决策日志与归因（P1）：2 张表 + 自动快照 + 到期结果 + 五类归因 + `/me?tab=journal`；不做持仓记账、不做推送
touches:     backend/app/models/, backend/app/migrations/versions/, backend/app/services/, backend/app/api/v1/, backend/app/schemas/, backend/tests/, frontend/src/pages/me/, frontend/src/features/journal/, frontend/src/shared/api/, frontend/e2e/, docs/
updated:     2026-10-18
next-action: -
---

# 决策日志与归因实现计划

> **For agentic workers:** 逐任务执行，每任务自带测试周期；契约以 [S9](../docs/specs/S9-decision-journal.md) 为准。

**Goal:** 用户 30 秒记录一次判断（3 行必填），系统自动附快照、到期算结果、把"错"分成五类，长期看出自己最常犯的错。

**Architecture:** 2 张表（entries + reviews，append-only）→ 创建时 `build_snapshot` 写入不可变快照 → 到期由 `evaluate_entry` 算结果 →
用户选归因写入 reviews（`unique(entry_id)`）→ `stats` 聚合。前端挂在 `/me?tab=journal`（**不新增路由**）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + Pydantic v2 + pytest；React 18 + TS + AntD 5 + React Query + Playwright。

## Global Constraints

- **仅 2 张新表、0 新 cron**；不建持仓/成本表。
- **不可篡改**：entries 的 `thesis/invalidation/horizon_days/symbol` 创建后不可改；reviews `unique(entry_id)`，二次复盘 409。
- **3 行必填**：`thesis ≤200` · `invalidation`（价格阈值或日期）· `horizon_days ∈ {7,30,90}`；缺一 422。
- **缺数即 null**：快照字段缺失用 `null + missing_reason`，**不得用 0**。
- **不构成投资建议**：产品文案沿用 S8 禁用词表；用户自由文本不扫描，但不得被加工成推荐。
- 权限：**登录即可**（只读自己的数据），不要求 `research:read`。
- 后端 uv cache：`cd backend && UV_CACHE_DIR=.uv-cache uv run …`。

---

### Task 1: 迁移 + 模型

**Files:** Create `backend/app/migrations/versions/<rev>_decision_journal.py`、`backend/app/models/journal.py`
**Produces:** `DecisionJournalEntry`、`DecisionJournalReview` ORM；DDL 逐字照 S9.2。

- [ ] 写迁移（2 表 + 4 个 index + check 约束），`alembic upgrade head` 本地通过
- [ ] 模型与迁移逐列对齐（`__table_args__` 含 check/unique）
- [ ] 测试：`tests/test_journal_model_db.py` —— 越界 thesis/`horizon_days`/`verdict` 被 DB 拒绝；同 entry 二次 review 抛 IntegrityError
- [ ] `UV_CACHE_DIR=.uv-cache uv run --extra dev pytest tests/test_journal_model_db.py -q`

### Task 2: 快照与结果计算（服务层）

**Files:** Create `backend/app/services/journal_service.py`；Test `backend/tests/test_journal_service.py`
**Produces:**
- `async def build_snapshot(db, *, symbol: str | None, as_of: date) -> dict`（≤5 查询，字段见 S9.3）
- `async def evaluate_entry(db, entry, *, as_of: date | None = None) -> dict`（`outcome_pct/bench_pct/excess_pct/invalidation_hit`）
- `async def create_entry(db, user_id, payload) -> DecisionJournalEntry`
- `async def list_entries(db, user_id, *, status: str | None) -> list[...]`（`open/due/reviewed`）
- `async def review_entry(db, user_id, entry_id, verdict, note) -> DecisionJournalReview`
- `async def stats(db, user_id) -> dict`
- [ ] 测试：快照缺数 → `null + missing_reason`；`horizon` 到期日 = `created_at::date + horizon_days`
- [ ] 测试：**PIT 对照** —— 把 `snapshot.price` 换成复盘日价格，`outcome_pct` 必须变（变红即证明用的是快照价）
- [ ] 测试：`status` 三态划分（未到期 open / 到期 due / 已复盘 reviewed）
- [ ] 测试：`stats.by_attribution` 合计 = `reviewed_n`；`reviewed_n < 5` 时占比为 `null`

### Task 3: API + schema

**Files:** Create `backend/app/schemas/journal.py`、`backend/app/api/v1/journal.py`；Modify `backend/app/api/v1/__init__.py`
**Produces:**
- `POST /api/v1/journal`（201）· `GET /api/v1/journal?status=` · `GET /api/v1/journal/{id}` · `POST /api/v1/journal/{id}/review`（201）· `GET /api/v1/journal/stats`
- 409：已复盘条目二次复盘；404：非本人条目；422：字段越界
- [ ] 测试 `tests/test_journal_api.py`：401（未登录）/ 201 / 422（缺 thesis）/ 422（horizon=15）/ 409 / 404 / stats 形状
- [ ] `grep` 断言：router 里没有 `@router.patch|put|delete`（不可篡改）

### Task 4: 后端收口

- [ ] `ruff check` + `ruff format --check` + `mypy app/services/journal_service.py app/api/v1/journal.py app/models/journal.py`
- [ ] 全量 `pytest -q`（排除 e2e/bench）
- [ ] 提交（只 add 自己的文件）

### Task 5: 前端 `/me?tab=journal`

**Files:** Create `frontend/src/shared/api/journal.ts`、`frontend/src/features/journal/components/{JournalPanel.tsx,JournalPanel.css,EntryForm.tsx,ReviewModal.tsx}`；Modify `frontend/src/pages/me/index.tsx`
**契约（API）**
```ts
type Verdict = "thesis_wrong"|"timing_wrong"|"discipline_break"|"right_for_wrong_reason"|"luck";
POST /api/v1/journal  { symbol?: string; kind?: string; thesis: string;
                        invalidation: {kind:"price_below"|"price_above"|"date_only"; value:number|string};
                        horizon_days: 7|30|90 }  → entry
GET  /api/v1/journal?status=open|due|reviewed → { items: [...], snapshot_summary: {...} }
POST /api/v1/journal/{id}/review { verdict: Verdict; note?: string } → review
GET  /api/v1/journal/stats → { n, reviewed_n, by_verdict, by_attribution, avg_excess_pct, discipline_break_share }
```
- [ ] 三块布局：记一笔 / 待复盘（`due`）/ 已复盘（含归因标签 + 统计卡）
- [ ] 表单：3 必填（thesis 字数计数器、invalidation 二选一 + 数值、horizon 三选一）；缺字段时**按钮禁用**并给出缺项提示
- [ ] 复盘弹窗：自动结果（outcome/bench/excess/invalidation_hit）只读展示 + **归因五选一必选** + note
- [ ] 已复盘列表的每条带归因标签；统计卡：`reviewed_n < 5` 显示"样本不足（<5 条）"，不显示占比
- [ ] `npx tsc --noEmit`

### Task 6: e2e（全 mock，确定性）

**Files:** Create `frontend/e2e/decisionJournal.spec.ts`（≥5 条）
- [ ] 表单：3 必填缺一 → 提交按钮禁用；填满 → 提交后列表出现该条
- [ ] 待复盘：`due` 条目显示"复盘"按钮 → 打开弹窗 → 未选归因时确认禁用 → 选 `discipline_break` 后提交 → 条目移到已复盘
- [ ] 统计：`reviewed_n=3` 显示"样本不足"；`reviewed_n=8` 显示占比
- [ ] 已复盘条目**没有**编辑/删除入口（断言无 `button:has-text("编辑")`）
- [ ] `E2E_BASE_URL=http://localhost npx playwright test decisionJournal.spec.ts`

### Task 7: 文档与集成

- [ ] `docs/features.md` 加「决策日志」行；`docs/specs/README.md` 登记 S9；`plans/index.md` 登记本计划
- [ ] `docs/Changelog.md` 一条
- [ ] 迁移在容器内跑通：`docker compose build api && up -d api worker scheduler`（migrate 容器自动 upgrade）
- [ ] 实机：`docker exec -e PYTHONPATH=/app backend_api python` 建 1 条 + 快照，核对快照字段与到期日
- [ ] `bash scripts/self_review.sh --full`
- [ ] `cd frontend && E2E_BASE_URL=http://localhost npx playwright test`（全量）
- [ ] 截图 `/me?tab=journal`（含 1 条待复盘 + 1 条已复盘）
