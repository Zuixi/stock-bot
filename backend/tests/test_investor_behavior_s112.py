"""spec §S1.12 情绪 · 投资者行为 —— 纯函数与登记面不变量（不触网、不连库）.

锁住四件事：

**A. 登记面（spec 判据 1 与判据 3 的机械化）**
1. 两个落库 key 在 `INDUSTRIES['market']` 里登记，且 `freq` 有 UI 标签、
   `description` 同时写明「频率」与「滞后」（判据 1：频率与滞后必须能在 UI 明示）。
2. **开户数不在登记面里**（判据 3：实测不可得 ⇒ 本项在 UI 不存在，连占位空壳 key 都不许有），
   且"为什么删掉"有文字记录（删除必须留痕）。
3. 「ETF 份额变化复用 S1.7」机械可证（`etf_share_change` 确实在 §S1.7 的登记面里）。
4. 服务的 `freq`/`unit`/`source_tier`/`source` 与 registry 逐字一致（否则 UI 明示的频率是谎言）。

**B. 上游形状守卫**（Ruling 51 同族：空结果/半张表/换单位必须**响亮失败**）
5. 新成立基金：0 行、缺列、行数 < 下限（静默截断）、`募集份额` 越界 → 全部报错。
6. 公募基金仓位：0 行、列集合变化 → 报错。

**C. 聚合的缺数纪律（绝不静默为 0）**
7. 该月有任一基金份额为空 → 整月 `None` + `source_empty`（**不**拿部分和冒充全月）。
8. 该月上游一行都没有 → **不产出该月**（"不存在"与"缺数"是两回事）。

**D. PIT：不得静默回退 `period`**
9. `new_fund_share` 的 `published_at.日期` = 该月**最后一个成立日** + 4 天（不是月首）；
10. `public_fund_equity_position` 的 `published_at.日期` = 观测日 + 15 个**工作日**（跳过周末）。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services import investor_behavior_service as svc
from app.services.industry_registry import (
    CAPITAL_FLOWS_ALL_KEYS,
    FREQ_LABELS,
    INDUSTRIES,
    INVESTOR_BEHAVIOR_ALL_KEYS,
    INVESTOR_BEHAVIOR_DROPPED_ITEMS,
    INVESTOR_BEHAVIOR_METRIC_KEYS,
    INVESTOR_BEHAVIOR_REUSED_KEYS,
)

_CN = ZoneInfo("Asia/Shanghai")


def _market_defs() -> dict[str, object]:
    return {m.key: m for m in INDUSTRIES["market"].metrics}


# ── A. 登记面 ──────────────────────────────────────────────────────────


class TestRegistrySurface:
    def test_both_metrics_registered_in_market_scope(self):
        defs = _market_defs()
        for key in INVESTOR_BEHAVIOR_ALL_KEYS:
            assert key in defs, f"{key} 未登记在 scope='market'"

    def test_freq_has_ui_label_and_description_states_freq_and_lag(self):
        """判据 1：频率与滞后必须能在 UI 明示（机器读 `freq` + 人读描述）。"""
        defs = _market_defs()
        for key in INVESTOR_BEHAVIOR_ALL_KEYS:
            metric = defs[key]
            assert metric.freq in FREQ_LABELS, f"{key}.freq={metric.freq} 前端无徽章标签"
            for token in ("频率", "滞后"):
                assert token in metric.description, f"{key} 的描述里没有「{token}」"

    def test_freq_values_are_the_declared_ones(self):
        """spec 判据 1 的具体取值（改了必须同步报告与字典）。"""
        assert svc.FREQS["new_fund_share"] == "monthly"
        assert svc.FREQS["public_fund_equity_position"] == "quarterly"

    def test_account_openings_metric_is_absent(self):
        """判据 3：开户数不可得 ⇒ 在 UI（registry）**不存在**，连空壳 key 都不许有。"""
        registered = set(INVESTOR_BEHAVIOR_ALL_KEYS)
        for forbidden in (
            "new_investor_accounts",
            "retail_investor_accounts",
            "investor_account_openings",
            "stock_account_statistics",
        ):
            assert forbidden not in registered
            assert forbidden not in _market_defs(), f"{forbidden} 出现在 market scope 登记面"

    def test_dropped_item_is_documented(self):
        """ "删除"必须留下为什么（否则后人只会看到一个不存在的功能）。"""
        assert INVESTOR_BEHAVIOR_DROPPED_ITEMS, "开户数被判不可得却没有文字记录"
        note = " ".join(INVESTOR_BEHAVIOR_DROPPED_ITEMS.values())
        assert "不可得" in note
        assert "2023-08" in note, "结论必须带实机证据（最新月停在 2023-08）"

    def test_etf_share_change_reuse_is_mechanically_provable(self):
        """spec：「基金申购赎回 → ETF 份额变化（复用 S1.7）」—— 复用必须可证。"""
        assert INVESTOR_BEHAVIOR_REUSED_KEYS == ("etf_share_change",)
        assert "etf_share_change" in CAPITAL_FLOWS_ALL_KEYS
        # 而且**不得**被本批次重复登记（重复登记会让白名单出现两份会漂移的定义）。
        assert set(INVESTOR_BEHAVIOR_METRIC_KEYS) & set(INVESTOR_BEHAVIOR_REUSED_KEYS) == set()

    def test_service_row_contract_matches_registry(self):
        """服务落库的四元组 = registry（导入期已有断言，这里锁住"断言真的跑过"）。"""
        defs = _market_defs()
        for key in svc.ALL_METRIC_KEYS:
            metric = defs[key]
            assert svc.UNITS[key] == metric.unit
            assert svc._TIERS[key] == metric.tier
            assert svc._SOURCES[key] in metric.sources

    def test_position_metric_is_not_official_tier(self):
        """硬约束 1：第三方编制、口径不可复算 ⇒ **不得**标成 official。"""
        assert _market_defs()["public_fund_equity_position"].tier == "calc"
        assert svc._TIERS["public_fund_equity_position"] == "calc"

    def test_position_description_says_background_only(self):
        """硬约束 1：文案必须体现"仅背景展示 / 不进信号链路"。"""
        desc = _market_defs()["public_fund_equity_position"].description
        assert "仅作背景展示" in desc
        assert "信号链路" in desc


# ── B. 上游形状守卫 ────────────────────────────────────────────────────


def _new_found_frame(n: int = svc.NEW_FUND_MIN_ROWS) -> pd.DataFrame:
    """合成一张**刚好过行数下限**的新成立基金表（列名与上游逐字一致）。

    默认 `n` = 下限本身 ⇒ 形状守卫放行；要测"越界值"这类**行内**守卫时必须显式传
    `n=svc.NEW_FUND_MIN_ROWS`（用 3 行的小表会被"疑似静默截断"守卫先拦下，那不是本用例
    要测的断言）。
    """
    rows = [
        {
            "基金代码": f"{i:06d}",
            "基金简称": f"测试基金{i}",
            "发行公司": "测试基金公司",
            "基金类型": "混合型-偏股",
            "集中认购期": "26/01/01～26/01/05",
            "募集份额": 1.0,
            "成立日期": date(2026, 1, 5),
            "成立来涨幅": 0.0,
            "基金经理": "张三",
            "申购状态": "开放申购",
            "优惠费率": 0.15,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


class TestNewFundGuards:
    def test_empty_frame_fails_loudly(self):
        with pytest.raises(svc.InvestorBehaviorSourceError, match="0 行"):
            svc.normalize_new_fund_rows(pd.DataFrame())

    def test_missing_column_fails_loudly(self):
        frame = _new_found_frame(3).drop(columns=["募集份额"])
        with pytest.raises(svc.InvestorBehaviorSourceError, match="缺列"):
            svc.normalize_new_fund_rows(frame)

    def test_truncated_frame_fails_loudly(self):
        """静默截断（只回 2000 行）必须响亮失败 —— 本会话连续踩到的陷阱族。"""
        with pytest.raises(svc.InvestorBehaviorSourceError, match="静默截断"):
            svc.normalize_new_fund_rows(_new_found_frame(svc.NEW_FUND_MIN_ROWS - 1))

    def test_out_of_range_share_fails_loudly(self):
        """单位从亿元换成万元/份 ⇒ 越界 ⇒ 响亮失败（不照落）。"""
        frame = _new_found_frame()
        frame.loc[0, "募集份额"] = 99999.0
        with pytest.raises(svc.InvestorBehaviorSourceError, match="越界"):
            svc.normalize_new_fund_rows(frame)

    def test_nan_share_is_kept_as_none_not_dropped(self):
        """空份额的行必须**保留**（只记 None）：丢掉它会造出一个偏低的全月合计。"""
        frame = _new_found_frame()
        frame.loc[1, "募集份额"] = float("nan")
        rows = svc.normalize_new_fund_rows(frame)
        assert len(rows) == len(frame)
        assert sum(1 for r in rows if r["share_yi"] is None) == 1


class TestPositionGuards:
    def test_empty_frame_fails_loudly(self):
        with pytest.raises(svc.InvestorBehaviorSourceError, match="0 行"):
            svc.normalize_position_rows(pd.DataFrame())

    def test_column_change_fails_loudly(self):
        """上游若补了 report_period/publish_date，PIT 结论必须重新裁决 ⇒ 响亮失败。"""
        frame = pd.DataFrame(
            {
                "date": [date(2026, 1, 5)],
                "close": [4000.0],
                "position": [90.0],
                "report_period": ["2025Q4"],
            }
        )
        with pytest.raises(svc.InvestorBehaviorSourceError, match="列集合变了"):
            svc.normalize_position_rows(frame)

    def test_out_of_range_position_fails_loudly(self):
        frame = pd.DataFrame({"date": [date(2026, 1, 5)], "close": [4000.0], "position": [420.0]})
        with pytest.raises(svc.InvestorBehaviorSourceError, match="越界"):
            svc.normalize_position_rows(frame)


# ── C. 聚合的缺数纪律 ──────────────────────────────────────────────────


def _rows(*triples: tuple[str, date, float | None]) -> list[dict[str, object]]:
    return [
        {"fund_code": code, "established": day, "share_yi": share} for code, day, share in triples
    ]


class TestNewFundAggregation:
    def test_month_sum_and_anchor_is_last_established(self):
        out = svc.new_fund_share_by_month(
            _rows(
                ("000001", date(2026, 3, 3), 10.0),
                ("000002", date(2026, 3, 27), 2.5),
            )
        )
        assert list(out) == [date(2026, 3, 1)]
        metric = out[date(2026, 3, 1)]
        assert metric.value == pytest.approx(12.5)
        assert metric.missing_reason is None
        assert metric.extra["n_funds"] == 2
        assert metric.extra["n_missing_share"] == 0
        # 锚 = **最后一个成立日**（不是月首/月尾）：上游按成立日收录。
        assert metric.extra["anchor_established"] == "2026-03-27"

    def test_partial_field_nulls_yield_an_explicit_lower_bound(self):
        """**字段级**缺值 ≠ 缺数 ≠ 0：照落有值成员之和，但必须显式登记"这是下界"。

        上游 `募集份额` 的空缺实测是**字段级**（~4.8%、均匀散布在各类型/各月），
        故整月判 NULL 会把 45 个月里的 42 个抹掉（实测）；而把缺的当 0 又会把下界
        冒充成精确值。故取中间档：值 = 有值成员之和，`is_lower_bound=True`。
        """
        out = svc.new_fund_share_by_month(
            _rows(
                ("000001", date(2026, 3, 3), 10.0),
                ("000002", date(2026, 3, 27), None),
            )
        )
        metric = out[date(2026, 3, 1)]
        assert metric.value == pytest.approx(10.0)
        assert metric.missing_reason is None
        assert metric.extra["n_funds"] == 1
        assert metric.extra["n_missing_share"] == 1
        assert metric.extra["is_lower_bound"] is True
        assert "下界" in metric.extra["missing_note"]

    def test_whole_month_without_any_share_is_missing_not_zero(self):
        """**整月**全空才是缺数 ⇒ `None` + `source_empty`（**绝不**落 0）。"""
        out = svc.new_fund_share_by_month(
            _rows(
                ("000001", date(2026, 3, 3), None),
                ("000002", date(2026, 3, 27), None),
            )
        )
        metric = out[date(2026, 3, 1)]
        assert metric.value is None
        assert metric.missing_reason == svc.MISSING_SOURCE_EMPTY
        assert metric.extra["n_funds"] == 0
        assert metric.extra["n_missing_share"] == 2

    def test_month_without_rows_is_absent_not_zero(self):
        """上游该月一行都没有 ⇒ 不产出该月（"不存在" ≠ "缺数" ≠ 0）。"""
        out = svc.new_fund_share_by_month(_rows(("000001", date(2026, 3, 3), 10.0)))
        assert date(2026, 4, 1) not in out
        assert all(m.value != 0 for m in out.values())

    def test_month_window_filter(self):
        rows = _rows(
            ("000001", date(2026, 1, 3), 1.0),
            ("000002", date(2026, 3, 3), 2.0),
        )
        out = svc.new_fund_share_by_month(rows, month_lo=date(2026, 3, 1))
        assert list(out) == [date(2026, 3, 1)]

    def test_month_start_is_the_period_anchor(self):
        assert svc.month_start(date(2026, 3, 27)) == date(2026, 3, 1)
        assert svc.month_start(date(2026, 1, 1)) == date(2026, 1, 1)


class TestPositionAggregation:
    def test_values_by_observation_day(self):
        rows = [
            {"day": date(2026, 1, 2), "position_pct": 90.5},
            {"day": date(2026, 1, 9), "position_pct": None},
        ]
        out = svc.public_fund_position_by_day(rows)
        assert out[date(2026, 1, 2)].value == pytest.approx(90.5)
        assert out[date(2026, 1, 9)].value is None
        assert out[date(2026, 1, 9)].missing_reason == svc.MISSING_SOURCE_EMPTY
        assert out[date(2026, 1, 2)].extra["signal_chain"] == "forbidden"

    def test_window_filter(self):
        rows = [{"day": date(2026, 1, 2), "position_pct": 90.5}]
        assert svc.public_fund_position_by_day(rows, day_lo=date(2026, 2, 1)) == {}


# ── D. PIT：不得静默回退 period ────────────────────────────────────────


class TestPublishedAt:
    def test_new_fund_published_is_anchor_plus_lag(self):
        metric = svc.new_fund_share_by_month(
            _rows(
                ("000001", date(2026, 3, 3), 10.0),
                ("000002", date(2026, 3, 27), 2.5),
            )
        )[date(2026, 3, 1)]
        row = svc._row(date(2026, 3, 1), metric)
        published = row["published_at"]
        assert isinstance(published, datetime)
        assert published.tzinfo is not None, "published_at 必须带时区（Ruling 35）"
        assert published.astimezone(_CN).date() == date(2026, 3, 31)  # 03-27 + 4 天
        # **不得**回退 period（月首 03-01）——回退就是静默谎报可得时点。
        assert published.astimezone(_CN).date() != date(2026, 3, 1)

    def test_position_published_is_observation_plus_15_working_days(self):
        metric = svc.public_fund_position_by_day([{"day": date(2026, 1, 2), "position_pct": 90.5}])[
            date(2026, 1, 2)
        ]
        row = svc._row(date(2026, 1, 2), metric)
        published = row["published_at"].astimezone(_CN)
        # 2026-01-02（周五）+ 15 个工作日，跳过周末 = 2026-01-23（周五）
        assert published.date() == date(2026, 1, 23)
        assert svc.POSITION_PUBLISH_LAG_DAYS == 15
        assert published.date() != date(2026, 1, 2), "不得回退 period"

    def test_every_row_carries_caliber_version_and_published_at(self):
        metric = svc.public_fund_position_by_day([{"day": date(2026, 1, 2), "position_pct": 90.5}])[
            date(2026, 1, 2)
        ]
        row = svc._row(date(2026, 1, 2), metric)
        assert row["caliber_version"] == "s1.12-v1"
        assert row["published_at"] is not None
        assert row["freq"] == "quarterly"
        assert row["source"] == svc._SOURCES["public_fund_equity_position"]
        assert row["extra"]["missing_reason"] if row["value"] is None else True

    def test_missing_row_still_carries_reason(self):
        metric = svc.new_fund_share_by_month(_rows(("000001", date(2026, 3, 3), None)))[
            date(2026, 3, 1)
        ]
        row = svc._row(date(2026, 3, 1), metric)
        assert row["value"] is None
        assert row["extra"]["missing_reason"] == svc.MISSING_SOURCE_EMPTY
        assert row["published_at"] is not None
        assert row["caliber_version"] is not None


# ── E. spec 判据 2：公募基金仓位不得出现在任何 signal_rules 中 ──────────


class TestCriterion2NotInSignalChain:
    """spec §S1.12 判据 2（行为）：**公募基金仓位不出现在任何 `signal_rules` 中**（grep 可验）。

    本任务落地时 `backend/app/services/signal_rules.py` **尚不存在**（S2.2 未做），
    故当前是 **0 命中 —— 因为文件不存在**。这条断言的作用是**把它变成机械可检**：
    S2.2 一旦创建该文件并引用本 key（或「基金仓位」这类中文别名），本用例立即红，
    而不是等到有人想起来跑那条 grep。
    """

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    #: spec 原文的对象文件（S2.2 的交付物）。
    _SIGNAL_RULES = _REPO_ROOT / "backend" / "app" / "services" / "signal_rules.py"
    #: 不得出现在**信号链**里的字面量（英文字面 + 中文别名）。
    _FORBIDDEN = ("public_fund_equity_position", "fund_stock_position", "基金仓位", "公募基金仓位")
    #: 允许出现这些字面量的**非信号链**文件（登记面 / 本模块自身 / 文档）。
    _ALLOWED = {
        "backend/app/services/industry_registry.py",
        "backend/app/services/investor_behavior_service.py",
        "backend/app/core/providers/akshare_client.py",
    }

    def test_signal_rules_leaves_this_key_at_zero(self):
        if not self._SIGNAL_RULES.exists():
            pytest.skip(
                "backend/app/services/signal_rules.py 尚不存在（S2.2 未做）—— "
                "当前 0 命中是因为没有文件；S2.2 落地后本用例会自动开始把关"
            )
        text = self._SIGNAL_RULES.read_text(encoding="utf-8")
        hits = [token for token in self._FORBIDDEN if token in text]
        assert hits == [], (
            f"spec §S1.12 判据 2：公募基金仓位不得进信号链路，但 signal_rules.py 里出现 {hits}"
        )

    def test_no_rule_or_signal_module_consumes_the_background_key(self):
        """更宽的兜底：`app/services/` 下任何**规则/信号**模块都不得出现该 key。"""
        services = self._REPO_ROOT / "backend" / "app" / "services"
        offenders: list[str] = []
        for path in sorted(services.glob("*.py")):
            rel = path.relative_to(self._REPO_ROOT).as_posix()
            if rel in self._ALLOWED:
                continue
            if not any(marker in path.name for marker in ("rule", "signal", "strategy")):
                continue
            text = path.read_text(encoding="utf-8")
            hits = [token for token in self._FORBIDDEN if token in text]
            if hits:
                offenders.append(f"{rel}: {hits}")
        assert offenders == [], (
            "spec §S1.12 判据 2：以下规则/信号模块消费了背景指标 —— " + "; ".join(offenders)
        )
