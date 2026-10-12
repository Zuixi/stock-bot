"""向后兼容转发：数据新鲜度响应模型已迁到 :mod:`app.schemas.market_data`。

Task 3 需要给 ``DataFreshnessOut`` 加 ``coverage``（只读巡检域覆盖率），而覆盖率行模型
（``CoverageOut``）与板块/资金流/龙虎榜等响应模型同源 —— 放在 ``market_data`` 里可以避免
"market_data ↔ reconciliation" 的循环导入。旧导入路径（本模块）保留为 re-export，不是
第二份定义（``is`` 恒等由 ``tests/test_market_coverage.py`` 守卫）。
"""

from __future__ import annotations

from app.schemas.market_data import (
    AdjFactorRepairOut,
    CoverageOut,
    DataFreshnessOut,
    DomainFreshnessOut,
    JobFailureOut,
)

__all__ = [
    "AdjFactorRepairOut",
    "CoverageOut",
    "DataFreshnessOut",
    "DomainFreshnessOut",
    "JobFailureOut",
]
