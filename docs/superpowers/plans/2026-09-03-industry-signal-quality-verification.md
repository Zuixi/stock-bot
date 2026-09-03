# Industry Signal Quality Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为行业投研工作台增加 registry 驱动的数据质量门控、不可变信号事件，以及猪周期 30/90 个自然日的确定性多指标验证闭环。

**Architecture:** 指标继续统一写入 `industry_metrics`；新建质量快照、信号事件、验证记录三张 PostgreSQL 表。ingest 在派生指标完成后评估质量，正式行业关键输入不足时保留最近有效信号但不生成新信号；有效信号发生 signal/phase 转换时创建事件，并由独立纯函数验证器在窗口到期后评分。Dashboard 只读这些持久化结果，前端用非阻断质量提示和事件时间线展示。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.0 async、PostgreSQL JSONB、Alembic、pytest、React 18、TypeScript、Ant Design 5、TanStack Query、Playwright、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-09-03-industry-signal-quality-verification-design.md`

## Global Constraints

- 所有质量、信号和验证结果由确定性数据/规则构建，不使用 AI。
- 不做文件形式的原始响应归档；新增状态全部写 PostgreSQL。
- Dashboard GET 与 signal-events GET 必须只读，不能触发重算或写库。
- 正式行业关键输入不合格时不得把“数据缺失”解释为“萧条/空仓”，也不得覆盖最近有效信号。
- 第一版只为 `pig` 的 `买入`、`卖出`事件创建 30d/90d 验证；broiler 明确为 demo，不生成正式验证。
- 验证窗口为自然日，日度指标宽限 7 天，月度指标宽限 45 天。
- Repository/service 不提交事务；API dependency、worker、scheduler 保持提交所有权。
- 新增表、字段与 DTO 使用 snake_case；前端 API mapper 集中转换为 camelCase。
- 每项代码修改需补测试；最后更新 `docs/Changelog.md` 和 `docs/references/best-practices.md`。

## File Structure

### Backend creates

- `backend/app/services/industry_data_quality.py`：纯函数质量分类、行业级聚合与可序列化结果。
- `backend/app/services/industry_signal_verification.py`：事件判定、验证评分、到期验证编排。
- `backend/app/migrations/versions/f7a8b9c0d1e2_add_industry_signal_quality_verification.py`：三张新表及约束。
- `backend/tests/test_industry_data_quality.py`：质量规则单测。
- `backend/tests/test_industry_signal_verification.py`：事件与验证纯函数/编排单测。

### Backend modifies

- `backend/app/services/industry_registry.py`：质量和验证配置 dataclass 及 pig/broiler 声明。
- `backend/app/models/industry_research.py`、`backend/app/models/__init__.py`：三张 ORM 模型与导出。
- `backend/app/repositories/industry_metric_repo.py`：质量、事件、验证 CRUD/幂等查询。
- `backend/app/services/industry_metric_service.py`：ingest 编排、门控、Dashboard 聚合；移除 GET 写库兜底。
- `backend/app/schemas/industry.py`：质量、事件、验证 DTO 与可空信号。
- `backend/app/api/v1/industries.py`：signal-events 只读端点。
- `backend/app/workers/industry_metrics_worker.py`：提交后 Dashboard cache invalidation。
- `backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`：17:20 到期验证任务和提交后缓存清理。
- `backend/tests/test_cycle_engine.py`、`backend/tests/test_industry_e2e.py`：门控与 API 合约回归。

### Frontend creates

- `frontend/src/features/industry-research/components/DataQualityBanner.tsx`：质量与 stale signal 非阻断提示。

### Frontend modifies

- `frontend/src/shared/api/industryResearch.ts`：DTO、mapper、事件/验证类型。
- `frontend/src/pages/research-workbench/index.tsx`：质量提示接入。
- `frontend/src/features/industry-research/components/SignalPanel.tsx`：事件和 30d/90d 结果展示。
- `frontend/e2e/research.spec.ts`：质量/验证状态的 mock contract E2E 与真实栈回归。
- `frontend/package.json`：补齐现有 ESLint 脚本所需开发依赖；若仓库无 ESLint 配置则新增 `frontend/eslint.config.js`，规则只覆盖当前代码可通过的 TypeScript/React 基线。

---

### Task 1: Registry-driven data quality core

**Files:**
- Create: `backend/app/services/industry_data_quality.py`
- Create: `backend/tests/test_industry_data_quality.py`
- Modify: `backend/app/services/industry_registry.py`
- Test: `backend/tests/test_industry_data_quality.py`

**Interfaces:**
- Consumes: ORM-like selected metric rows exposing `source`, `freq`, `period`, `value`, plus optional company coverage counts.
- Produces:
  - `MetricQualityDef` fields on `MetricDef` via direct fields named in the spec.
  - `MetricQualityResult(metric_key, status, source, period, age_days, reason, entity_coverage)`.
  - `IndustryQualityResult(status, signal_ready, ready_count, missing_count, stale_count, rejected_count, partial_count, details)`.
  - `assess_metric_quality(metric, row, *, as_of, entity_coverage=None, for_signal=False) -> MetricQualityResult`.
  - `aggregate_industry_quality(cfg, results) -> IndustryQualityResult`.

- [x] **Step 1: Write failing registry and metric quality tests**

Add explicit tests for:

```python
def test_daily_metric_becomes_stale_after_max_age():
    metric = replace(PIG_INDUSTRY.metric("hog_price"), max_age_days=7)
    row = SimpleNamespace(source="akshare_soozhu", period=date(2026, 8, 20), value=13.5)
    result = assess_metric_quality(metric, row, as_of=date(2026, 9, 3), for_signal=True)
    assert result.status == "stale"
    assert result.age_days == 14


def test_mock_source_is_rejected_for_formal_signal():
    metric = PIG_INDUSTRY.metric("hog_price")
    row = SimpleNamespace(source="mock", period=date(2026, 9, 3), value=13.5)
    result = assess_metric_quality(metric, row, as_of=date(2026, 9, 3), for_signal=True)
    assert result.status == "source_rejected"


def test_missing_required_signal_metric_makes_signal_unavailable():
    results = ready_pig_results_except("sow_inventory_mom", status="missing")
    quality = aggregate_industry_quality(PIG_INDUSTRY, results)
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_broiler_is_explicit_demo_not_formal_ready():
    quality = aggregate_industry_quality(BROILER_INDUSTRY, demo_ready_results())
    assert quality.status == "demo"
    assert quality.signal_ready is False
```

- [x] **Step 2: Run the new tests and verify failure**

Run:

```bash
cd backend
uv run --extra dev pytest tests/test_industry_data_quality.py -q
```

Expected: import/attribute failures because quality types and registry fields do not exist.

- [x] **Step 3: Add registry declarations**

Implement frozen dataclasses:

```python
@dataclass(frozen=True)
class VerificationRuleDef:
    metric_key: str
    direction: str
    threshold_pct: float | None
    weight: int
    required: bool = True
    grace_days: int = 7


@dataclass(frozen=True)
class VerificationHorizonDef:
    days: int
    rules: tuple[VerificationRuleDef, ...]


@dataclass(frozen=True)
class SignalVerificationConfig:
    methodology_version: str
    supported_signals: tuple[str, ...]
    horizons: tuple[VerificationHorizonDef, ...]
```

Add the six quality fields defined in the spec to `MetricDef`, and add `signal_quality_required` plus optional `verification` to `IndustryConfig`. Configure pig required metrics and pig-cycle-v1 30/90d rules; configure broiler as demo/no verification.

- [x] **Step 4: Implement pure quality assessment**

Use `(as_of - period).days`, selected row provenance, metric flags, and entity coverage. Keep domain warning bands separate from quality status. `aggregate_industry_quality` must return `unavailable` only when a formal signal-required metric is not ready; dashboard-only problems produce `degraded`.

- [x] **Step 5: Run focused tests and registry regressions**

Run:

```bash
cd backend
uv run --extra dev pytest tests/test_industry_data_quality.py tests/test_industry_generalization.py tests/test_industry_source_priority.py -q
```

Expected: all pass.

- [x] **Step 6: Commit Task 1**

```bash
git add backend/app/services/industry_registry.py backend/app/services/industry_data_quality.py backend/tests/test_industry_data_quality.py
git commit -m "feat(backend): add industry data quality rules"
```

---

### Task 2: Persistence models and repositories

**Files:**
- Create: `backend/app/migrations/versions/f7a8b9c0d1e2_add_industry_signal_quality_verification.py`
- Modify: `backend/app/models/industry_research.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/repositories/industry_metric_repo.py`
- Modify: `backend/tests/test_industry_signal_verification.py`

**Interfaces:**
- Consumes: Task 1 serializable quality result dictionaries.
- Produces:
  - ORM `IndustryDataQualitySnapshot`, `IndustrySignalEvent`, `IndustrySignalEvaluation`.
  - `upsert_quality_snapshot(db, row)` and `latest_quality_snapshot(db, industry_key)`.
  - `latest_signal_event(db, industry_key)`, `create_signal_event(db, row)`, `list_signal_events(db, industry_key, limit)`.
  - `upsert_signal_evaluation(db, row)`, `list_due_signal_evaluations(db, industry_key, as_of)`, `list_event_evaluations(db, event_ids)`.

- [x] **Step 1: Write failing model/constraint tests**

Assert:

```python
assert named_unique(IndustryDataQualitySnapshot, "uq_industry_quality_date") == {
    "industry_key", "as_of"
}
assert named_unique(IndustrySignalEvent, "uq_industry_signal_event") == {
    "industry_key", "event_date", "signal_type", "phase"
}
assert named_unique(IndustrySignalEvaluation, "uq_industry_signal_evaluation") == {
    "signal_event_id", "horizon_days", "methodology_version"
}
```

Also assert `IndustrySignalEvaluation.signal_event_id` has `ondelete="CASCADE"` and status/score/snapshots exist.

- [x] **Step 2: Run focused tests and verify failure**

```bash
cd backend
uv run --extra dev pytest tests/test_industry_signal_verification.py -q
```

Expected: model imports fail.

- [x] **Step 3: Add ORM models and migration**

Use PostgreSQL JSONB for `details`, `basis`, `basis_periods`, `quality_snapshot`, `start_snapshot`, `end_snapshot`, `criteria_results`, and `insufficient_reasons`. Use explicit indexes for industry/date and due-status lookup. Migration `down_revision` must be `e6f7a8b9c0d1`.

- [x] **Step 4: Add idempotent repositories**

Quality and evaluation methods use PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`; signal events use `ON CONFLICT DO NOTHING ... RETURNING` so immutable events are never rewritten. Due query selects `status='pending'` and `target_date <= as_of` ordered by target date.

- [x] **Step 5: Verify migration metadata and repository tests**

```bash
cd backend
uv run --extra dev pytest tests/test_industry_signal_verification.py -q
uv run alembic heads
```

Expected: tests pass and one head equals `f7a8b9c0d1e2`.

- [x] **Step 6: Commit Task 2**

```bash
git add backend/app/migrations/versions/f7a8b9c0d1e2_add_industry_signal_quality_verification.py backend/app/models backend/app/repositories/industry_metric_repo.py backend/tests/test_industry_signal_verification.py
git commit -m "feat(backend): persist signal quality and evaluations"
```

---

### Task 3: Signal gating, transition events, and deterministic verification

**Files:**
- Create: `backend/app/services/industry_signal_verification.py`
- Modify: `backend/app/services/industry_metric_service.py`
- Modify: `backend/tests/test_industry_data_quality.py`
- Modify: `backend/tests/test_industry_signal_verification.py`
- Modify: `backend/tests/test_cycle_engine.py`

**Interfaces:**
- Consumes: Task 1 quality assessment, Task 2 repositories, `cycle_engine.CycleInput/Output`, registry verification config.
- Produces:
  - `assess_current_quality(db, cfg, *, as_of) -> IndustryQualityResult`.
  - `evaluate_and_store_signal(db, cfg, *, quality, effective_date) -> SignalUpdateResult` where fields include `signal`, `updated`, `stale`, `event`.
  - `ensure_signal_event(db, cfg, signal_row, *, basis_periods, quality) -> IndustrySignalEvent | None`.
  - `score_verification(rules, start_snapshot, end_snapshot) -> VerificationScore`.
  - `run_due_signal_evaluations(db, cfg, *, as_of) -> EvaluationRunResult`.

- [x] **Step 1: Write failing signal gate tests**

Use `AsyncMock` to prove:

```python
quality = IndustryQualityResult(status="unavailable", signal_ready=False, ...)
result = await evaluate_and_store_signal(db, PIG_INDUSTRY, quality=quality, effective_date=today)
cycle_evaluator.assert_not_called()
repo.upsert_signal.assert_not_awaited()
assert result.updated is False
assert result.stale is True
assert result.signal is previous_signal
```

Add tests that demo broiler can retain demo signal behavior but never creates formal evaluations.

- [x] **Step 2: Write failing event idempotency tests**

Cover first valid signal, same-state rerun, signal change, and phase-only change. Assert only changes create an event and each supported event gets exactly two pending evaluations at 30/90 days.

- [x] **Step 3: Write failing verification scorer tests**

Build explicit snapshots for:

- buy score 100 → confirmed;
- buy score 55 → partially_confirmed;
- sell score below 40 → invalidated;
- required monthly evidence missing after grace → inconclusive;
- target not reached → remains pending;
- first observation on/after target is selected, never an observation before target.

- [x] **Step 4: Run focused tests and verify failure**

```bash
cd backend
uv run --extra dev pytest tests/test_industry_data_quality.py tests/test_industry_signal_verification.py tests/test_cycle_engine.py -q
```

Expected: new orchestration/scorer symbols missing.

- [x] **Step 5: Implement quality selection and basis periods**

Reuse `_pick_row()` semantics so quality evaluates the same selected source/frequency that the engine consumes. Capture each selected input’s `period` in `basis_periods`; do not use response `as_of` as the metric date. Persist the quality snapshot before signal gating.

- [x] **Step 6: Implement signal gate and events**

Change ingest order to quality → conditional signal update → conditional event creation → due evaluation scan. Return `quality`, `signal_updated`, `signal_stale`, `event_created`, and evaluation counts in ingest result. Remove the Dashboard GET fallback that calls `evaluate_and_store_signal` when no row exists.

- [x] **Step 7: Implement deterministic due evaluation**

For each due record, load the frozen event snapshots/rules, query metric history for the first eligible point on or after target date, observe per-rule grace days, calculate per-criterion results and aggregate verdict. Never re-resolve a different methodology version from current registry when the stored evaluation already has frozen rules.

- [x] **Step 8: Run focused and existing industry tests**

```bash
cd backend
uv run --extra dev pytest tests/test_cycle_engine.py tests/test_industry_data_quality.py tests/test_industry_signal_verification.py tests/test_industry_fetchers.py tests/test_industry_rollup.py tests/test_industry_batch_import.py tests/test_industry_generalization.py -q
```

Expected: all pass.

- [x] **Step 9: Commit Task 3**

```bash
git add backend/app/services/industry_metric_service.py backend/app/services/industry_signal_verification.py backend/tests
git commit -m "feat(backend): gate and verify industry signals"
```

---

### Task 4: API contracts and scheduled evaluation

**Files:**
- Modify: `backend/app/schemas/industry.py`
- Modify: `backend/app/api/v1/industries.py`
- Modify: `backend/app/workers/industry_metrics_worker.py`
- Modify: `backend/app/scheduler/jobs.py`
- Modify: `backend/app/scheduler/runner.py`
- Modify: `backend/tests/test_industry_e2e.py`
- Test: `backend/tests/test_industry_signal_verification.py`

**Interfaces:**
- Consumes: Task 3 result objects and repositories.
- Produces:
  - `DataQualityOut`, `MetricQualityOut`, `SignalEvaluationOut`, `SignalEventOut`, `VerificationSummaryOut`.
  - `DashboardOut.signal: SignalOut | None`, `signal_is_stale`, `data_quality`, `signal_events`, `verification_summary`.
  - `GET /api/v1/industries/{industry_key}/signal-events?limit=20`.
  - Scheduler job `industry_signal_evaluation_job()` at 17:20 Asia/Shanghai.

- [x] **Step 1: Add failing schema and dashboard contract tests**

Assert JSON contains:

```python
assert body["data_quality"]["status"] in {"healthy", "degraded", "unavailable", "demo"}
assert isinstance(body["signal_is_stale"], bool)
assert "signal_events" in body
assert "verification_summary" in body
```

For each event evaluation assert `horizon_days`, `status`, `target_date`, optional `score`, and `criteria_results`.

- [x] **Step 2: Add failing signal-events endpoint tests**

Verify unknown industry returns 404, `limit` bounds return 422, ordering is newest first, and repeated GET does not change event/evaluation counts.

- [x] **Step 3: Implement schemas, mappers, and endpoint**

Keep historical audit JSON in backend persistence but expose only user-facing evidence fields. Compute verification summary from non-pending events; do not show a percentage when completed directional evaluations are fewer than 5.

- [x] **Step 4: Add scheduler job and post-commit cache invalidation**

Implement evaluation scan after securities refresh at 17:20. Worker and scheduler must call `await db.commit()` before deleting `industry:{industry_key}:dashboard`. Redis deletion failure remains non-fatal.

- [x] **Step 5: Run backend unit, lint, and type checks**

```bash
cd backend
uv run --extra dev pytest -m "not e2e" -q
uv run --extra dev ruff check .
uv run --extra dev mypy app
```

Expected: all pass. If existing tests are not correctly marked, run the explicit offline test list and document the baseline marker defect; do not hide real failures.

- [x] **Step 6: Commit Task 4**

```bash
git add backend/app/schemas/industry.py backend/app/api/v1/industries.py backend/app/workers/industry_metrics_worker.py backend/app/scheduler backend/tests
git commit -m "feat(api): expose signal quality verification"
```

---

### Task 5: Frontend quality banner and signal verification timeline

**Files:**
- Create: `frontend/src/features/industry-research/components/DataQualityBanner.tsx`
- Modify: `frontend/src/shared/api/industryResearch.ts`
- Modify: `frontend/src/pages/research-workbench/index.tsx`
- Modify: `frontend/src/features/industry-research/components/SignalPanel.tsx`
- Modify: `frontend/package.json`
- Create if absent: `frontend/eslint.config.js`
- Modify: `frontend/e2e/research.spec.ts`

**Interfaces:**
- Consumes: Task 4 Dashboard API contract.
- Produces: camelCase `DataQuality`, `MetricQuality`, `SignalEvent`, `SignalEvaluation`, `VerificationSummary`; accessible `DataQualityBanner`; event-driven `SignalPanel`.

- [x] **Step 1: Add Playwright mocked-state tests first**

Use `page.route("**/api/v1/industries/pig/dashboard", ...)` with a complete fixture copied from the API contract. Cover:

```text
unavailable + signal_is_stale → warning banner and “最近一次有效信号” text
confirmed 30d evaluation → event timeline shows “30天 已确认” and score
pending 90d evaluation → target date shown
inconclusive → “证据不足” reason shown
healthy → compact quality status without warning alert
```

Add stable `data-testid` names: `industry-data-quality`, `signal-event-timeline`, `signal-evaluation-30`, `signal-evaluation-90`.

- [x] **Step 2: Run new E2E tests against current UI and verify failure**

With a local frontend dev server or final Docker stack:

```bash
cd frontend
npx playwright test e2e/research.spec.ts --grep "data quality|signal verification"
```

Expected: selectors/text absent.

- [x] **Step 3: Extend API types and mapper**

Map every snake_case field centrally. Preserve backend `coverage` instead of dropping it. `signal` must be nullable. No component may access backend snake_case keys directly.

- [x] **Step 4: Implement `DataQualityBanner`**

Use Ant Design `Alert`, `Tag`, `Progress`, `Collapse` or `Descriptions`. The banner must be non-blocking, distinguish domain warning from data quality, show at most three issues before expansion, and include the stable test ID.

- [x] **Step 5: Refactor `SignalPanel` to event history**

Keep current signal card, add stale disclosure, replace repeated daily history with event timeline, display horizon badges and verdict copy. Do not calculate accuracy client-side; render backend summary counts.

- [x] **Step 6: Fix reproducible frontend lint tooling**

Add the minimum ESLint 9 flat-config dependencies compatible with TypeScript/React (`eslint`, `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`, `globals`). Configure generated/dist paths ignored and rules matching current Vite React baseline. Do not weaken TypeScript build checks.

- [x] **Step 7: Run frontend checks**

```bash
cd frontend
npm install --include=dev --dangerously-allow-all-scripts
npm run lint
npm run build
```

Expected: all pass.

- [x] **Step 8: Commit Task 5**

```bash
git add frontend/package.json frontend/package-lock.json frontend/eslint.config.js frontend/src frontend/e2e/research.spec.ts
git commit -m "feat(frontend): show signal data quality and verification"
```

---

### Task 6: Docker database/API/E2E verification

**Files:**
- Modify only when a verified integration defect requires it.
- Test: `backend/tests/test_industry_e2e.py`
- Test: `frontend/e2e/research.spec.ts`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: migrated Docker stack and passing real end-to-end evidence.

- [x] **Step 1: Build and start the complete stack**

From repository root:

```bash
docker compose build
docker compose up -d --wait
```

Expected: postgres, redis, rabbitmq, migrate, api, worker, scheduler, frontend healthy; migration completes successfully.

- [x] **Step 2: Verify migration and runtime schema**

```bash
docker compose exec api alembic current
docker compose exec postgres psql -U stock_bot -d stock_bot -c '\d industry_data_quality_snapshots'
docker compose exec postgres psql -U stock_bot -d stock_bot -c '\d industry_signal_events'
docker compose exec postgres psql -U stock_bot -d stock_bot -c '\d industry_signal_evaluations'
```

Expected: current revision `f7a8b9c0d1e2`; all constraints/indexes present.

- [x] **Step 3: Run complete backend tests against Docker API**

```bash
cd backend
TEST_API_URL=http://localhost:8000 uv run --extra dev pytest
```

Expected: all backend tests pass; network-marked external tests may remain explicitly skipped.

- [x] **Step 4: Run complete Playwright suite**

```bash
cd frontend
E2E_BASE_URL=http://localhost:3000 npm run test:e2e
```

Expected: all navigation, K-line, research, quality, and verification tests pass.

- [x] **Step 5: Inspect container logs for hidden failures**

```bash
docker compose logs --since=10m api worker scheduler migrate
```

Expected: no traceback, migration error, repeated scheduler failure, or cache invalidation exception.

- [x] **Step 6: Run final static verification**

```bash
cd backend
uv run --extra dev ruff check .
uv run --extra dev mypy app
cd ../frontend
npm run lint
npm run build
```

Expected: all pass.

- [x] **Step 7: Commit integration fixes if needed**

```bash
git add <only-files-changed-for-verified-integration-defects>
git commit -m "fix: complete signal verification integration"
```

Skip the commit if no files changed.

---

### Task 7: Documentation, final review, commit, and push

**Files:**
- Modify: `docs/Changelog.md`
- Modify: `docs/references/best-practices.md`
- Modify: this plan checkbox status as tasks complete.

**Interfaces:**
- Consumes: verified implementation and test results.
- Produces: durable project documentation and pushed feature branch.

- [x] **Step 1: Update documentation**

Append one changelog entry summarizing data-quality gating, immutable signal events, 30/90d verification, API/UI, and involved modules. Append one best-practice sentence:

```text
周期规则信号应在生成前通过 registry 驱动的存在性/新鲜度/来源质量门控，并把“每日状态”与“状态转换事件”分开持久化后再按冻结规则多窗口回评，避免把数据缺失误判为业务阶段、把连续同一信号重复计为独立样本。
```

- [x] **Step 2: Run verification-before-completion review**

Check:

```bash
git status --short
git diff --check
git log --oneline --decorate -10
```

Confirm no `.env`, `.playwright-mcp`, `.superpowers`, coverage file, build artifact, or unrelated user file is staged.

- [x] **Step 3: Commit docs and plans**

```bash
git add docs/Changelog.md docs/references/best-practices.md docs/superpowers/specs/2026-09-03-industry-signal-quality-verification-design.md docs/superpowers/plans/2026-09-03-industry-signal-quality-verification.md
git commit -m "docs: record industry signal verification design"
```

- [x] **Step 4: Push the feature branch**

```bash
git push -u origin feat/industry-signal-verification
```

Expected: branch is available on origin with all implementation commits.

- [x] **Step 5: Report final outcome**

Report branch name, commit list, exact verification commands/results, any skipped external-network test, and note that the original main worktree’s untracked tool directories were untouched.
