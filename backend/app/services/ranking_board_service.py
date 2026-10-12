"""A股 7 榜的榜型抽象与查询（S1.6）。

**为什么要新抽象，而不是往 ``market_service._RANKING_ORDER`` 里加 key**
--------------------------------------------------------------------------
``_RANKING_ORDER: dict[str, tuple[str, str]]`` = ``{榜名: (daily_quotes 的列, 方向)}``，
形状只能表达「一张表的单列排序」。spec §S1.6 要的 7 个榜里：

* 市值 / 股息率 来自 ``daily_basic_indicators``（另一张表）；
* 半年利润 / 年利润来自 ``income_statement_facts``、ROE 来自 ``financial_metrics``
  ——**还要带 PIT 报告期选择**（同一只股票在不同交易日该用哪一期）；
* 月度涨幅 / 跌幅是**窗口聚合**（需要一条参考日行情），不是单日 ``pct_chg``。

三类榜的 SQL 形状、参数、口径披露项都不同 → 用 :class:`RankingBoardSpec` 抽象三个判别式
（``basic_sort`` / ``momentum`` / ``financial_statement``），每类一条 SQL 模板。
**既有 5 个单日榜一个字节都没动**（Ruling 30：不得删工作能力）。

PIT（本任务最关键的一条）
--------------------------
财务类榜选"该交易日**已公告**的最新一期"，可见性判据是
``coalesce(f_ann_date, ann_date) <= 口径交易日``：

* ``f_ann_date``（实际公告日）优先——它才是「市场真的能在这一天看到」的日期；
* ``ann_date`` 只是**缺失时的回退**，并通过 ``ann_date_source`` 判别式**显式告知**
  （不是静默回退，见 ADR 0011 与 S1.2 Ruling 34）；
* 取最新一期时 ``end_date`` 与 ``coalesce(...)`` **同时**参与 ``DISTINCT ON`` 排序：
  ``(stock_id, end_date DESC, ann_date DESC)``——一只股票若同时有两期"同一 end_date
  但公告日不同"的版本（更正披露），取更晚可见的那条，不会用一份还没发布的更正数。

**没有已公告期的股票不进榜**（``eligible_rows`` 不含它），且它的财务字段是 ``None``
而**不是 0**——0 会被排进"利润最小"的名次里，那是错的。剔除的理由写进 ``notes``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.schemas.ranking_board import (
    FinancialReportRefOut,
    RankingBoardCategory,
    RankingBoardItemOut,
    RankingBoardKey,
    RankingBoardResponseOut,
    RankingBoardSource,
    RankingCaliberOut,
)
from app.services import market_day_service
from app.services.market_data_service import _today_sh
from app.services.market_service import _MARKET_CACHE_TTL, last_weekday

logger = logging.getLogger(__name__)

#: 「月度」的窗口长度（**交易日**，不含口径日当天）。与项目既有的 20 交易日窗口
#: 口径一致（``industry_registry`` 的 ``close_above_ma20`` 与
#: ``market_sentiment_service`` 的 ``win = 20``）。改这个数 = 改口径 → 必须同时改
#: 下面 :data:`MOMENTUM_CALIBER_TEMPLATE` 的文案与 `caliber_version` 讨论，
#: 否则 UI 上写的窗口与实际算的不一致。
MOMENTUM_WINDOW_TRADING_DAYS = 20

#: 参考日的日历回看上限（日历日）：20 个交易日 ≈ 28 个日历日（含长假），40 天留足余量。
#: **不向前取更早的日期**——窗口内没有行情（次新股/长期停牌）就整只剔除，
#: 与 ``market_sentiment_service`` 的"窗口不足整只剔除，不进分子也不进分母"同构。
MOMENTUM_LOOKBACK_CALENDAR_DAYS = 40

#: 次新股阈值（日历日）。spec 只要求"是否含次新股"必须写明，故这里给的是一个
#: **可披露的机械定义**（不是业务判断）：上市不足 365 天即标记 ``is_new_stock``。
NEW_STOCK_CALENDAR_DAYS = 365

#: ST 判据：证券简称含 ``ST``（含 ``*ST``）。``stocks`` 表没有 ST 标志列，
#: 名称是唯一可得信号；它是**披露口径**的计数，不参与任何涨跌停/限幅计算
#: （后者必须走 ``limit_up`` 的权威 ``up_limit``，见 market_data.py 的裁定）。
_ST_NAME_PATTERN = "ST"

MOMENTUM_CALIBER_TEMPLATE = (
    "股价月度{label}口径：{close_ref} 的复权收盘 ÷ 参考日复权收盘 − 1，"
    "其中参考日 = 口径交易日往前第 {window} 个交易日"
    "（日历回看上限 {lookback} 天内最近的一个交易日）；"
    "复权 = close × coalesce(adj_factor, 1)（前复权比值，免取基准日）。"
    "窗口内取不到参考日行情的标的（次新股 / 长期停牌）整只剔除，不进分子也不进分母。"
)

_SAMPLE_SCOPE_FULL = (
    "全市场：凡在该交易日有可用指标值的标的都参与排序，**含 ST、含次新股**"
    "（不做任何样本剔除；次新股/ST 逐行以 is_new_stock / is_st 标记，供前端筛选）。"
)
_SAMPLE_SCOPE_DIVIDEND = (
    "全市场：口径日 dv_ratio（股息率）**> 0** 的标的参与排序，**含 ST、含次新股**。"
    "dv_ratio 为 NULL 或 0 的标的**不进榜**（0 不是「股息率很低」，是「该指标无值」）；"
    "逐行标记 is_new_stock / is_st。"
)
_SAMPLE_SCOPE_MOMENTUM = (
    "全市场：在口径交易日与参考日**都有**复权收盘价的标的参与排序，**含 ST、含次新股**"
    "（窗口不足的整只剔除）；逐行标记 is_new_stock / is_st。"
)
_SAMPLE_SCOPE_FINANCIAL = (
    "全市场：在口径交易日**已有已公告财报**的标的参与排序，**含 ST、含次新股**。"
    "该交易日尚无任何已公告期的标的**不进榜**（不是排最后、更不是按 0 值排）；"
    "逐行标记 is_new_stock / is_st。"
)

_NO_FINANCIAL_CALIBER = "不适用（该榜不使用财务数据，只排序行情/估值指标）"


def _financial_period_template(metric_label: str, report_hint: str) -> str:
    return (
        f"{metric_label}取「口径交易日**已公告**的最新一期」财报，不是「库里最新一期」："
        "可见性判据 ann_date = coalesce(f_ann_date, ann_date) ≤ 口径交易日"
        "（公告日 ≤ 该交易日的报表才算当时可得），其中 f_ann_date = 实际公告日优先、"
        "缺失才回退 ann_date，"
        "逐行以 ann_date_source 标注。取期末排序 (end_date DESC, ann_date DESC)，"
        f"逐行给出实际取到的报告期与公告日。口径提示：不限报告期类型（{report_hint}）。"
    )


# ---------------------------------------------------------------------------
# 榜型抽象
# ---------------------------------------------------------------------------

BasicSortParams = Literal["total_mv", "dv_ratio"]
FinancialColumn = Literal["n_income_attr_p", "roe"]
FinancialMetricKey = Literal["roe"]

#: 财务指标列 → 落库表。**实测结论**（不是推断）：``financial_ingest.py`` 把
#: ``income`` 的 ``n_income`` / ``n_income_attr_p`` 写进 ``income_statement_facts``，
#: 把 ``fina_indicator.roe`` 作为 provider 指标写进 ``financial_metrics``
#: （``metric_key = 'roe'``）。两者都通过 ``report_version_id`` 关联到
#: ``financial_report_versions``（PIT 判据的 ``f_ann_date`` / ``ann_date`` 在这里）。
#: 表名只来自这张常量表（与 ``_RANKING_ORDER`` 同一"白名单内插"纪律），不来自请求。
FINANCIAL_COLUMN_SOURCE: dict[
    FinancialColumn, Literal["income_statement_facts", "financial_metrics"]
] = {
    "n_income_attr_p": "income_statement_facts",
    "roe": "financial_metrics",
}


@dataclass(frozen=True)
class RankingBoardSpec:
    """一个榜的**全部**静态信息：来源、排序、口径披露。

    ``source_kind`` 是判别式（不是布尔开关堆叠）：三类榜的 SQL 参数、排序方向语义、
    "用的是哪期财务数据"是否有意义，都由它决定。
    """

    key: RankingBoardKey
    label: str
    category: RankingBoardCategory
    source_kind: RankingBoardSource
    source_table: str
    sort_direction: Literal["DESC", "ASC"]
    metric_unit: str
    metric_key: str
    sample_scope: str
    # 判别式对应的类型化参数（用不到则为 None）
    basic_column: BasicSortParams | None = None
    financial_column: FinancialColumn | None = None
    financial_metric_key: FinancialMetricKey | None = None
    # notes 里额外要披露的榜级说明（例如"0 值不进榜"）
    extra_notes: tuple[str, ...] = ()


def _basic_sql(column: BasicSortParams, direction: str) -> TextClause:
    """市值 / 股息率：``daily_basic_indicators`` 单日单列排序。

    ``:extra_rows`` = limit + 1：多取一行用于判定「截断边界是否并列」，
    让"榜尾为什么是这几只"可核对，而不是静默按 stock_id 切。
    """
    return text(f"""
        SELECT s.symbol, s.name, s.exchange, b.close, b.{column} AS metric,
               b.total_mv, b.dv_ratio, s.list_date
        FROM daily_basic_indicators b
        JOIN stocks s ON s.id = b.stock_id
        WHERE b.trade_date = :as_of AND b.{column} IS NOT NULL
        ORDER BY b.{column} {direction}, b.stock_id ASC
        LIMIT :extra_rows
    """)


def _dividend_sql() -> TextClause:
    """股息率榜：``dv_ratio > 0``。

    ``IS NOT NULL`` 不够：实测该列 3759 行全部 > 0、NULL 才是「无值」，但把 0 当
    "股息率很低"排进榜尾会造出假名次，所以 0 与 NULL 一并剔除并在口径里写明。
    """
    return text("""
        SELECT s.symbol, s.name, s.exchange, b.close, b.dv_ratio AS metric,
               b.total_mv, b.dv_ratio, s.list_date
        FROM daily_basic_indicators b
        JOIN stocks s ON s.id = b.stock_id
        WHERE b.trade_date = :as_of AND b.dv_ratio IS NOT NULL AND b.dv_ratio > 0
        ORDER BY b.dv_ratio DESC, b.stock_id ASC
        LIMIT :extra_rows
    """)


def _momentum_sql(direction: str) -> TextClause:
    """月度涨幅/跌幅：20 交易日窗口的复权收益率。

    结构：先定**参考日**（口径日往前第 20 个交易日；找不到则为 NULL），再取该日的
    复权收盘，最后按收益率排序。参考日为空 → ``JOIN`` 落空 → 该股整只剔除。

    ``:offset`` 供"多取一行判并列"用（momentum 模板带 LIMIT，不易再参数化 extra_rows，
    故与 financial 模板统一走 LIMIT/OFFSET 双参数）。
    """
    return text(f"""
        WITH ref_day AS (
            SELECT max(trade_date) AS day
            FROM daily_quotes
            WHERE trade_date < :as_of
              AND trade_date >= CAST(:as_of AS date) - CAST(:lookback_days AS integer)
              AND trade_date <= CAST(:as_of AS date) - make_interval(days => :window_days)
        )
        SELECT s.symbol, s.name, s.exchange, cur.close, cur.pct_chg,
               (cur.close * coalesce(cur.adj_factor, 1)
                / (ref.close * coalesce(ref.adj_factor, 1)) - 1) * 100 AS metric,
               b.total_mv, b.dv_ratio, s.list_date
        FROM daily_quotes cur
        JOIN stocks s ON s.id = cur.stock_id
        JOIN ref_day ON ref_day.day IS NOT NULL
        JOIN daily_quotes ref ON ref.stock_id = cur.stock_id AND ref.trade_date = ref_day.day
        LEFT JOIN daily_basic_indicators b
               ON b.stock_id = cur.stock_id AND b.trade_date = :as_of
        WHERE cur.trade_date = :as_of
          AND cur.close IS NOT NULL AND cur.close > 0
          AND ref.close IS NOT NULL AND ref.close > 0
        ORDER BY metric {direction}, cur.stock_id ASC
        LIMIT :limit OFFSET :offset
    """)


#: 财务类榜的"选期 CTE"（一次写、两处用：取 top-N 与算分母/报告期分布）。
#: 两处**必须逐字同源**——否则 ``eligible_rows`` 的分母与实际排序用的是两套口径，
#: "为什么不足 100 条"就会说错（这是本文件最容易出现假绿的地方，故用同一个生成器）。
_FINANCIAL_PIT_CTE = """
        WITH ann AS (
            SELECT v.id,
                   v.stock_id,
                   v.end_date,
                   v.report_type,
                   coalesce(v.f_ann_date, v.ann_date) AS ann_date,
                   CASE WHEN v.f_ann_date IS NOT NULL THEN 'f_ann_date' ELSE 'ann_date' END
                       AS ann_date_source
            FROM financial_report_versions v
            WHERE v.comp_type = '1'
              AND coalesce(v.f_ann_date, v.ann_date) IS NOT NULL
              AND coalesce(v.f_ann_date, v.ann_date) <= CAST(:as_of AS date)
        ),
        have AS (
            SELECT DISTINCT ON (fm.stock_id)
                   fm.stock_id, fm.report_version_id, a.end_date, a.report_type,
                   a.ann_date, a.ann_date_source
            FROM financial_metrics fm
            JOIN ann a ON a.id = fm.report_version_id
            WHERE fm.metric_key = :metric_key
            ORDER BY fm.stock_id, a.end_date DESC, a.ann_date DESC
        )
"""


def _momentum_count_sql() -> TextClause:
    """月度涨跌榜的**全市场分母**：两个交易日都有正复权收盘价的标的数。

    条件与 :func:`_momentum_sql` 的 WHERE 逐字一致（``close IS NOT NULL AND > 0``）；
    参考日为空（窗口内无行情）→ ``count`` 自然为 0，与排序查询返回 0 行自洽。
    """
    return text("""
        WITH ref_day AS (
            SELECT max(trade_date) AS day
            FROM daily_quotes
            WHERE trade_date < :as_of
              AND trade_date >= CAST(:as_of AS date) - CAST(:lookback_days AS integer)
              AND trade_date <= CAST(:as_of AS date) - make_interval(days => :window_days)
        )
        SELECT count(*) AS stock_count
        FROM daily_quotes cur
        JOIN ref_day ON ref_day.day IS NOT NULL
        JOIN daily_quotes ref ON ref.stock_id = cur.stock_id AND ref.trade_date = ref_day.day
        WHERE cur.trade_date = :as_of
          AND cur.close IS NOT NULL AND cur.close > 0
          AND ref.close IS NOT NULL AND ref.close > 0
    """)


def _financial_value_sql(column: FinancialColumn) -> str:
    """指标值的 SQL 取法。

    ``financial_metrics`` 是**长表**（``metric_key`` + ``value``），没有 ``roe`` 列——
    取 ``im.value``；``income_statement_facts`` 是宽表，取同名列。
    """
    if FINANCIAL_COLUMN_SOURCE[column] == "income_statement_facts":
        return f"im.{column}"
    return "im.value"


def _financial_join(column: FinancialColumn) -> str:
    """指标列的取数 JOIN。表名来自 :data:`FINANCIAL_COLUMN_SOURCE` 白名单。

    实测落库位置：``n_income_attr_p`` 在 ``income_statement_facts``；
    ``roe`` 在 ``financial_metrics``（``financial_ingest.py`` 的 provider 列表）。
    """
    if FINANCIAL_COLUMN_SOURCE[column] == "income_statement_facts":
        return "JOIN income_statement_facts im ON im.report_version_id = h.report_version_id"
    return (
        "JOIN financial_metrics im ON im.report_version_id = h.report_version_id"
        " AND im.metric_key = :metric_key"
    )


def _financial_top_sql(column: FinancialColumn, direction: str) -> TextClause:
    """半年利润 / 年利润 / ROE：PIT 选取已公告最新一期，再按该期指标排序。

    **两步是必须的**：``have`` 只负责「选期」（``metric_key`` 在此是**存在性**判据），
    随后按 ``report_version_id`` 回表取实际值。若把 ``metric_key = 'n_income_attr_p'``
    写进 ``have`` 又同时取 ``roe`` 列（或反过来），关联列会被筛成 NULL → 整榜全空。

    ``DISTINCT ON`` 排序键 = ``(stock_id, end_date DESC, ann_date DESC)``：期末新者优先；
    同一期末有多个可见版本（更正披露）时取更晚可见的那条。
    """
    value = _financial_value_sql(column)
    return text(f"""
        {_FINANCIAL_PIT_CTE}
        SELECT s.symbol, s.name, s.exchange, q.close, q.pct_chg,
               {value} AS metric,
               b.total_mv, b.dv_ratio, s.list_date,
               h.end_date, h.report_type, h.ann_date, h.ann_date_source
        FROM have h
        {_financial_join(column)}
        JOIN stocks s ON s.id = h.stock_id
        LEFT JOIN daily_quotes q ON q.stock_id = h.stock_id AND q.trade_date = :as_of
        LEFT JOIN daily_basic_indicators b
               ON b.stock_id = h.stock_id AND b.trade_date = :as_of
        WHERE {value} IS NOT NULL
        ORDER BY {value} {direction}, h.stock_id ASC
        LIMIT :limit OFFSET :offset
    """)


def _financial_stats_sql(column: FinancialColumn) -> TextClause:
    """与 :func:`_financial_top_sql` **同源**的分母 / 报告期分布聚合。

    分组后 ``sum(stock_count)`` = ``eligible_rows``（排序前的全市场分母），
    同时给出「实际取到的报告期分布」——这两件事是 UI 回答
    「不足 100 条时为什么」和「用的是哪期财务数据」的唯一依据。
    """
    source = FINANCIAL_COLUMN_SOURCE[column]
    later = (
        "SELECT h.end_date, h.report_type, count(DISTINCT h.stock_id) AS stock_count"
        if source == "financial_metrics"
        else "SELECT h.end_date, h.report_type, count(DISTINCT s.id) AS stock_count"
    )
    extra_join = "" if source == "financial_metrics" else "JOIN stocks s ON s.id = h.stock_id"
    return text(f"""
        {_FINANCIAL_PIT_CTE}
        {later}
        FROM have h
        {_financial_join(column)}
        {extra_join}
        WHERE {_financial_value_sql(column)} IS NOT NULL
        GROUP BY h.end_date, h.report_type
        ORDER BY h.end_date DESC, h.report_type
    """)


def _all_have_count_sql() -> TextClause:
    """财务类榜的「全市场分母」：该交易日有**任何**已公告期的标的总数。

    与 ``eligible_rows``（该榜指标可取值的标的数）分开：这样才能说清"不足 100 条
    是因为没财报，还是因为该指标无值"。
    """
    return text("""
        SELECT count(DISTINCT v.stock_id) AS stock_count
        FROM financial_report_versions v
        WHERE v.comp_type = '1'
          AND coalesce(v.f_ann_date, v.ann_date) IS NOT NULL
          AND coalesce(v.f_ann_date, v.ann_date) <= CAST(:as_of AS date)
    """)


def _basic_count_sql(column: BasicSortParams) -> TextClause:
    """市值/股息率榜的**全市场分母**（不是取回行数）。

    为什么必须单独查：排序查询只回 ``limit + 1`` 行，用它的长度当分母会把
    "有 5559 只可排" 说成 "只有 100 只可排"——"不足 100 条时的原因"立刻就错了。
    过滤条件必须与 :func:`_basic_sql` / :func:`_dividend_sql` **逐字一致**。
    """
    if column == "dv_ratio":
        return text("""
            SELECT count(*) AS stock_count
            FROM daily_basic_indicators b
            WHERE b.trade_date = :as_of AND b.dv_ratio IS NOT NULL AND b.dv_ratio > 0
        """)
    return text("""
        SELECT count(*) AS stock_count
        FROM daily_basic_indicators b
        WHERE b.trade_date = :as_of AND b.total_mv IS NOT NULL
    """)


BASIC_SQL: dict[BasicSortParams, TextClause] = {
    "total_mv": _basic_sql("total_mv", "DESC"),
    "dv_ratio": _dividend_sql(),
}
BASIC_COUNT_SQL: dict[BasicSortParams, TextClause] = {
    "total_mv": _basic_count_sql("total_mv"),
    "dv_ratio": _basic_count_sql("dv_ratio"),
}
MOMENTUM_SQL: dict[Literal["monthly_gain", "monthly_loss"], TextClause] = {
    "monthly_gain": _momentum_sql("DESC"),
    "monthly_loss": _momentum_sql("ASC"),
}
MOMENTUM_COUNT_SQL = _momentum_count_sql()
FINANCIAL_SQL: dict[FinancialColumn, TextClause] = {
    "n_income_attr_p": _financial_top_sql("n_income_attr_p", "DESC"),
    "roe": _financial_top_sql("roe", "DESC"),
}
#: 分母/报告期分布 SQL（与上表同源，见 _financial_stats_sql 的注释）。
FINANCIAL_STATS_SQL: dict[FinancialColumn, TextClause] = {
    "n_income_attr_p": _financial_stats_sql("n_income_attr_p"),
    "roe": _financial_stats_sql("roe"),
}


def _specs() -> dict[RankingBoardKey, RankingBoardSpec]:
    return {
        "market_cap": RankingBoardSpec(
            key="market_cap",
            label="市值",
            category="valuation",
            source_kind="daily_basic",
            source_table="daily_basic_indicators.total_mv",
            sort_direction="DESC",
            metric_unit="万元",
            metric_key="total_mv",
            sample_scope=_SAMPLE_SCOPE_FULL,
            basic_column="total_mv",
        ),
        "dividend_yield": RankingBoardSpec(
            key="dividend_yield",
            label="股息率",
            category="valuation",
            source_kind="daily_basic",
            source_table="daily_basic_indicators.dv_ratio",
            sort_direction="DESC",
            metric_unit="%",
            metric_key="dv_ratio",
            sample_scope="",
            basic_column="dv_ratio",
            extra_notes=(
                "dv_ratio = 0 的标的**不进榜**（0 表示该指标无值，不是「股息率极低」）。",
            ),
        ),
        "monthly_gain": RankingBoardSpec(
            key="monthly_gain",
            label="股价月度涨幅",
            category="price",
            source_kind="daily_quotes_window",
            source_table="daily_quotes（窗口聚合）",
            sort_direction="DESC",
            metric_unit="%",
            metric_key="monthly_pct_chg",
            sample_scope="",
        ),
        "monthly_loss": RankingBoardSpec(
            key="monthly_loss",
            label="股价月度跌幅",
            category="price",
            source_kind="daily_quotes_window",
            source_table="daily_quotes（窗口聚合）",
            sort_direction="ASC",
            metric_unit="%",
            metric_key="monthly_pct_chg",
            sample_scope="",
        ),
        "half_year_profit": RankingBoardSpec(
            key="half_year_profit",
            label="半年利润",
            category="financial",
            source_kind="financial_statement",
            source_table="income_statement_facts.n_income_attr_p",
            sort_direction="DESC",
            metric_unit="元",
            metric_key="n_income_attr_p",
            sample_scope=_SAMPLE_SCOPE_FINANCIAL,
            financial_column="n_income_attr_p",
            financial_metric_key="roe",
            extra_notes=(
                "「半年利润」排序不把报告期钉在 06-30：取该交易日已公告的最新一期归母净利润，"
                "故榜上可能同时出现中报与一季报（逐行给出报告期）。",
            ),
        ),
        "annual_profit": RankingBoardSpec(
            key="annual_profit",
            label="年利润",
            category="financial",
            source_kind="financial_statement",
            source_table="income_statement_facts.n_income_attr_p",
            sort_direction="DESC",
            metric_unit="元",
            metric_key="n_income_attr_p",
            sample_scope=_SAMPLE_SCOPE_FINANCIAL,
            financial_column="n_income_attr_p",
            financial_metric_key="roe",
            extra_notes=(
                "TuShare 的 ``income`` 只有累计口径（``report_type`` = 1 合并报表），"
                "单季值需另算；本榜按已公告最新一期的**累计**归母净利润排序。",
            ),
        ),
        "roe": RankingBoardSpec(
            key="roe",
            label="ROE",
            category="financial",
            source_kind="financial_statement",
            source_table="financial_metrics.roe",
            sort_direction="DESC",
            metric_unit="%",
            metric_key="roe",
            sample_scope=_SAMPLE_SCOPE_FINANCIAL,
            financial_column="roe",
            financial_metric_key="roe",
            extra_notes=(
                "ROE 为 TuShare ``fina_indicator.roe`` 原样值（已公告口径），非本地重算。",
            ),
        ),
    }


BOARDS: dict[RankingBoardKey, RankingBoardSpec] = _specs()

#: UI/测试枚举用的顺序（分组：估值 → 行情 → 财务）。
BOARD_ORDER: tuple[RankingBoardKey, ...] = (
    "market_cap",
    "dividend_yield",
    "monthly_gain",
    "monthly_loss",
    "half_year_profit",
    "annual_profit",
    "roe",
)


def get_board(key: str) -> RankingBoardSpec:
    """未知 key 抛 ``ValueError``（端点据此回 422，不透给 SQL）。"""
    spec = BOARDS.get(cast(RankingBoardKey, key))
    if spec is None:
        raise ValueError(f"unknown ranking board: {key}")
    return spec


# ---------------------------------------------------------------------------
# 行 → schema
# ---------------------------------------------------------------------------


def _report_type_label(report_type: str) -> str:
    return {"FY": "年报", "H1": "中报", "Q1": "一季报", "Q3": "三季报"}.get(
        report_type, report_type
    )


def _is_st(name: str | None) -> bool:
    return bool(name) and _ST_NAME_PATTERN in cast(str, name).upper()


def _is_new_stock(list_date: date | None, as_of: date) -> bool:
    if list_date is None:
        return False
    return (as_of - list_date).days < NEW_STOCK_CALENDAR_DAYS


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _metric_value(spec: RankingBoardSpec, row: dict[str, Any]) -> float | None:
    """这一行的排序指标值（截断并列判定用）。"""
    if spec.source_kind == "financial_statement":
        assert spec.financial_column is not None  # 由 _specs() 的不变量保证
        return _to_float(row.get("metric"))
    return _to_float(row.get("metric"))


def _row_to_item(
    spec: RankingBoardSpec, row: dict[str, Any], *, rank: int, as_of: date
) -> RankingBoardItemOut:
    payload: dict[str, Any] = {
        "rank": rank,
        "symbol": row["symbol"],
        "name": row["name"],
        "exchange": row.get("exchange"),
        "close": _to_float(row.get("close")),
        "pct_chg": _to_float(row.get("pct_chg")),
        "total_mv": _to_float(row.get("total_mv")),
        "dv_ratio": _to_float(row.get("dv_ratio")),
        "is_st": _is_st(row.get("name")),
        "is_new_stock": _is_new_stock(row.get("list_date"), as_of),
    }
    metric = _to_float(row.get("metric"))
    if spec.source_kind == "daily_basic":
        if spec.basic_column == "total_mv":
            payload["total_mv"] = metric
        elif spec.basic_column == "dv_ratio":
            payload["dv_ratio"] = metric
    elif spec.source_kind == "daily_quotes_window":
        payload["monthly_pct_chg"] = metric
    else:
        if spec.financial_column == "n_income_attr_p":
            payload["n_income_attr_p"] = metric
        else:
            payload["roe"] = metric
        end_date = row.get("end_date")
        if end_date is not None:
            payload["financial_report"] = FinancialReportRefOut(
                end_date=end_date,
                report_type=str(row.get("report_type")),
                ann_date=row["ann_date"],
                ann_date_source=str(row["ann_date_source"]),
            )
    return RankingBoardItemOut(**payload)


def _financial_period_text(spec: RankingBoardSpec, period_mix: list[tuple[str, str, int]]) -> str:
    """「用的是哪期财务数据」这一栏的正文（含实际取到的报告期分布）。"""
    head = _financial_period_template(spec.label, "中报/年报/季报都算「一期」")
    if not period_mix:
        return head + "本榜当前取到的报告期分布：无（该交易日没有任何标的已公告财报）。"
    mix = "；".join(f"{label}（{end}）{count} 只" for end, label, count in period_mix)
    return head + f"本榜实际取到的报告期分布：{mix}。"


def _caliber(
    spec: RankingBoardSpec,
    as_of: date,
    period_mix: list[tuple[str, str, int]],
) -> RankingCaliberOut:
    """口径三件事（交易日 / 财务期 / 样本范围）。"""
    if spec.source_kind == "financial_statement":
        return RankingCaliberOut(
            trade_date=as_of,
            financial_period=_financial_period_text(spec, period_mix),
            sample_scope=spec.sample_scope,
        )
    if spec.source_kind == "daily_quotes_window":
        direction_label = "涨幅" if spec.sort_direction == "DESC" else "跌幅"
        return RankingCaliberOut(
            trade_date=as_of,
            financial_period=MOMENTUM_CALIBER_TEMPLATE.format(
                label=direction_label,
                close_ref="口径交易日",
                window=MOMENTUM_WINDOW_TRADING_DAYS,
                lookback=MOMENTUM_LOOKBACK_CALENDAR_DAYS,
            ),
            sample_scope=_SAMPLE_SCOPE_MOMENTUM,
        )
    return RankingCaliberOut(
        trade_date=as_of,
        financial_period=_NO_FINANCIAL_CALIBER,
        sample_scope=(
            _SAMPLE_SCOPE_DIVIDEND if spec.basic_column == "dv_ratio" else spec.sample_scope
        ),
    )


def _notes(
    spec: RankingBoardSpec,
    *,
    returned_rows: int,
    limit: int,
    eligible_rows: int,
    truncated_ties: bool,
    all_have_financials: int | None,
) -> list[str]:
    notes: list[str] = list(spec.extra_notes)
    if returned_rows < limit:
        if spec.source_kind == "financial_statement":
            detail = (
                f"已公告财报的标的有 {all_have_financials} 只，但其中 {spec.metric_key} 取不到值的"
                f"（NULL，非 0）不计入分母"
                if all_have_financials is not None
                else "该榜分母只算指标可取值的标的"
            )
            notes.append(
                f"不足 {limit} 条：本榜实际返回 {returned_rows} 条，口径下可参与排序的标的仅 "
                f"{eligible_rows} 只（{detail}）。"
            )
        else:
            notes.append(
                f"不足 {limit} 条：本榜实际返回 {returned_rows} 条，口径下可参与排序的标的仅 "
                f"{eligible_rows} 只（口径见 caliber.sample_scope）。"
            )
    if truncated_ties:
        notes.append(
            f"第 {limit} 名存在并列：第 {limit + 1} 名的 {spec.metric_key} 与第 {limit} 名相同，"
            f"榜尾是按 stock_id 升序切出来的，不是唯一前 {limit} 名。"
        )
    return notes


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


async def _fetch_rows(
    db: AsyncSession,
    spec: RankingBoardSpec,
    *,
    as_of: date,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """按榜型取行（``limit + 1`` 用于并列判定，由调用方减去）。"""
    params: dict[str, Any] = {"as_of": as_of, "limit": limit, "offset": offset}
    if spec.source_kind == "daily_basic":
        assert spec.basic_column is not None
        stmt = BASIC_SQL[spec.basic_column]
        # ``extra_rows`` 是「多取一行判并列」，**不是**分页参数：daily_basic 模板没有
        # OFFSET，把 offset 混进来会让 limit+offset 在 offset=0 时退化成 limit，
        # 并列判定就永远看不到第 limit+1 行（静默失效）。
        params = {"as_of": as_of, "extra_rows": limit + 1}
    elif spec.source_kind == "daily_quotes_window":
        stmt = MOMENTUM_SQL[cast(Any, spec.key)]
        params.update(
            {
                "window_days": MOMENTUM_WINDOW_TRADING_DAYS,
                "lookback_days": MOMENTUM_LOOKBACK_CALENDAR_DAYS,
            }
        )
    else:
        assert spec.financial_column is not None
        stmt = FINANCIAL_SQL[spec.financial_column]
        params["metric_key"] = spec.financial_metric_key
    rows = (await db.execute(stmt, params)).mappings().all()
    return [dict(row) for row in rows]


async def _scalar_count(db: AsyncSession, stmt: TextClause, params: dict[str, Any]) -> int:
    row = (await db.execute(stmt, params)).mappings().one()
    return int(row["stock_count"])


async def _eligible_rows(
    db: AsyncSession,
    spec: RankingBoardSpec,
    as_of: date,
) -> tuple[int, list[tuple[str, str, int]]]:
    """(分母, [(end_date, 报告期标签, 只数)])。

    分母一律**单独聚合**，绝不用取回行数充当——排序查询只回 ``limit + 1`` 行，
    拿它当分母会把"全市场 5559 只可排"说成"只有 100 只"，"不足 100 条的原因"即失真。
    """
    if spec.source_kind == "financial_statement":
        pass  # 走下面的聚合 CTE
    elif spec.source_kind == "daily_basic":
        assert spec.basic_column is not None
        return await _scalar_count(db, BASIC_COUNT_SQL[spec.basic_column], {"as_of": as_of}), []
    else:
        return (
            await _scalar_count(
                db,
                MOMENTUM_COUNT_SQL,
                {
                    "as_of": as_of,
                    "window_days": MOMENTUM_WINDOW_TRADING_DAYS,
                    "lookback_days": MOMENTUM_LOOKBACK_CALENDAR_DAYS,
                },
            ),
            [],
        )
    assert spec.financial_column is not None
    rows = (
        (
            await db.execute(
                FINANCIAL_STATS_SQL[spec.financial_column],
                {"as_of": as_of, "metric_key": spec.financial_metric_key},
            )
        )
        .mappings()
        .all()
    )
    total = 0
    mix: list[tuple[str, str, int]] = []
    for row in rows:
        count = int(row["stock_count"])
        total += count
        mix.append(
            (
                row["end_date"].isoformat(),
                _report_type_label(str(row["report_type"])),
                count,
            )
        )
    return total, mix


async def _all_have_financials(db: AsyncSession, as_of: date) -> int:
    row = (await db.execute(_all_have_count_sql(), {"as_of": as_of})).mappings().one()
    return int(row["stock_count"])


async def get_ranking_board(
    db: AsyncSession,
    cache: CacheClient | None,
    board_key: str,
    limit: int = 100,
) -> RankingBoardResponseOut:
    """返回某榜的 top-N（默认 100）。

    与既有 ``market_service.get_rankings`` **同一套模式**（复用而非另起一套）：

    1. **先解析口径日，再看缓存**——缓存键带 resolved ``as_of``
       （``market:ranking-board:{key}:{limit}:{as_of}``），脏日不可能把上一日的
       负载端出来；
    2. ``as_of`` / ``as_of_quality`` 来自完整性判据
       （``market_day_service.resolve_latest_complete_day``），不是裸 ``max(trade_date)``；
    3. 空库 → 一致的**空 payload 降级**（不 500、不写缓存，首次 ingest 后立即恢复）。
    """
    spec = get_board(board_key)

    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    if md is None:
        # 公开免登录页面：空 daily_quotes 必须降级成自洽的空 payload（与 get_rankings 同）。
        as_of = last_weekday(_today_sh())
        return RankingBoardResponseOut(
            as_of=as_of,
            as_of_quality="partial",
            is_latest_trading_day=False,
            key=spec.key,
            label=spec.label,
            category=spec.category,
            source=spec.source_kind,
            sort_direction=spec.sort_direction,
            metric_unit=spec.metric_unit,
            limit=limit,
            returned_rows=0,
            eligible_rows=0,
            truncated_ties=False,
            notes=[f"不足 {limit} 条：本榜实际返回 0 条 —— 行情库当前没有任何完整交易日。"],
            caliber=_caliber(spec, as_of, []),
            items=[],
        )

    cache_key = f"market:ranking-board:{spec.key}:{limit}:{md.day.isoformat()}"
    if cache:
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):  # 非 dict = 外来/历史形状，当未命中
            return RankingBoardResponseOut.model_validate(
                {**cached, "as_of_quality": cached.get("as_of_quality", "partial")}
            )

    fetched = await _fetch_rows(db, spec, as_of=md.day, limit=limit, offset=0)
    rows = fetched[:limit]
    truncated_ties = False
    if len(fetched) > limit:
        truncated_ties = _metric_value(spec, fetched[limit]) == _metric_value(
            spec, fetched[limit - 1]
        )

    eligible, period_mix = await _eligible_rows(db, spec, md.day)
    all_have: int | None = None
    if spec.source_kind == "financial_statement" and len(rows) < limit:
        all_have = await _all_have_financials(db, md.day)

    items = [
        _row_to_item(spec, row, rank=index + 1, as_of=md.day) for index, row in enumerate(rows)
    ]
    out = RankingBoardResponseOut(
        as_of=md.day,
        as_of_quality=md.quality,
        as_of_reason=md.reason,
        # 与 get_rankings 完全同一条判据：展示的这一天是不是当前（上海时区）预期最近交易日。
        is_latest_trading_day=md.day == last_weekday(_today_sh()),
        key=spec.key,
        label=spec.label,
        category=spec.category,
        source=spec.source_kind,
        sort_direction=spec.sort_direction,
        metric_unit=spec.metric_unit,
        limit=limit,
        returned_rows=len(rows),
        eligible_rows=eligible,
        truncated_ties=truncated_ties,
        notes=_notes(
            spec,
            returned_rows=len(rows),
            limit=limit,
            eligible_rows=eligible,
            truncated_ties=truncated_ties,
            all_have_financials=all_have,
        ),
        caliber=_caliber(spec, md.day, period_mix),
        items=items,
    )
    if cache:
        # 与既有榜单同一 TTL（5 分钟）。财务类榜的输入按 (交易日, 已公告期) 是确定性的，
        # 5 分钟足以挡住"7 个榜 × 翻页轮询"的重复全市场排序，又短到不会把刚入库的
        # 财报挡在外面（口径日变更时缓存键自然换新）。
        await cache.set(cache_key, out.model_dump(mode="json"), _MARKET_CACHE_TTL)
    return out


def board_catalog() -> list[tuple[str, str, str]]:
    """(key, label, category) 列表——给前端 Tab 与文档枚举用（不含数据）。"""
    return [(key, BOARDS[key].label, BOARDS[key].category) for key in BOARD_ORDER]


__all__ = [
    "BOARDS",
    "BOARD_ORDER",
    "MOMENTUM_WINDOW_TRADING_DAYS",
    "MOMENTUM_LOOKBACK_CALENDAR_DAYS",
    "NEW_STOCK_CALENDAR_DAYS",
    "RankingBoardSpec",
    "board_catalog",
    "get_board",
    "get_ranking_board",
]
