"""``GET /api/v1/market/regime`` 的响应契约（spec §S10.3）：只读、零新表、零新源。

字段名与 spec 的 JSON 示例逐字对齐。唯一的结构妥协是 ``forward``：
``5d`` / ``20d`` 是**数字开头**的键，不能做 pydantic 字段名（Python 标识符），
故用 ``dict[str, RegimeForwardOut | None]`` 承载，键由后端按
``market_regime_service`` 的 horizon 常量生成——前端不自己拼字符串，也就不存在
"后端算 5 日、前端读 five_d" 的漂移。

**三态的语义**（前端据此选文案，后端不改写）：

- ``state`` 有效 + 数字非 ``null``：可展示"当前处于 X（已 N 天）｜历史同态 5 日中位 +0.8%（n=37）"；
- ``state`` 有效但某 horizon 的 ``median_pct`` 为 ``null``：样本 ``n < 30``，只显示 ``n``；
- ``state`` 为 ``null``：产物缺失/损坏/落后，只显示 ``notes`` 的原因（**不是**"中性"）。
"""

from datetime import date

from pydantic import BaseModel


class RegimeForwardOut(BaseModel):
    """同态后续 N 个交易日的一格：``n`` 恒给，中位/最差 5% 在 ``n < 30`` 时一律 ``null``。"""

    #: 有完整 N 日后续窗口的入场日数（同态连续段里每天都是一个入场日）
    n: int = 0
    #: 中位收益（百分数）
    median_pct: float | None = None
    #: 最差 5% 分位（百分数）
    p05_pct: float | None = None


class MarketRegimeOut(BaseModel):
    state: str | None
    since: date | None
    duration_days: int
    #: 当前态的转移概率行（``{下一态: 概率}``，行和 = 1；未出现过出边 → 空 dict）
    transition: dict[str, float]
    forward: dict[str, RegimeForwardOut | None]
    notes: list[str]
    #: 产物截至日与阈值版本（契约外的两列，给前端标"截至哪天"与排查口径漂移用）
    as_of: date | None = None
    version: str | None = None
