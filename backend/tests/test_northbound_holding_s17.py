"""§S1.7 陆股通持仓（季度）：季末/发布日规则、单位归一万股、分批取数与上限守卫。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services import northbound_holding_service as svc

_CN = ZoneInfo("Asia/Shanghai")


class _FakeClient:
    def __init__(self, per_exchange: dict[str, pd.DataFrame]) -> None:
        self._per_exchange = per_exchange
        self.calls: list[dict] = []

    async def fetch_hk_hold(self, trade_date: str, *, exchange=None, ts_code=None):
        self.calls.append({"trade_date": trade_date, "exchange": exchange})
        return self._per_exchange.get(exchange or "", pd.DataFrame())


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_quarter_end_and_recent_quarters():
    assert svc.quarter_end(date(2026, 10, 17)) == date(2026, 9, 30)
    assert svc.quarter_end(date(2026, 1, 5)) == date(2025, 12, 31)
    assert svc.quarter_end(date(2026, 3, 31)) == date(2026, 3, 31)
    assert svc.recent_quarter_ends(date(2026, 10, 17), 2) == [date(2026, 6, 30), date(2026, 9, 30)]


def test_publish_day_is_fifth_trading_day_after_quarter_end():
    """发布规则 = 季末后**第 5 个交易日**（不是固定日历偏移）。"""
    q = date(2026, 9, 30)
    days = [date(2026, 10, d) for d in (8, 9, 12, 13, 14, 15)]  # 升序交易日
    assert svc.publish_day(q, days) == date(2026, 10, 14)
    with pytest.raises(RuntimeError, match="不得猜"):
        svc.publish_day(q, days[:4])


@pytest.mark.asyncio
async def test_fetch_splits_by_exchange_and_normalises():
    client = _FakeClient(
        {
            "SH": _df(
                [{"ts_code": "600000.SH", "vol": 12_345_600, "ratio": 1.2, "name": "浦发银行"}]
            ),
            "SZ": _df(
                [{"ts_code": "000001.SZ", "vol": 8_000_000, "ratio": 0.8, "name": "平安银行"}]
            ),
        }
    )
    holdings = await svc.fetch_quarter_holdings(client, date(2026, 9, 30))
    assert set(holdings) == {"600000.SH", "000001.SZ"}
    assert [c["exchange"] for c in client.calls] == ["SH", "SZ"]
    assert client.calls[0]["trade_date"] == "20260930"


@pytest.mark.asyncio
async def test_fetch_raises_when_row_cap_hit():
    """撞 4200 行上限 ⇒ 被截断，必须响亮失败（实测不传 exchange 时 SZ 会被截掉）。"""
    cap = svc.HK_HOLD_ROW_CAP
    big = _df(
        [{"ts_code": f"60000{i % 10}.SH", "vol": 1, "ratio": 1.0, "name": "x"} for i in range(cap)]
    )
    client = _FakeClient({"SH": big, "SZ": pd.DataFrame()})
    with pytest.raises(RuntimeError, match="被截断"):
        await svc.fetch_quarter_holdings(client, date(2026, 9, 30))


def test_holding_row_units_and_nature():
    published = datetime(2026, 10, 14, 21, 0, tzinfo=_CN)
    row = svc.holding_row(
        "600000.SH",
        7,
        {"vol": 12_345_600.0, "ratio": 1.2, "name": "浦发银行", "exchange": "SH"},
        date(2026, 9, 30),
        published,
    )
    assert row["freq"] == "quarterly" and row["unit"] == "万股"
    assert row["value"] == pytest.approx(1234.56)
    assert row["period"] == date(2026, 9, 30)
    assert row["published_at"] == published
    assert row["extra"]["holder_nature"] == "ccass_participants_aggregate"
    # 口径禁令：不得把汇总账户读作"外资"
    assert "外资" not in str(row)
