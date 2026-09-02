# 行业投研工作台加固（P1-P4 评审修复）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-09-02 代码评审发现的行业投研工作台（feature/industry-research-workbench 分支）缺陷：源优先级、月度聚合缺失、回补参数未贯通、规则引擎缺测试、读路径副作用与契约问题。

**Architecture:** 全部改动落在既有三层结构内——registry（配置）、industry_metric_service/repo（服务与数据）、前端 features/industry-research。不新增表、不新增依赖、不改部署。规则引擎保持纯函数。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + PostgreSQL（backend，uv 管理，Python 3.13）；React + TS + antd + echarts-for-react（frontend，npm）。

**Spec:** 本计划 §Spec（评审结论，绑定有效）；上下文文档 `plans/industry-research-workbench.md`（原始计划，非绑定）与 `docs/design/data-source.md`（数据源口径）。

## Spec（评审结论 — 绑定）

1. registry `sources` 把 mock 排在真实源之前，切真实源后 mock 持续胜出；`lh_future_main` 的 akshare 实际 source 名 `akshare_sina` 未登记。
2. 无日度→月度聚合层；真实源（AKShare）只写 daily 行，dashboard 月度趋势将空白。
3. `ingest_industry_metrics(months)` 参数从未使用；AKShare 抓取器硬编码 `tail(45)`，无法回补历史。
4. `latest_rows_by_metric` DISTINCT ON (metric_key, source) 跨频率取 max(period)，月度行（mock 未来月末日期）压过日度行，latest 语义混乱。
5. 规则引擎复苏分支：`loss=None` 且 `ratio=None` 时仅凭能繁去化即判复苏+买入，与注释声明的"盈亏平衡之上+去化确认"不变量不符，与"关键指标缺失保守判定"精神冲突。
6. 规则引擎零测试（提交声称有场景断言，实际无文件）。
7. `get_dashboard`（GET）内调用 `evaluate_and_store_signal` 写库；ingest 后不失效 dashboard 缓存（TTL 60s，可容忍）。
8. `MetricBatchResponse` 缺 `derived_upserted` 字段（pydantic 静默丢弃）；batch 导入可伪造任意 source；`source_tier` 三元表达式两分支相同（死代码）。
9. history 端点 `months` 参数实际是行数 limit，语义误导。
10. 前端：`EChart` 的 `silent` prop 未实现其文档声明（关闭 tooltip/动画）；工作台页硬编码"演示数据源：mock"与 PHASE_LABELS（后端 payload 已含 phases labels）。
11. mock `_wobble_series` off-by-one：返回 n+1 点、调用方用前 n 点，"日度末点=月度最新值"不变量不成立；`n` 参数未使用。

## Global Constraints

- 分支：所有提交落在 `feature/industry-research-workbench`（当前已检出）。
- 后端测试命令：`cd backend && uv run pytest tests/<file> -v`。新增测试必须是**纯单元测试**（不触 DB/HTTP——现有 conftest 是连真实 API 的集成测试，新测试文件不得使用其 `client` fixture）。
- 前端验证命令：`cd frontend && npm run build`（含 tsc 类型检查）。
- 不新增任何依赖；不改 docker-compose / Dockerfile / nginx。
- 注释与文案用中文，风格与现有代码一致（含 `# noqa` 习惯）。
- Commit message：conventional commits + 中文描述（如 `fix(backend): ...`），与仓库历史一致。
- `MetricDef` 是 frozen dataclass，加字段必须带默认值。
- 每个任务独立可测、独立提交；任务内多步小提交。

---

### Task 1: 规则引擎复苏分支加固 + 纯函数测试套件

**Files:**
- Modify: `backend/app/services/cycle_engine.py:74-100`
- Test: `backend/tests/test_cycle_engine.py`（新建，纯单元，无 DB）

**Interfaces:**
- Consumes: 无（纯函数，Task 1 无前置）。
- Produces: `evaluate_pig_cycle(CycleInput) -> CycleOutput` 行为变更——复苏分支增加盈亏确认条件；后续任务不依赖此内部逻辑，仅依赖既有导出。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_cycle_engine.py`：

```python
"""纯函数测试：猪周期规则引擎（无 DB、无 I/O）。"""

import pytest

from app.services import cycle_engine as ce
from app.services.cycle_engine import CycleInput, evaluate_pig_cycle


def _inp(**kw) -> CycleInput:
    base = dict(
        ratio=7.5, price=16.0, cost=15.0,
        sow_mom_series=[-0.5, -0.4, -0.3],
        ratio_series=[7.0, 7.2, 7.4, 7.5],
    )
    base.update(kw)
    return CycleInput(**base)


class TestPhase:
    def test_overheat_is_prosperity(self):
        out = evaluate_pig_cycle(_inp(ratio=9.6, ratio_series=[9.0, 9.2, 9.4, 9.6]))
        assert out.phase == "prosperity"
        assert out.signal == "卖出"

    def test_deep_loss_is_depression(self):
        out = evaluate_pig_cycle(_inp(price=12.0, cost=15.0, sow_mom_series=[0.5, 0.4]))
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_ratio_below_6_is_depression_even_above_cost(self):
        out = evaluate_pig_cycle(_inp(ratio=5.5, sow_mom_series=[]))
        assert out.phase == "depression"

    def test_profit_plus_derating_is_recovery_buy(self):
        out = evaluate_pig_cycle(_inp())
        assert out.phase == "recovery"
        assert out.signal == "买入"

    def test_profit_expanding_is_prosperity_or_recession(self):
        out = evaluate_pig_cycle(_inp(sow_mom_series=[0.3, 0.4, 0.5]))
        assert out.phase in ("prosperity", "recession")
        assert out.signal == "关注"

    def test_ratio_falling_after_high_is_recession(self):
        out = evaluate_pig_cycle(
            _inp(sow_mom_series=[0.3, 0.4], ratio_series=[8.8, 8.6, 8.4, 7.6])
        )
        assert out.phase == "recession"

    def test_missing_everything_defaults_to_depression(self):
        out = evaluate_pig_cycle(CycleInput())
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_derating_alone_without_profit_evidence_is_conservative(self):
        # 修复目标：价格/成本/猪粮比全缺失时，仅凭去化不得判复苏发买入
        out = evaluate_pig_cycle(CycleInput(sow_mom_series=[-1.0, -1.0, -1.0]))
        assert out.phase == "depression"
        assert out.signal == "空仓"

    def test_missing_cost_but_ratio_above_6_with_derating_is_recovery(self):
        # 猪粮比 >= 6 是引擎自身的盈亏平衡代理口径，可替代成本口径
        out = evaluate_pig_cycle(_inp(cost=None))
        assert out.phase == "recovery"
        assert out.signal == "买入"


class TestSignals:
    def test_depression_with_derating_is_watch(self):
        out = evaluate_pig_cycle(_inp(price=13.0, cost=15.0))
        assert out.phase == "depression"
        assert out.signal == "关注"

    def test_deep_loss_reason_mentions_level1_warning(self):
        out = evaluate_pig_cycle(_inp(ratio=4.8, price=12.0, cost=15.0))
        assert any("一级预警" in r for r in out.reasons)

    def test_positions_come_from_registry_template(self):
        out = evaluate_pig_cycle(_inp())
        assert [p.name for p in out.positions] == ["核心底仓", "波段仓位", "现金储备"]


class TestHelpers:
    def test_count_consecutive_negative_stops_at_none(self):
        assert ce.count_consecutive_negative([0.1, -0.2, None, -0.3, -0.4]) == 2

    def test_count_consecutive_negative_all_positive(self):
        assert ce.count_consecutive_negative([0.1, 0.2]) == 0

    @pytest.mark.parametrize("value,expected", [
        (4.9, "一级预警"), (5.0, "一级预警"), (5.1, "二级预警"),
        (6.0, "二级预警"), (6.1, "正常"), (9.0, "正常"), (9.01, "过度上涨"),
    ])
    def test_band_label_boundaries(self, value, expected):
        from app.services.industry_registry import PIG_INDUSTRY

        m = PIG_INDUSTRY.metric("hog_corn_ratio")
        assert m.band_label(value) == expected
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_cycle_engine.py -v`
Expected: `test_derating_alone_without_profit_evidence_is_conservative` FAIL（当前返回 recovery/买入），其余 PASS。

- [ ] **Step 3: 修复复苏分支**

`backend/app/services/cycle_engine.py`，在 `sow_decline_months` 计算后、阶段判定前新增盈亏确认变量，并修改复苏分支条件：

```python
    sow_decline_months = count_consecutive_negative(inp.sow_mom_series)
    sow_declining = sow_decline_months >= 3
    ratio_low = inp.ratio is not None and inp.ratio < RATIO_LEVEL2
    ratio_deep_loss = inp.ratio is not None and inp.ratio < RATIO_LEVEL1
    ratio_overheat = inp.ratio is not None and inp.ratio > RATIO_OVERHEAT
    # 盈亏平衡确认：成本口径（price≥cost）或猪粮比口径（ratio≥6，引擎自身的代理口径）。
    # 走到复苏分支时亏损与低猪粮比已被排除，故二者任一非空即构成确认；全缺失则不确认。
    breakeven_confirmed = loss is not None or inp.ratio is not None
```

分支改为：

```python
    elif sow_declining and breakeven_confirmed:
        phase = PHASE_RECOVERY
        reasons.append("猪价站上盈亏平衡线，且能繁产能持续去化")
```

（原 `elif sow_declining:` 行整体替换；`loss`/`ratio_low` 等变量计算保持不动。）

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_cycle_engine.py -v`
Expected: 全部 PASS（约 17 项）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/cycle_engine.py backend/tests/test_cycle_engine.py
git commit -m "fix(backend): 猪周期引擎复苏分支增加盈亏确认 + 纯函数测试套件"
```

---

### Task 2: 源优先级修复（mock 永远垫底）+ 真实源切换时清除 mock 行

**Files:**
- Modify: `backend/app/services/industry_registry.py:143-228`（PIG_METRICS 各 sources）、`:53`（字段注释）
- Modify: `backend/app/services/industry_metric_service.py:120-147`（ingest）、`:254-263`（_pick_latest）
- Modify: `backend/app/repositories/industry_metric_repo.py`（新增 delete_mock_rows）
- Test: `backend/tests/test_industry_source_priority.py`（新建，纯单元）

**Interfaces:**
- Consumes: Task 1 无依赖。
- Produces: `repo.delete_mock_rows(db, industry_key) -> int`；`ingest_industry_metrics` 返回 dict 新增键 `"purged_mock": int`（worker/scheduler 只透传日志，不依赖该键）；registry 各指标 `sources` 顺序变更（Task 3/5 消费）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_industry_source_priority.py`：

```python
"""纯单元测试：源优先级裁决与 registry 声明。"""

from datetime import date

from app.models.industry_research import IndustryMetric
from app.services.industry_metric_service import _pick_latest
from app.services.industry_registry import PIG_INDUSTRY


def _row(metric_key, source, period, freq="daily"):
    return IndustryMetric(
        industry_key="pig", stock_id=0, metric_key=metric_key,
        source=source, freq=freq, period=period, value=1.0,
    )


def test_real_source_beats_mock_regardless_of_recency():
    grouped = {
        "hog_price": [
            _row("hog_price", "mock", date(2026, 9, 2)),
            _row("hog_price", "akshare_100ppi", date(2026, 8, 30)),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "hog_price").source == "akshare_100ppi"


def test_mock_always_last_in_registry_sources():
    for m in PIG_INDUSTRY.metrics:
        if len(m.sources) > 1:
            assert m.sources[-1] == "mock", f"{m.key}: {m.sources}"


def test_lh_future_registers_akshare_sina():
    assert "akshare_sina" in PIG_INDUSTRY.metric("lh_future_main").sources


def test_fallback_prefers_most_recent_period():
    # registry 未登记的 source 兜底时按最新 period 取，行为确定
    grouped = {
        "hog_price": [
            _row("hog_price", "manual", date(2026, 1, 1)),
            _row("hog_price", "other", date(2026, 6, 1)),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "hog_price").source == "other"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_industry_source_priority.py -v`
Expected: 前 3 项 FAIL（mock 在前 / akshare_sina 未登记），兜底测试 PASS（现行为 `rows[0]` 恰为 other，改动后仍需保持）。

- [ ] **Step 3: 改 registry sources**

`industry_registry.py` — `MetricDef.sources` 字段注释改为：

```python
    sources: list[str]              # ingest/查询源优先级（高→低）；mock 永远垫底，演示数据不得压过真实源
```

PIG_METRICS 逐项改 `sources`（其余字段不动）：
- `hog_price` / `corn_price` / `soybean_meal_price`：`["akshare_100ppi", "mock"]`
- `pork_wholesale`：`["manual", "mock"]`
- `piglet_price_15kg`：`["manual", "mock"]`
- `lh_future_main`：`["akshare_sina", "mock"]`
- `sow_inventory`：`["stats_gov", "mock"]`
- `industry_cost_avg`：`["manual", "mock"]`
- `msy` / `psy` / `feed_meat_ratio`：`["manual", "mock"]`
- `hog_corn_ratio` / `sow_inventory_mom`：`["derived"]` 不变

- [ ] **Step 4: _pick_latest 兜底确定化**

`industry_metric_service.py` `_pick_latest` 末行 `return rows[0]` 改为：

```python
    return max(rows, key=lambda r: r.period)
```

- [ ] **Step 5: repo 新增 delete_mock_rows + ingest 接入**

`industry_metric_repo.py` 文件头部 import 区补 `delete`（`from sqlalchemy import delete, desc, select`），文件末尾新增：

```python
async def delete_mock_rows(db: AsyncSession, industry_key: str) -> int:
    """Purge demo/mock rows once a real source has landed (mock never masquerades as data)."""
    stmt = delete(IndustryMetric).where(
        IndustryMetric.industry_key == industry_key,
        IndustryMetric.stock_id == 0,
        IndustryMetric.source == "mock",
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
```

`industry_metric_service.py` `ingest_industry_metrics` 中，在 `upserted = await repo.upsert_metrics(db, rows)` 之后、`derived_count = ...` 之前插入：

```python
    purged = 0
    # 真实源首次落库后清除演示数据：宁可空缺也不让 mock 冒充真实值
    if source != "mock" and upserted > 0:
        purged = await repo.delete_mock_rows(db, cfg.key)
```

返回 dict 增加 `"purged_mock": purged`。

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_industry_source_priority.py tests/test_cycle_engine.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/industry_registry.py backend/app/services/industry_metric_service.py backend/app/repositories/industry_metric_repo.py backend/tests/test_industry_source_priority.py
git commit -m "fix(backend): 源优先级 mock 垫底 + 登记 akshare_sina + 真实源落库后清除 mock 行"
```

---

### Task 3: 日度→月度 rollup + latest 按注册频率裁决

**Files:**
- Modify: `backend/app/services/industry_registry.py`（MetricDef 加 `rollup_monthly` 字段；hog_price/corn_price 置 True）
- Modify: `backend/app/services/industry_metric_service.py:165-208`（_compute_derived_metrics 拆两段 upsert）、`:254-263`（_pick_latest 频率过滤）
- Modify: `backend/app/repositories/industry_metric_repo.py:39-51`（DISTINCT ON 加 freq 维度）
- Test: `backend/tests/test_industry_rollup.py`（新建，纯单元）

**Interfaces:**
- Consumes: Task 2 之后的 registry sources 顺序。
- Produces: `MetricDef.rollup_monthly: bool`（默认 False）；纯函数 `_rollup_monthly_rows(cfg, m, rows) -> list[dict]`（Task 4/5 不消费，仅供测试与 ingest 内部使用）；`latest_rows_by_metric` 返回值变为每 (metric, source) 最多两行（daily+monthly），消费方 `_pick_latest` 已兼容。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_industry_rollup.py`：

```python
"""纯单元测试：日度→月度 rollup 与 latest 频率裁决。"""

from datetime import date

from app.models.industry_research import IndustryMetric
from app.services.industry_metric_service import _pick_latest, _rollup_monthly_rows
from app.services.industry_registry import PIG_INDUSTRY


def _row(metric_key, source, period, freq="daily", value=1.0):
    return IndustryMetric(
        industry_key="pig", stock_id=0, metric_key=metric_key,
        source=source, freq=freq, period=period, value=value,
    )


def test_rollup_takes_last_daily_value_per_month():
    rows = [
        _row("hog_price", "akshare_100ppi", date(2026, 7, 10), value=10.0),
        _row("hog_price", "akshare_100ppi", date(2026, 7, 31), value=12.0),
        _row("hog_price", "akshare_100ppi", date(2026, 8, 5), value=13.0),
    ]
    m = PIG_INDUSTRY.metric("hog_price")
    out = _rollup_monthly_rows(PIG_INDUSTRY, m, rows)
    assert [(r["period"], r["value"], r["freq"]) for r in out] == [
        (date(2026, 7, 31), 12.0, "monthly"),
        (date(2026, 8, 31), 13.0, "monthly"),
    ]
    assert all(r["source"] == "akshare_100ppi" and r["source_tier"] == m.tier for r in out)


def test_rollup_marks_extra_and_skips_none_values():
    rows = [
        _row("hog_price", "akshare_100ppi", date(2026, 7, 10), value=None),
        _row("hog_price", "akshare_100ppi", date(2026, 7, 20), value=11.0),
    ]
    out = _rollup_monthly_rows(PIG_INDUSTRY, PIG_INDUSTRY.metric("hog_price"), rows)
    assert len(out) == 1
    assert out[0]["extra"] == {"rollup": "last_daily"}


def test_pick_latest_prefers_registry_freq_over_newer_other_freq():
    # hog_price 注册频率 daily：未来月末的 monthly 行不得压过当日 daily 行
    grouped = {
        "hog_price": [
            _row("hog_price", "mock", date(2026, 9, 30), freq="monthly"),
            _row("hog_price", "mock", date(2026, 9, 2), freq="daily"),
        ]
    }
    picked = _pick_latest(PIG_INDUSTRY, grouped, "hog_price")
    assert picked.freq == "daily" and picked.period == date(2026, 9, 2)


def test_pick_latest_falls_back_to_any_freq_when_registry_freq_absent():
    grouped = {
        "sow_inventory": [
            _row("sow_inventory", "stats_gov", date(2026, 6, 30), freq="monthly"),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "sow_inventory").period == date(2026, 6, 30)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_industry_rollup.py -v`
Expected: 前 3 项 FAIL（`_rollup_monthly_rows` 不存在 / 无频率过滤），第 4 项 PASS。

- [ ] **Step 3: registry 加 rollup_monthly**

`industry_registry.py` `MetricDef` 在 `warn_bands` 之前加：

```python
    rollup_monthly: bool = False    # 日度指标按"每月最后一个日度值"补一条月度行（source 不变）
```

`hog_price` 与 `corn_price` 两个 MetricDef 各加 `rollup_monthly=True,`（放在 `group="quick"` 同段参数区）。

- [ ] **Step 4: service 加纯函数 rollup 并接入派生链**

`industry_metric_service.py` 顶部补 `import calendar`。在 `_compute_derived_metrics` 上方新增模块级纯函数：

```python
def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _rollup_monthly_rows(cfg: IndustryConfig, m: MetricDef, rows: list) -> list[dict]:
    """每日度序列 → 月度行：每月最后一个非空日度值，period=月末，source 原样保留。"""
    by_key: dict[tuple[str, date], object] = {}
    for r in rows:  # rows 为升序
        if r.value is None:
            continue
        by_key[(r.source, _month_end(r.period))] = r
    return [
        {
            "industry_key": cfg.key, "stock_id": 0, "metric_key": m.key,
            "source": source, "source_tier": m.tier, "freq": "monthly",
            "period": period, "value": float(r.value), "unit": m.unit or None,
            "extra": {"rollup": "last_daily"},
        }
        for (source, period), r in sorted(by_key.items())
    ]
```

`_compute_derived_metrics` 改为两段 upsert（rollup 先落库，猪粮比月度序列才能当次读到）：

```python
async def _compute_derived_metrics(db: AsyncSession, cfg: IndustryConfig) -> int:
    """rollup（日→月）+ 派生（猪粮比/能繁环比）— 统一幂等落表."""
    total = 0

    # 1) 日度→月度 rollup：先落库，后续月度派生当次可见
    rollup_rows: list[dict] = []
    for m in cfg.metrics:
        if not m.rollup_monthly:
            continue
        daily = await repo.get_metric_history(db, cfg.key, m.key, limit=4000, freq="daily")
        rollup_rows.extend(_rollup_monthly_rows(cfg, m, daily))
    if rollup_rows:
        total += await repo.upsert_metrics(db, rollup_rows)

    derived: list[dict] = []
    # ……（猪粮比、能繁环比两段计算逻辑保持原样，不动）……
    total += await repo.upsert_metrics(db, derived)
    return total
```

（原函数中 `return await repo.upsert_metrics(db, derived)` 改为累计进 `total` 后返回；`_row` 内部助手与两段计算体原样保留。）

- [ ] **Step 5: repo DISTINCT ON 加 freq + _pick_latest 频率过滤**

`industry_metric_repo.py` `latest_rows_by_metric`：

```python
    stmt = (
        select(IndustryMetric)
        .where(IndustryMetric.industry_key == industry_key, IndustryMetric.stock_id == 0)
        .distinct(IndustryMetric.metric_key, IndustryMetric.source, IndustryMetric.freq)
        .order_by(
            IndustryMetric.metric_key, IndustryMetric.source,
            IndustryMetric.freq, desc(IndustryMetric.period),
        )
    )
```

`industry_metric_service.py` `_pick_latest` 频率过滤（在 by_source 构造前）：

```python
    rows = grouped.get(metric_key, [])
    m = cfg.metric(metric_key)
    if m is None or not rows:
        return None
    # 注册频率优先：月度行不得借月末日期压过日度行
    rows = [r for r in rows if r.freq == m.freq] or rows
```

（后续 `by_source` / 优先级循环 / `max(rows, key=...)` 兜底不变。）

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_industry_rollup.py tests/test_industry_source_priority.py tests/test_cycle_engine.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/industry_registry.py backend/app/services/industry_metric_service.py backend/app/repositories/industry_metric_repo.py backend/tests/test_industry_rollup.py
git commit -m "feat(backend): 日度→月度 rollup 层 + latest 按注册频率裁决"
```

---

### Task 4: months 回补窗口贯通（fetcher/mock/worker/API）

**Files:**
- Modify: `backend/app/services/industry_metric_service.py:66-115`（_fetch_akshare_rows）、`:120-135`（ingest 签名）
- Modify: `backend/app/services/industry_mock_data.py:54-57`（_wobble off-by-one）、`:60-84`（build 签名与月度窗口）
- Modify: `backend/app/workers/industry_metrics_worker.py:30-44`
- Modify: `backend/app/schemas/task.py`（FetchIndustryMetricsRequest 加 months）
- Test: `backend/tests/test_industry_mock_data.py`（新建，纯单元）

**Interfaces:**
- Consumes: Task 3 后的 service 结构。
- Produces: `build_pig_mock_points(industry_key="pig", months=37) -> list[dict]`；`ingest_industry_metrics(..., months: int = 37)`（Task 5 的调用方 dashboard 不传 months，默认值兼容）；`FetchIndustryMetricsRequest.months: int`（worker payload 透传键名 `"months"`）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_industry_mock_data.py`：

```python
"""纯单元测试：mock 序列生成器的窗口与不变量。"""

import random
from datetime import date

from app.services.industry_mock_data import _wobble_series, build_pig_mock_points


def test_wobble_series_exact_length_and_last_point():
    rng = random.Random(7)
    out = _wobble_series([3.0] * 45, rng, 0.01, 45)
    assert len(out) == 45
    assert out[-1] == 3.0  # 末点精确等于基准值


def test_mock_points_respects_months_window():
    rows = build_pig_mock_points("pig", months=12)
    monthly = [
        r for r in rows
        if r["metric_key"] == "hog_price" and r["freq"] == "monthly"
    ]
    assert len(monthly) == 12
    daily = [
        r for r in rows
        if r["metric_key"] == "hog_price" and r["freq"] == "daily"
    ]
    assert len(daily) <= 45


def test_mock_daily_last_equals_monthly_latest():
    rows = build_pig_mock_points("pig", months=37)
    daily = [r for r in rows if r["metric_key"] == "hog_price" and r["freq"] == "daily"]
    monthly = [r for r in rows if r["metric_key"] == "hog_price" and r["freq"] == "monthly"]
    assert daily[-1]["value"] == monthly[-1]["value"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_industry_mock_data.py -v`
Expected: 三项全 FAIL（签名不接受 months / off-by-one / 末点不等）。

- [ ] **Step 3: 修 _wobble_series 与 build 签名**

`industry_mock_data.py`：

```python
def _wobble_series(base: list[float], rng: random.Random, scale: float, n: int) -> list[float]:
    """n 个点：前 n-1 个带抖动，末点精确等于基准值（保证日/月口径一致）。"""
    out = [round(b * (1 + rng.gauss(0, scale)), 3) for b in base[: n - 1]]
    return out + [round(base[-1], 3)]
```

`build_pig_mock_points` 签名与月度窗口：

```python
def build_pig_mock_points(industry_key: str = "pig", months: int = 37) -> list[dict[str, Any]]:
    """Build all mock metric rows (industry-level) for upsert."""
    cfg = reg.PIG_INDUSTRY
    today = date.today()
    rng = random.Random(42)  # noqa: S311 - deterministic demo data
    rows: list[dict[str, Any]] = []
    months = max(1, min(months, _MONTHS))
    # ……（add 助手原样）……

    months_periods = _monthly_periods(today, months)
    base_slice = slice(-months, None)
    for period, price, cost, corn, sow in zip(
        months_periods,
        _PRICE[base_slice], _COST[base_slice], _CORN[base_slice], _SOW[base_slice],
        strict=True,
    ):
        # ……循环体四行 add(...) 原样）……
```

（`_PRICE` 等四个常量、日度/周度/年度段原样不动；日度基准 `daily_specs` 里的 `_PRICE[-1]` 等取值不因切片改变——常量本身不切，只切 zip 输入。）

- [ ] **Step 4: fetcher 与 ingest 贯通 months**

`industry_metric_service.py`：
- `_fetch_akshare_rows(cfg: IndustryConfig, months: int = 37)`；两处 `df.tail(45)` 改为 `df.tail(max(45, months * 31))`。
- `ingest_industry_metrics` 中 mock 分支改 `rows = build_pig_mock_points(cfg.key, months=months)`；akshare 分支改 `rows = await _fetch_akshare_rows(cfg, months=months)`。

- [ ] **Step 5: schema/worker 透传**

`schemas/task.py`：

```python
class FetchIndustryMetricsRequest(BaseModel):
    industry_key: str = "pig"
    source: str | None = Field(default=None, pattern="^(mock|akshare)$")
    months: int = Field(default=37, ge=1, le=120)
```

`workers/industry_metrics_worker.py` `process` 内：

```python
        months = int(payload.get("months", 37))
        result = await industry_metric_service.ingest_industry_metrics(
            db, industry_key=industry_key, source=source, months=months
        )
```

（`task_service.trigger_fetch_industry_metrics` 的 `model_dump(exclude_none=True)` 已带 months，无需改。）

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_industry_mock_data.py tests/test_cycle_engine.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/industry_metric_service.py backend/app/services/industry_mock_data.py backend/app/workers/industry_metrics_worker.py backend/app/schemas/task.py backend/tests/test_industry_mock_data.py
git commit -m "fix(backend): months 回补窗口贯通 fetcher/mock/worker/API + mock 序列 off-by-one"
```

---

### Task 5: 读路径卫生与契约修正（backend 小修合集）

**Files:**
- Modify: `backend/app/services/industry_metric_service.py`（get_dashboard 去常态写、batch 白名单与响应、history 参数改名、去掉未用 cache 参数）
- Modify: `backend/app/api/v1/industries.py`（路由参数同步、batch 校验）
- Modify: `backend/app/schemas/industry.py`（MetricBatchResponse、DashboardOut）
- Test: `backend/tests/test_industry_batch_import.py`（新建，纯单元）

**Interfaces:**
- Consumes: Task 2 的 sources、Task 4 的 ingest 签名（默认参数，不改调用）。
- Produces: `DashboardOut.data_source: str`（前端 Task 6 消费，字段名 `data_source`）；`MetricBatchResponse.derived_upserted: int`、`skipped_invalid_source: list[str]`；history 端点查询参数改名为 `limit`（前端未消费该端点，无兼容负担）；纯函数 `_prepare_batch_rows(cfg, items) -> tuple[list[dict], list[str], list[str]]`。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_industry_batch_import.py`：

```python
"""纯单元测试：batch 导入行准备（白名单/未知指标/source_tier）。"""

from datetime import date

from app.services.industry_metric_service import IMPORT_ALLOWED_SOURCES, _prepare_batch_rows
from app.services.industry_registry import PIG_INDUSTRY


def test_batch_accepts_manual_and_rejects_provider_sources():
    items = [
        {"metric_key": "industry_cost_avg", "period": date(2026, 8, 31), "value": 13.5,
         "source": "manual"},
        {"metric_key": "industry_cost_avg", "period": date(2026, 8, 31), "value": 13.5,
         "source": "akshare_100ppi"},  # 采集适配器专属 source，人工通道不得伪造
        {"metric_key": "sow_inventory", "period": date(2026, 6, 30), "value": 4038.0,
         "source": "stats_gov"},  # 统计局 CSV 导入通道
        {"metric_key": "nope", "period": date(2026, 8, 31), "value": 1.0},
    ]
    rows, unknown, rejected = _prepare_batch_rows(PIG_INDUSTRY, items)
    assert [r["source"] for r in rows] == ["manual", "stats_gov"]
    assert unknown == ["nope"]
    assert rejected == ["industry_cost_avg:akshare_100ppi"]


def test_batch_source_tier_always_from_registry():
    rows, _, _ = _prepare_batch_rows(PIG_INDUSTRY, [
        {"metric_key": "sow_inventory", "period": date(2026, 6, 30), "value": 4038.0,
         "source": "stats_gov"},
    ])
    assert rows[0]["source_tier"] == PIG_INDUSTRY.metric("sow_inventory").tier


def test_import_allowed_sources_constant():
    assert IMPORT_ALLOWED_SOURCES == {"manual", "stats_gov"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_industry_batch_import.py -v`
Expected: FAIL（`IMPORT_ALLOWED_SOURCES` / `_prepare_batch_rows` 不存在）。

- [ ] **Step 3: 抽取 _prepare_batch_rows + 白名单**

`industry_metric_service.py` 模块级常量与纯函数（放在 `batch_upsert_metrics` 上方）：

```python
# 人工/CSV 导入通道允许写库的 source：人工录入 + 统计局 CSV（data-source.md L2 通道）。
# 采集适配器专属 source（akshare_* 等）与 mock/derived 不得经人工通道伪造。
IMPORT_ALLOWED_SOURCES = {"manual", "stats_gov"}


def _prepare_batch_rows(
    cfg: IndustryConfig, items: list[dict]
) -> tuple[list[dict], list[str], list[str]]:
    """校验并规整导入行：返回 (rows, skipped_unknown_metric, skipped_invalid_source)."""
    rows: list[dict] = []
    skipped: list[str] = []
    rejected: list[str] = []
    for item in items:
        m = cfg.metric(item["metric_key"])
        source = item.get("source") or "manual"
        if m is None:
            skipped.append(item["metric_key"])
            continue
        if source not in IMPORT_ALLOWED_SOURCES:
            rejected.append(f"{m.key}:{source}")
            continue
        rows.append({
            "industry_key": cfg.key,
            "stock_id": item.get("stock_id") or 0,
            "metric_key": m.key,
            "source": source,
            "source_tier": m.tier,
            "freq": item.get("freq") or m.freq,
            "period": item["period"],
            "value": item["value"],
            "unit": item.get("unit") or m.unit or None,
            "extra": None,
        })
    return rows, sorted(set(skipped)), sorted(set(rejected))
```

`batch_upsert_metrics` 改为调用它：

```python
async def batch_upsert_metrics(
    db: AsyncSession, industry_key: str, items: list[dict], recompute_derived: bool = False
) -> dict:
    """人工/CSV 导入通道：白名单校验 + 幂等 upsert."""
    cfg = _require_industry(industry_key)
    rows, skipped, rejected = _prepare_batch_rows(cfg, items)
    upserted = await repo.upsert_metrics(db, rows)
    derived = 0
    if recompute_derived:
        derived = await _compute_derived_metrics(db, cfg)
        await evaluate_and_store_signal(db, cfg)
    return {
        "upserted": upserted, "derived_upserted": derived,
        "skipped_unknown_metric": skipped, "skipped_invalid_source": rejected,
    }
```

- [ ] **Step 4: schema 补字段**

`schemas/industry.py`：

```python
class MetricBatchResponse(BaseModel):
    upserted: int
    derived_upserted: int = 0
    skipped_unknown_metric: list[str] = []
    skipped_invalid_source: list[str] = []
```

`DashboardOut` 增加字段（`as_of` 之后）：

```python
    data_source: str = "mock"  # settings.industry_data_source，前端据此展示演示标签
```

- [ ] **Step 5: get_dashboard 去常态写 + data_source**

`industry_metric_service.py` `get_dashboard`：文件头已有 `from app.config import settings`，直接使用——`DashboardOut(...)` 构造参数增加 `data_source=settings.industry_data_source`，不新增 import。

信号段替换（原 `signal_row = await evaluate_and_store_signal(db, cfg)` 一行）：

```python
    # 信号在 ingest 时评估落表；GET 只读。空库引导：从未评估过才补算一次。
    signal_row = await repo.latest_signal(db, cfg.key)
    if signal_row is None:
        signal_row = await evaluate_and_store_signal(db, cfg)
```

- [ ] **Step 6: history 参数 months→limit、去除未用 cache 参数**

`api/v1/industries.py`：
- history 路由参数 `months: int = Query(default=36, ...)` 改为 `limit: int = Query(default=500, ge=1, le=5000, description="返回的最大数据点数")`，调用改 `limit=limit`。
- `list_industries` / `get_latest_metrics` / `get_metric_history` 路由函数去掉 `cache: CacheDep` 参数与调用透传（service 对应函数同步去掉 `cache: CacheClient` 参数）。
- `CacheDep`/`CacheClient` 若因此不再被引用则清理 import。

`industry_metric_service.py`：
- `get_latest_metrics(db, cache, industry_key, group=None)` → `get_latest_metrics(db, industry_key, group=None)`。
- `get_metric_history(db, cache, industry_key, metric_key, months=36, ...)` → `get_metric_history(db, industry_key, metric_key, limit=500, freq=None, source=None)`，内部 `repo.get_metric_history(..., limit=limit, ...)`。
- `list_industries(db, cache)` → `list_industries(db)`。
- dashboard 路由保留 `CacheDep`（真实使用缓存）。

- [ ] **Step 7: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_industry_batch_import.py tests/test_industry_rollup.py tests/test_industry_source_priority.py tests/test_cycle_engine.py tests/test_industry_mock_data.py -v && uv run python -c "import app.main"`
Expected: 全部 PASS，模块导入冒烟通过。

- [ ] **Step 8: 提交**

```bash
git add backend/app/services/industry_metric_service.py backend/app/api/v1/industries.py backend/app/schemas/industry.py backend/tests/test_industry_batch_import.py
git commit -m "fix(backend): 读路径去常态写 + 导入白名单 + 契约字段补齐 + history 参数更名"
```

---

### Task 6: 前端修正（EChart silent / 演示标签 / 去本地相位文案）

**Files:**
- Modify: `frontend/src/shared/ui/EChart.tsx`
- Modify: `frontend/src/pages/research-workbench/index.tsx:16-35,121-143`
- Modify: `frontend/src/shared/api/industryResearch.ts`（BackendDashboard、Dashboard、mapDashboard）

**Interfaces:**
- Consumes: Task 5 的 `DashboardOut.data_source`（JSON 字段 `data_source`）。
- Produces: 无后续消费者。

- [ ] **Step 1: EChart silent 落实文档语义**

`EChart.tsx` 组件体改为：

```tsx
export function EChart({ option, height = 300, silent = false }: Props) {
  const finalOption = silent
    ? { ...option, animation: false, tooltip: { show: false } }
    : option;
  return (
    <ReactECharts
      option={finalOption}
      notMerge
      lazyUpdate
      style={{ height, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
```

- [ ] **Step 2: Dashboard 类型与映射加 dataSource**

`industryResearch.ts`：
- `BackendDashboard` 接口加 `data_source: string;`（`as_of` 之后）。
- `Dashboard` 接口加 `dataSource: string;`。
- `mapDashboard` 返回对象加 `dataSource: d.data_source,`。

- [ ] **Step 3: 工作台页去本地 PHASE_LABELS、演示标签动态化**

`research-workbench/index.tsx`：
- 删除 `PHASE_LABELS` 常量块（`PHASE_COLORS` 保留——颜色是纯展示，registry 不下发）。
- `Workbench` 组件内标签区改为：

```tsx
  const phaseLabel =
    dashboard.cycle.phases.find((p) => p.key === cycle.phase)?.label ?? cycle.phase;
```

Tag 内 `{PHASE_LABELS[cycle.phase] ?? cycle.phase}` 换成 `{phaseLabel}`。
- 演示标签改为条件渲染：

```tsx
          {dashboard.dataSource === "mock" && (
            <Tag style={{ borderRadius: 14, padding: "2px 12px", color: "#86909c", borderStyle: "dashed" }}>
              演示数据源：mock
            </Tag>
          )}
```

- [ ] **Step 4: 构建验证**

Run: `cd frontend && npm run build`
Expected: tsc 与 vite build 成功，零类型错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/shared/ui/EChart.tsx frontend/src/pages/research-workbench/index.tsx frontend/src/shared/api/industryResearch.ts
git commit -m "fix(frontend): EChart silent 关闭交互 + 相位文案改用后端下发 + 演示标签条件渲染"
```

---

### Task 7: 文档收尾（Changelog / best-practices / 计划进度备注）

**Files:**
- Modify: `docs/Changelog.md`（文末按既有格式追加）
- Modify: `docs/references/best-practices.md`（文末追加一条一句话经验）
- Modify: `plans/industry-research-workbench.md`（头部进度备注追加一行）

**Interfaces:**
- Consumes: Task 1-6 的完成状态。
- Produces: 无。

- [ ] **Step 1: Changelog 追加**（置于文件末尾，格式对齐既有条目）

```markdown
## 2026-09-02 - 行业投研工作台评审加固（P1-P4 修复）
- 修复源优先级（mock 垫底）并登记 akshare_sina，真实源落库后自动清除 mock 行；新增日度→月度 rollup 与 latest 按注册频率裁决；months 回补窗口贯通 fetcher/worker/API；猪周期引擎复苏分支增加盈亏确认并补齐纯函数测试套件；dashboard GET 去常态写库、batch 导入 source 白名单、响应补 derived_upserted、history 参数更名 limit；前端 EChart silent 落实、相位文案与演示标签改后端驱动
- 涉及模块：backend/services（registry/metric_service/mock_data/cycle_engine）、backend/repositories、backend/schemas、backend/api、backend/workers、frontend/shared/ui、frontend/pages/research-workbench
```

- [ ] **Step 2: best-practices 追加一条**（对齐该文件既有句式）

```markdown
- 演示/占位数据源必须在源优先级中垫底，并在真实源首次落库时清除，否则切换数据源后会继续冒充真实数据。
```

- [ ] **Step 3: 计划进度备注**

`plans/industry-research-workbench.md` 头部 `> **进度备注**（2026-08-31）` 段落之后追加一行：

```markdown
> **加固备注**（2026-09-02）：P1-P4 评审修复已落地（源优先级/rollup/months 贯通/引擎测试/读路径卫生），详见 `docs/Changelog.md` 2026-09-02 条目。
```

- [ ] **Step 4: 提交**

```bash
git add docs/Changelog.md docs/references/best-practices.md plans/industry-research-workbench.md
git commit -m "docs: 行业投研工作台评审加固 Changelog 与经验沉淀"
```
