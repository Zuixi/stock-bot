"""SQLAlchemy ORM models."""

from app.models.cluster import ClusterExplanation, ClusteringMember, ClusteringRun
from app.models.concept import ConceptBoard, ConceptMember, ConceptMemberChange
from app.models.daily_basic import DailyBasicIndicator
from app.models.feature import StockFeature
from app.models.financial import (
    BalanceSheetFacts,
    CashFlowStatementFacts,
    FinancialMetric,
    FinancialRawRecord,
    FinancialReportVersion,
    IncomeStatementFacts,
)
from app.models.index_daily import IndexDaily
from app.models.industry_research import (
    IndustryIngestState,
    IndustryKnowledge,
    IndustryMetric,
    IndustryReferencePoint,
    IndustrySignal,
)
from app.models.journal import DecisionJournalEntry, DecisionJournalReview
from app.models.market_data import (  # noqa: F401
    Announcement,
    BlockTrade,
    DragonTigerEntry,
    NorthboundDaily,
    SectorMoneyflowSnapshot,
    ShareFloat,
    StockRepurchase,
)

# 全景三表（market_breadth_daily / _bucket_daily / market_industry_daily）的模型定义在
# `models/market_data.py`（并行会话的全景特性自带 ingest + 读面）；本文件不再单独维护副本，
# 否则 SQLAlchemy 会因同名表重复定义而 ImportError。
from app.models.quote import DailyQuote
from app.models.securities import CbDaily, FundEtfDaily
from app.models.sse_index_snapshot import SseIndexSnapshot
from app.models.stock import Stock, StockHistory, StockUserTag
from app.models.sw_industry import StockCustomSwTag, SwIndustryClass, SwIndustryMember
from app.models.task import Task
from app.models.watchlist import UserWatchlist, UserWatchlistItem

__all__ = [
    "Stock",
    "StockHistory",
    "StockUserTag",
    "UserWatchlist",
    "UserWatchlistItem",
    "DailyQuote",
    "DailyBasicIndicator",
    "IndexDaily",
    "SseIndexSnapshot",
    "StockFeature",
    "ClusteringRun",
    "ClusteringMember",
    "ClusterExplanation",
    "ConceptBoard",
    "ConceptMember",
    "ConceptMemberChange",
    "SwIndustryClass",
    "SwIndustryMember",
    "StockCustomSwTag",
    "Task",
    "IndustryMetric",
    "IndustryIngestState",
    "IndustryReferencePoint",
    "IndustrySignal",
    "IndustryKnowledge",
    "DecisionJournalEntry",
    "DecisionJournalReview",
    "FundEtfDaily",
    "CbDaily",
    "SectorMoneyflowSnapshot",
    "DragonTigerEntry",
    "NorthboundDaily",
    "BlockTrade",
    "ShareFloat",
    "StockRepurchase",
    "Announcement",
    "MarketBreadthDaily",
    "MarketBreadthBucketDaily",
    "MarketIndustryDaily",
    "FinancialRawRecord",
    "FinancialReportVersion",
    "IncomeStatementFacts",
    "BalanceSheetFacts",
    "CashFlowStatementFacts",
    "FinancialMetric",
]
