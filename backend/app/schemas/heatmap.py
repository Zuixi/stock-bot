"""市场/个股热力图（S1.5）响应契约。

一个批量端点回**一次**全市场快照；四个分组维度（全市场 / 申万一级 / 概念板块 /
自选）共用同一批 items，分组在前端完成（见 schemas 文件头与
frontend/src/features/market/heatmapTree.ts）。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.market import AsOfQuality


class HeatmapStockItem(BaseModel):
    """一只标的的热力图行。

    单位与缺值口径：
    * ``pct_chg``（%）恒非空——SQL 只取当日真有涨跌的行；
    * ``amount``（成交额，**千元**，TuShare 原生）与 ``total_mv``（总市值，
      **万元**，daily_basic）可空：缺值回 ``null``，前端画「无面积」小格，
      绝不用 0 冒充（ADR 0004：快照型数据只做当日/向前，不回填）；
    * ``sw_l1_code`` / ``concept_code``：该标的的分组归属码（各自取码最小的一个，
      每股一组）；无归属为 ``null`` → 前端「未分类」组。
    """

    symbol: str
    name: str | None = None
    pct_chg: float
    amount: float | None = None
    total_mv: float | None = None
    sw_l1_code: str | None = None
    concept_code: str | None = None


class HeatmapGroup(BaseModel):
    """分组组头（全市场 / 申万一级 / 概念板块 / 自选共用）。

    ``code`` 为空串 = 合成组（全市场 / 自选 / 未分类）；``name`` 为空串 =
    码未知（前端回落显示码本身，不猜名）。
    """

    code: str
    name: str
    count: int


class HeatmapTreemapOut(BaseModel):
    """``GET /api/v1/market/heatmap/treemap`` 信封。

    ``count`` = ``len(items)``（当日真有涨跌的标的数；面积/颜色字段可各自缺）。
    ``amount_unit`` / ``mv_unit`` 是显式单位声明——面积切换的 UI 标注直接消费，
    不允许前端猜单位（避免「千元当元」量级误读）。
    """

    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    count: int = 0
    amount_unit: Literal["千元"] = "千元"
    mv_unit: Literal["万元"] = "万元"
    items: list[HeatmapStockItem] = Field(default_factory=list)


class ConceptBoardRef(BaseModel):
    """概念板块下拉项（``GET /api/v1/market/heatmap/concept-boards``）。

    键用前端直接消费的 camelCase（``boardCode``）：这一项没有 snake_case 消费方，
    双命名只会多一层映射漂移。
    """

    model_config = {"populate_by_name": True}

    board_code: str = Field(serialization_alias="boardCode", validation_alias="boardCode")
    name: str
