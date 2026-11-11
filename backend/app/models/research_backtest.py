"""Backtest run ledger (spec §S2.3).

一次回测一行：**参数、样本切分、PIT 标记、逐期限统计**全部落库，而不是只返回
一个临时 JSON。理由（S2.3 / 计划 Q4）：

* 结论要可复现——同一 ``rule_key + rule_version + split + 数据窗口`` 必须能还原；
* ``pit_enforced`` 是**硬字段**：没有通过 PIT 对照的回测不允许对外展示，
  因此不能只在响应里临时算；
* ``split`` 必填（样本内/样本外），防止"全样本调参再全样本报收益"。

表只存**结果**，不存逐笔成交/持仓——事件研究只需要每个事件的远期收益，
不构建组合、不扣交易成本（单规则事件研究，不是策略回测）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("idx_backtest_runs_rule", "rule_key", "window_to"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_key: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="industry")
    scope_keys: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    window_from: Mapped[date] = mapped_column(Date, nullable=False)
    window_to: Mapped[date] = mapped_column(Date, nullable=False)
    horizons: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    benchmark: Mapped[str] = mapped_column(String(16), nullable=False)
    split: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pit_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
