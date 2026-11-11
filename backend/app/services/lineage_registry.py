"""指标血缘注册表（P7）——"这个数字从哪来"的唯一真相.

刻意用**代码即配置**而不是新表：6 个指标的定义、口径版本、来源表、消费者端点
都跟代码一起评审、一起回滚。``/api/v1/provenance/{metric_key}`` 与跨页一致性检查
都从这里读，避免"文档写一套、SQL 写另一套"。

新增指标 = 在 ``METRICS`` 加一条 + 在 ``tests/test_provenance.py`` 锁定解析结果。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRef:
    """一个来源表；``date_column`` 用来按日计数（"这条链路今天有多少行"）。"""

    table: str
    domain: str
    date_column: str = "trade_date"


@dataclass(frozen=True)
class MetricLineage:
    """一个可溯源指标的完整定义."""

    key: str
    label: str
    unit: str
    caliber_version: str
    definition: str
    value_sql: str
    sources: tuple[SourceRef, ...]
    consumers: tuple[str, ...]
    #: 需要 ``scope_key`` 的指标（如申万一级行业）；SQL 里用 ``:scope_key`` 引用。
    scope_required: bool = False


METRICS: dict[str, MetricLineage] = {
    "zt_count": MetricLineage(
        key="zt_count",
        label="涨停家数",
        unit="家",
        caliber_version="limit-up-v3",
        definition="收盘价 ≥ 交易所涨停价且非停牌的股票数；口径见 limit_up_calculator",
        value_sql="SELECT zt_count::float FROM market_sentiment_daily WHERE trade_date = :day",
        sources=(
            SourceRef("market_sentiment_daily", "sentiment"),
            SourceRef("daily_quotes", "daily_quotes"),
            SourceRef("stock_price_limits", "price_limits"),
        ),
        consumers=(
            "/api/v1/market/limit-up/calendar",
            "/api/v1/market/sentiment/metrics/zt_count",
        ),
    ),
    "broken_rate": MetricLineage(
        key="broken_rate",
        label="炸板率",
        unit="%",
        caliber_version="limit-up-v3",
        definition="炸板家数 / (涨停家数 + 炸板家数)；仅样本 n≥5 时给 Wilson 区间",
        value_sql="SELECT broken_rate::float FROM market_sentiment_daily WHERE trade_date = :day",
        sources=(
            SourceRef("market_sentiment_daily", "sentiment"),
            SourceRef("market_limit_up_stock_daily", "limit_up_stock_daily"),
        ),
        consumers=(
            "/api/v1/market/sentiment/metrics/broken_rate",
            "/api/v1/market/sentiment/trends",
        ),
    ),
    "promo_1to2": MetricLineage(
        key="promo_1to2",
        label="1进2 晋级率",
        unit="%",
        caliber_version="limit-up-v3",
        definition="昨日涨停今日继续涨停的家数 / 昨日涨停家数（样本 n 随日披露）",
        value_sql="SELECT promo_1to2::float FROM market_sentiment_daily WHERE trade_date = :day",
        sources=(
            SourceRef("market_sentiment_daily", "sentiment"),
            SourceRef("market_limit_up_stock_daily", "limit_up_stock_daily"),
        ),
        consumers=(
            "/api/v1/market/sentiment/metrics/promo_1to2",
            "/api/v1/market/sentiment/trends",
        ),
    ),
    "total_amount": MetricLineage(
        key="total_amount",
        label="全市场成交额",
        unit="千元",
        caliber_version="panorama-v1",
        definition="当日 daily_quotes.amount 求和（本地派生，B 类可回算）",
        value_sql="SELECT amount::float FROM market_breadth_daily WHERE trade_date = :day",
        sources=(
            SourceRef("market_breadth_daily", "market_breadth"),
            SourceRef("daily_quotes", "daily_quotes"),
        ),
        consumers=("/api/v1/market/panorama",),
    ),
    "up_count": MetricLineage(
        key="up_count",
        label="上涨家数",
        unit="家",
        caliber_version="panorama-v1",
        definition="当日 pct_chg > 0 的股票数（本地派生，B 类可回算）",
        value_sql="SELECT up_count::float FROM market_breadth_daily WHERE trade_date = :day",
        sources=(
            SourceRef("market_breadth_daily", "market_breadth"),
            SourceRef("daily_quotes", "daily_quotes"),
        ),
        consumers=("/api/v1/market/panorama", "/api/v1/market/distribution"),
    ),
    "sw_l1_pct_chg": MetricLineage(
        key="sw_l1_pct_chg",
        label="申万一级涨跌幅",
        unit="%",
        caliber_version="sw-l1-v1",
        definition="该申万一级行业成分股当日涨跌幅中位数；归属按当前申万快照回溯",
        value_sql=(
            "SELECT pct_chg::float FROM market_industry_daily "
            "WHERE trade_date = :day AND industry_code = :scope_key"
        ),
        sources=(
            SourceRef("market_industry_daily", "market_industry"),
            SourceRef("daily_quotes", "daily_quotes"),
        ),
        consumers=("/api/v1/market/panorama",),
        scope_required=True,
    ),
}


def get(metric_key: str) -> MetricLineage | None:
    return METRICS.get(metric_key)
