"""Fixture-driven tests for :mod:`app.core.providers.eastmoney_report_client`.

No network: a fake ``httpx.AsyncClient`` returns canned payloads / raises.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.providers.eastmoney_report_client import (
    EastmoneyReportClient,
    _num,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
BEGIN = date(2026, 11, 1)
END = date(2026, 11, 10)

FULL_RECORD: dict[str, Any] = {
    "infoCode": "ABC123",
    "title": "生猪养殖行业深度：产能去化加速",
    "stockCode": "002714",
    "stockName": "牧原股份",
    "orgSName": "中信证券",
    "orgName": "中信证券股份有限公司",
    "publishDate": "2026-11-10 00:00:00.000",
    "emRatingName": "买入",
    "ratingChange": 1,
    "indvAimPriceT": "58.60",
    "indvAimPriceL": "50.00",
    "predictThisYearEps": "3.10",
    "predictNextYearEps": "4.25",
    "predictNextTwoYearEps": "5.05",
    "predictThisYearPe": "12.5",
    "predictNextYearPe": "9.1",
    "predictNextTwoYearPe": "7.7",
    "indvInduName": "农牧饲渔",
    "indvInduCode": "BK0433",
    "researcher": "张三,李四",
    "encodeUrl": "abcDEF123",
}


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=httpx.Request("GET", "http://reportapi.eastmoney.com/report/list"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


class FakeAsyncClient:
    """Minimal stand-in: responses are payloads, exceptions, or callables(params)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.is_closed = False

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        item = self._responses[index]
        if callable(item):
            item = item(params or {})
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, FakeResponse) else FakeResponse(item)


def _client(responses: list[Any]) -> tuple[EastmoneyReportClient, FakeAsyncClient]:
    fake = FakeAsyncClient(responses)
    return EastmoneyReportClient(fake, backoff=0.0), fake


async def test_maps_all_fields_for_one_record() -> None:
    client, _ = _client([{"hits": 1, "data": [FULL_RECORD]}])

    rows = await client.fetch_reports(BEGIN, END)

    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "source_item_id",
        "title",
        "stock_code",
        "stock_name",
        "org_name",
        "published_at",
        "rating",
        "rating_change",
        "target_price",
        "eps_this_year",
        "eps_next_year",
        "eps_next_two_year",
        "pe_this_year",
        "pe_next_year",
        "pe_next_two_year",
        "industry",
        "industry_code",
        "researcher",
        "pdf_url",
    }
    assert row["source_item_id"] == "ABC123"
    assert row["title"] == "生猪养殖行业深度：产能去化加速"
    assert row["stock_code"] == "002714"
    assert row["stock_name"] == "牧原股份"
    assert row["org_name"] == "中信证券"
    assert row["published_at"] == datetime(2026, 11, 10, tzinfo=SHANGHAI)
    assert row["published_at"].isoformat() == "2026-11-10T00:00:00+08:00"
    assert row["rating"] == "买入"
    assert row["rating_change"] == 1
    assert isinstance(row["rating_change"], int)
    assert row["target_price"] == pytest.approx(58.6)
    assert row["eps_this_year"] == pytest.approx(3.10)
    assert row["eps_next_year"] == pytest.approx(4.25)
    assert row["eps_next_two_year"] == pytest.approx(5.05)
    assert row["pe_this_year"] == pytest.approx(12.5)
    assert row["pe_next_year"] == pytest.approx(9.1)
    assert row["pe_next_two_year"] == pytest.approx(7.7)
    assert row["industry"] == "农牧饲渔"
    assert row["industry_code"] == "BK0433"
    assert row["researcher"] == "张三,李四"


async def test_pdf_url_matches_eastmoney_pattern() -> None:
    client, _ = _client([{"hits": 1, "data": [FULL_RECORD]}])

    row = (await client.fetch_reports(BEGIN, END))[0]

    assert row["pdf_url"] == "https://pdf.dfcfw.com/pdf/H3_ABC123_1.pdf?abcDEF123"


async def test_empty_numeric_fields_map_to_none_not_zero() -> None:
    record = dict(FULL_RECORD)
    record.update(
        {
            "indvAimPriceT": "",
            "indvAimPriceL": "",
            "predictThisYearEps": "",
            "predictNextYearEps": None,
            "predictNextTwoYearEps": "nan",
            "predictThisYearPe": "",
            "predictNextYearPe": None,
            "predictNextTwoYearPe": "nan",
            "ratingChange": "",
            "encodeUrl": "",
        }
    )
    client, _ = _client([{"hits": 1, "data": [record]}])

    row = (await client.fetch_reports(BEGIN, END))[0]

    for key in (
        "target_price",
        "eps_this_year",
        "eps_next_year",
        "eps_next_two_year",
        "pe_this_year",
        "pe_next_year",
        "pe_next_two_year",
        "rating_change",
        "pdf_url",
    ):
        assert row[key] is None, key
    assert _num("nan") is None


def _page_of(page_size: int) -> Any:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        page = int(params["pageNum"])
        data = [
            {
                **FULL_RECORD,
                "infoCode": f"P{page}-{i}",
                "encodeUrl": f"u{page}-{i}",
            }
            for i in range(page_size)
        ]
        return {"hits": 250, "data": data}

    return handler


async def test_page_cap_with_remaining_hits_raises_instead_of_truncating() -> None:
    client, fake = _client([_page_of(3)])

    with pytest.raises(RuntimeError, match="truncated"):
        await client.fetch_reports(BEGIN, END, page_size=3, max_pages=1)

    assert len(fake.calls) == 1


async def test_retries_twice_then_succeeds() -> None:
    client, fake = _client(
        [
            httpx.ConnectError("boom"),
            httpx.ReadTimeout("slow"),
            {"hits": 1, "data": [FULL_RECORD]},
        ]
    )

    rows = await client.fetch_reports(BEGIN, END)

    assert [row["source_item_id"] for row in rows] == ["ABC123"]
    assert len(fake.calls) == 3


async def test_retry_exhaustion_raises_runtime_error() -> None:
    client, fake = _client([httpx.ConnectError("boom")])

    with pytest.raises(RuntimeError, match="3 attempts"):
        await client.fetch_reports(BEGIN, END)
    assert len(fake.calls) == 3


async def test_record_without_info_code_is_skipped() -> None:
    orphan = dict(FULL_RECORD)
    orphan.pop("infoCode")
    client, _ = _client([{"hits": 2, "data": [orphan, FULL_RECORD]}])

    rows = await client.fetch_reports(BEGIN, END)

    assert [row["source_item_id"] for row in rows] == ["ABC123"]


async def test_sends_required_headers_and_fixed_params() -> None:
    client, fake = _client([{"hits": 1, "data": [FULL_RECORD]}])

    await client.fetch_reports(BEGIN, END, page_size=50)

    call = fake.calls[0]
    assert call["url"] == "http://reportapi.eastmoney.com/report/list"
    headers = call["headers"]
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert headers["Referer"] == "http://data.eastmoney.com/report/"
    assert call["params"] == {
        "pageSize": "50",
        "pageNo": "1",
        "qType": "0",
        "beginTime": "2026-11-01",
        "endTime": "2026-11-10",
        "industryCode": "*",
        "industry": "*",
        "rating": "",
        "ratingChange": "",
        "orgCode": "",
        "code": "*",
        "rcode": "",
        "p": "1",
        "pageNum": "1",
    }
