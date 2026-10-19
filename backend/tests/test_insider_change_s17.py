"""§S1.7 董监高及关联人持股变动（官方一手）：取数映射 / 单位归一 / PIT 不对称 / 聚合。

**为什么这些用例值得存在**：本 key 的两个坑都是"静默错标"型 ——
① 沪市原生单位是**股**、深市是**万股**，混了就是 10000 倍的错；
② 深交所该表**没有披露日**，若用变动日冒充披露日，回测会系统性偏乐观。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import insider_change_service as svc

_CN = ZoneInfo("Asia/Shanghai")


class _Resp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """按调用次序返回预置页（超出则重复最后一页）。"""

    def __init__(self, pages: list[object]) -> None:
        self._pages = pages
        self.calls = 0

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> _Resp:
        page = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return _Resp(page)


def _sse_page(rows: list[dict], page_count: int = 1) -> dict:
    return {"pageHelp": {"pageCount": page_count}, "result": rows}


def _szse_page(rows: list[dict]) -> list[dict]:
    return [{"data": rows}]


# ── 沪市：单位（股 → 万股）与 PIT（FORM_DATE）──────────────────────────


@pytest.mark.asyncio
async def test_sse_parses_shares_to_wan_and_uses_form_date_as_pit():
    client = _FakeClient(
        [
            _sse_page(
                [
                    {
                        "COMPANY_CODE": "600000",
                        "CHANGE_NUM": "-54000",  # 原生**股**
                        "CURRENT_AVG_PRICE": "9.93",
                        "CHANGE_DATE": "2026-10-15",
                        "FORM_DATE": "2026-10-16",  # 填报=披露日
                        "NAME": "张三",
                        "DUTY": "董事",
                        "CHANGE_REASON": "二级市场买卖",
                    }
                ]
            )
        ]
    )
    events = await svc.fetch_sse_events(client, date(2026, 10, 12), date(2026, 10, 16))
    assert len(events) == 1
    event = events[0]
    assert event.symbol == "600000" and event.market == "SH"
    # 54000 股 = 5.4 万股，卖出为负
    assert event.delta_wan == pytest.approx(-5.4)
    assert event.price == pytest.approx(9.93)
    # PIT = FORM_DATE 当日 21:00（**不是**变动日 CHANGE_DATE）
    assert event.period == date(2026, 10, 16)
    assert event.published_at == datetime(2026, 10, 16, 21, 0, tzinfo=_CN)


@pytest.mark.asyncio
async def test_sse_missing_form_date_falls_back_to_change_date_without_pit():
    """没有 FORM_DATE 时不得编造披露时点：period 退回变动日，`published_at` 保持 NULL。"""
    client = _FakeClient(
        [
            _sse_page(
                [
                    {
                        "COMPANY_CODE": "688575",
                        "CHANGE_NUM": "123800",
                        "CHANGE_DATE": "2026-10-15",
                        "FORM_DATE": None,
                    }
                ]
            )
        ]
    )
    events = await svc.fetch_sse_events(client, date(2026, 10, 15), date(2026, 10, 15))
    assert events[0].period == date(2026, 10, 15)
    assert events[0].published_at is None


# ── 深市：单位（原生万股）与"无披露日" ──────────────────────────────────


@pytest.mark.asyncio
async def test_szse_keeps_wan_units_and_has_no_pit():
    rows = [
        {
            "zqdm": "000597",
            "jyrq": "2026-10-15",
            "bdgs": "-6.50",  # 原生**万股**
            "bdjj": "4.80",
            "ggxm": "郭建民",
            "zw": "董事",
            "bdyy": "竞价交易",
        }
    ]
    client = _FakeClient([_szse_page(rows)])
    events = await svc.fetch_szse_events(client, date(2026, 10, 12), date(2026, 10, 16))
    assert len(events) == 1
    event = events[0]
    assert event.market == "SZ" and event.delta_wan == pytest.approx(-6.5)
    # **没有披露日** —— 必须保持 None（由行上的 flag 显式声明，见下行构造用例）
    assert event.published_at is None
    assert event.period == date(2026, 10, 15)


# ── 聚合：买正卖负、金额只在有价时累加 ─────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_nets_buy_minus_sell_and_keeps_members():
    events = [
        svc.InsiderEvent(
            "600000", "SH", date(2026, 10, 16), 5.0, 10.0, None, "A", "董事", "买", None
        ),
        svc.InsiderEvent(
            "600000", "SH", date(2026, 10, 16), -2.0, 10.0, None, "B", "监事", "卖", None
        ),
        svc.InsiderEvent(
            "600000", "SH", date(2026, 10, 16), 1.0, None, None, "C", None, "买", None
        ),
    ]
    days = svc.aggregate_events(events)
    assert len(days) == 1
    day = days[0]
    assert day.delta_wan == pytest.approx(4.0)
    assert day.buy_wan == pytest.approx(6.0)
    assert day.sell_wan == pytest.approx(-2.0)
    # 金额只累加有价的：5*10 + (-2)*10 = 30（万元），无价那条只计数
    assert day.amount_wan == pytest.approx(30.0)
    assert day.events_without_price == 1
    assert day.n_events == 3
    assert [m["member"] for m in day.members] == ["A", "B", "C"]


def test_row_marks_missing_disclosure_date_explicitly():
    """深市行：`published_at=None` 必须带显式 flag（共享 upsert 守卫只放过显式豁免）。"""
    day = svc.InsiderDay("000597", "SZ", date(2026, 10, 15), published_at=None)
    row = svc.insider_row(day, stock_id=42)
    assert row["published_at"] is None
    assert row["extra"]["disclosure_date_unavailable"] is True
    assert row["caliber_version"] == svc.INSIDER_CALIBER_VERSION
    assert row["unit"] == "万股" and row["freq"] == "daily"
    assert row["source_tier"] == "official"

    with_pit = svc.InsiderDay(
        "600000", "SH", date(2026, 10, 16), published_at=datetime(2026, 10, 16, 21, 0, tzinfo=_CN)
    )
    assert "disclosure_date_unavailable" not in svc.insider_row(with_pit, 7)["extra"]


# ── 共享 PIT 守卫：只放过"显式豁免"的 NULL ─────────────────────────────


@pytest.mark.asyncio
async def test_pit_guard_allows_only_explicit_exemption(monkeypatch):
    from app.services import market_activity_service as mas

    written: list[list[dict]] = []

    async def fake_upsert(db, rows):
        written.append(list(rows))
        return len(rows)

    monkeypatch.setattr(mas.metric_repo, "upsert_metrics", fake_upsert)

    base = {
        "industry_key": "market",
        "stock_id": 1,
        "metric_key": "insider_net_change_daily",
        "source": "sse_szse_insider_change",
        "source_tier": "official",
        "freq": "daily",
        "period": date(2026, 10, 15),
        "value": 1.0,
        "unit": "万股",
        "caliber_version": "s1.7-insider-v1",
    }
    # 缺 PIT 且**没有**显式豁免 ⇒ 响亮失败
    with pytest.raises(RuntimeError, match="published_at"):
        await mas.upsert_metric_rows(None, [{**base, "extra": None, "published_at": None}])  # type: ignore[arg-type]
    # 显式豁免 ⇒ 允许落库
    ok = {**base, "extra": {"disclosure_date_unavailable": True}, "published_at": None}
    assert await mas.upsert_metric_rows(None, [ok]) == 1  # type: ignore[arg-type]
    assert written and written[0][0]["metric_key"] == "insider_net_change_daily"


# ── 分页：翻满上限必须响亮失败（不得静默截断）──────────────────────────


@pytest.mark.asyncio
async def test_sse_pagination_overflow_raises(monkeypatch):
    monkeypatch.setattr(svc, "SSE_MAX_PAGES", 2)
    full_page = _sse_page(
        [
            {
                "COMPANY_CODE": "600000",
                "CHANGE_NUM": "100",
                "CHANGE_DATE": "2026-10-15",
                "FORM_DATE": "2026-10-16",
            }
        ],
        page_count=99,
    )
    client = _FakeClient([full_page])
    with pytest.raises(RuntimeError, match="页数超过上限"):
        await svc.fetch_sse_events(client, date(2026, 10, 12), date(2026, 10, 16))
