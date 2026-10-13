"""北向 ``moneyflow_hsgt.north_money`` 的**口径判别**（Ruling 62/63）。

## 为什么需要这个模块

上游 TuShare ``moneyflow_hsgt.north_money`` **同一列换过语义**（实机验证，2026-10-13）：

- ``trade_date < 2024-08-19``：**净买入**（百万元）——148 日里 **83 个负值**，
  区间 ``[-13056.64, 22448.99]``，负值是常态；
- ``trade_date >= 2024-08-19``：**成交额**（百万元）——502 日**零负值、零零值**，
  区间 ``[77584.24, 510101.29]``。独立产地东财 ``stock_hsgt_hist_em`` 的
  「当日成交净买额」断点后 **502/502 全空** ⇒ **每日净买入确实已取消**。

口径证据：``docs/design/data-source.md`` 附.9.1 ㉖ 与附.9.2 #1–#5。

## 为什么是"判别函数 + 行级标注"而不是"读的时候靠数值正负猜"

历史上 ``northbound_daily.net_amount`` / ``get_northbound_series`` / ``NorthboundCard``
把断点后的**成交额**当成**净流入**发布，甚至求和成"近30日累计净流入"——一个没有意义的数。
数值正负**不能**当口径判据（断点前的净买入也可能是正数；成交量口径恒正只是巧合），
所以口径必须由**数据自己显式携带**，读取方先看口径再解释数值。

本模块是**断点常量的唯一活来源**（ingest 与读面共用）：迁移脚本里的同一日期是**冻结的
历史**（迁移不得 import 应用代码），两边的一致性由
``tests/test_market_data_northbound_caliber.py`` 的断言钉住。
"""

from __future__ import annotations

from datetime import date

#: 上游 ``north_money`` **换语义**的断点：此日及之后是**成交额**，之前是**净买入**。
#: 依据 docs/design/data-source.md 附.9.1 ㉖（断点前 83 个负值 / 断点后零负值）。
NORTHBOUND_TURNOVER_SINCE: date = date(2024, 8, 19)

#: 行级口径取值。``net_buy`` 只可能出现在断点前的历史行上（Ruling 30：不删也不伪装）。
NET_BUY: str = "net_buy"
TURNOVER: str = "turnover"
NORTHBOUND_AMOUNT_KINDS: tuple[str, str] = (NET_BUY, TURNOVER)


def northbound_amount_kind(trade_date: date) -> str:
    """交易日 → 该日 ``north_money`` 的口径：断点前 ``net_buy``，断点起 ``turnover``。

    纯函数（口径只由交易日决定），故迁移回填、ingest 落行、读面兜底三处可以各自调用
    而不会漂移。**绝无**第三档：返回值恒在 :data:`NORTHBOUND_AMOUNT_KINDS` 内。
    """
    return TURNOVER if trade_date >= NORTHBOUND_TURNOVER_SINCE else NET_BUY
