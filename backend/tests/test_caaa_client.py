"""纯单元测试：CAAA（pig.caaa.cn）能繁协会源 — 文章解析 + ingest 接线.

不触网：解析函数为纯函数，用真实文章快照 fixture（2026-04-27《2026年3月份
全国生猪产品数据》）锁定正文形状；fetcher 注入假 client。抓取端容错（异常→None）
通过覆写 ``_get_text`` 离线验证。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.providers.caaa_client import (
    CaaaClient,
    find_latest_data_article,
    parse_sow_article,
)
from app.services.industry_metric_service import (
    _covered_purge_keys,
    _fetch_caaa_sow_row,
)
from app.services.industry_registry import PIG_INDUSTRY

FIXTURE = Path(__file__).parent / "fixtures" / "caaa_article.html"
ARTICLE_URL = "https://pig.caaa.cn/html/pig_rd/pig_hydt/2026/0427/2467.html"


def _article(body: str, title: str = "2026年6月份全国生猪产品数据") -> str:
    return f"<html><head><title>{title}</title></head><body><p>{body}</p></body></html>"


# ── parse_sow_article：真实快照 ───────────────────────────────────────

def test_parse_real_article_snapshot():
    data = parse_sow_article(FIXTURE.read_text(encoding="utf-8"), ARTICLE_URL)
    assert data == {
        "period": date(2026, 3, 31),      # 标题"3月份"→该月月末（与正文"1季度末"一致）
        "inventory_wan_tou": 3904.0,
        "mom_pct": -1.5,                  # 环比下降1.5% → 负号
        "article_url": ARTICLE_URL,
        "article_date": "2026-04-27",     # 原发表日期
    }


# ── parse_sow_article：环比符号归一 ──────────────────────────────────

def test_mom_negative_verbs():
    for verb in ("下降", "降低", "回落", "降", "跌"):
        html = _article(f"2026年6月末能繁母猪存栏4000万头，环比{verb}2.5%，同比涨1%。")
        assert parse_sow_article(html, "https://x/2026/0705/1.html")["mom_pct"] == -2.5, verb


def test_mom_positive_verbs():
    for verb in ("上升", "增长", "升高", "涨", "升"):
        html = _article(f"能繁母猪存栏4000.5万头，环比{verb}2.5%，同比下降1%。")
        assert parse_sow_article(html, "https://x/2026/0705/1.html")["mom_pct"] == 2.5, verb


def test_mom_flat_without_digits_is_none():
    html = _article("能繁母猪存栏4000万头，环比持平，同比增长1%。")
    assert parse_sow_article(html, "https://x/2026/0705/1.html")["mom_pct"] is None


def test_mom_missing_keeps_inventory():
    html = _article("能繁母猪存栏4000万头。")  # 句内无环比 → mom=None 但绝对数可用
    data = parse_sow_article(html, "https://x/2026/0705/1.html")
    assert data is not None and data["mom_pct"] is None and data["inventory_wan_tou"] == 4000.0


# ── parse_sow_article：period 兜底链与失败路径 ────────────────────────

def test_period_falls_back_to_url_month_without_title_month():
    html = _article("能繁母猪存栏4000万头，环比下降1%。", title="生猪数据（转载）")
    data = parse_sow_article(html, "https://pig.caaa.cn/html/pig_rd/pig_hydt/2025/0901/2437.html")
    assert data is not None and data["period"] == date(2025, 9, 30)
    assert data["article_date"] == "2025-09-01"  # 无"原发表日期"→取 URL 日期


def test_period_falls_back_to_in_sentence_quarter_end():
    html = _article("2026年2季度末能繁母猪存栏3880万头，环比下降0.6%。", title="生猪产品数据")
    data = parse_sow_article(html, "https://pig.caaa.cn/html/pig_rd/pig_hydt/x/1.html")
    assert data is not None and data["period"] == date(2026, 6, 30)


def test_garbage_or_missing_sow_returns_none():
    assert parse_sow_article("<html><body>404 Not Found</body></html>", "https://x") is None
    # 指标说明里"存栏正常保有量为3900万头"不是数据行，不得误抓
    assert parse_sow_article(_article("能繁母猪存栏正常保有量为3900万头。"), "u/1.html") is None


def test_missing_period_returns_none():
    # 有绝对数但标题/正文/URL 均无月份 → 宁缺毋滥
    html = _article("能繁母猪存栏4000万头，环比下降1%。", title="转载")
    assert parse_sow_article(html, "https://example.com/a.html") is None


# ── find_latest_data_article：栏目列表页发现 ─────────────────────────

def test_find_latest_data_article_picks_first_match_and_joins_base():
    index = """
      <li><a href="https://pig.caaa.cn/html/pig_rd/pig_hydt/2026/0824/2482.html" class="news_page_list">
        <div class="news_title_fz ell">陆泳霖：从生猪工业饲料产量看后市行情</div></a></li>
      <li><a href="/html/pig_rd/pig_hydt/2026/0427/2467.html" class="news_page_list">
        <div class="news_title_fz ell">2026年3月份全国生猪产品数据</div></a></li>
    """
    assert find_latest_data_article(index) == (
        "https://pig.caaa.cn/html/pig_rd/pig_hydt/2026/0427/2467.html"
    )


def test_find_latest_data_article_returns_none_when_column_missing():
    assert find_latest_data_article("<html>403 Forbidden</html>") is None


# ── 抓取端容错（离线：覆写 _get_text） ────────────────────────────────

class _OfflineClient(CaaaClient):
    def __init__(self, pages: dict[str, str] | None = None, error: Exception | None = None):
        self._pages = pages or {}
        self._error = error
        self.hits: list[str] = []

    async def _get_text(self, url: str) -> str:
        self.hits.append(url)
        if self._error is not None:
            raise self._error
        return self._pages[url]


async def test_client_network_error_returns_none():
    assert await _OfflineClient(error=TimeoutError("blocked")).fetch_latest_sow_inventory() is None


async def test_client_discovers_and_parses_article(monkeypatch):
    monkeypatch.setattr("app.core.providers.caaa_client.settings.caaa_sow_article_url", "")
    index = (
        '<a href="/html/pig_rd/pig_hydt/2026/0824/2482.html" class="x">'
        '<div class="news_title_fz ell">无关文章</div></a>'
        '<a href="/html/pig_rd/pig_hydt/2026/0427/2467.html" class="x">'
        '<div class="news_title_fz ell">2026年3月份全国生猪产品数据</div></a>'
    )
    article = FIXTURE.read_text(encoding="utf-8")
    column = "https://pig.caaa.cn/html/pig_rd/pig_hydt/"
    client = _OfflineClient(pages={column: index, ARTICLE_URL: article})

    data = await client.fetch_latest_sow_inventory()

    assert client.hits == [column, ARTICLE_URL]  # 首页命中即停，不多翻页
    assert data is not None and data["period"] == date(2026, 3, 31)
    assert data["inventory_wan_tou"] == 3904.0 and data["mom_pct"] == -1.5


async def test_client_explicit_url_setting_bypasses_discovery(monkeypatch):
    article = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "app.core.providers.caaa_client.settings.caaa_sow_article_url", ARTICLE_URL
    )
    client = _OfflineClient(pages={ARTICLE_URL: article})

    data = await client.fetch_latest_sow_inventory()

    assert client.hits == [ARTICLE_URL]  # 未请求栏目列表页
    assert data is not None and data["period"] == date(2026, 3, 31)


# ── 服务接线：row 契约 + 覆盖清除 ─────────────────────────────────────

class FakeCaaaClient:
    """假协会 client：返回预置 dict / None，或抛错."""

    def __init__(self, data: dict | None | Exception):
        self._data = data

    async def fetch_latest_sow_inventory(self) -> dict | None:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


_SOW_DATA = {
    "period": date(2026, 3, 31), "inventory_wan_tou": 3904.0, "mom_pct": -1.5,
    "article_url": ARTICLE_URL, "article_date": "2026-04-27",
}


async def test_fetch_caaa_sow_row_builds_row_from_registry():
    rows = await _fetch_caaa_sow_row(PIG_INDUSTRY, client=FakeCaaaClient(_SOW_DATA))
    m = PIG_INDUSTRY.metric("sow_inventory")
    assert rows == [{
        "industry_key": "pig", "stock_id": 0, "metric_key": "sow_inventory",
        "source": "caaa", "source_tier": m.tier, "freq": "monthly",
        "period": date(2026, 3, 31), "value": 3904.0, "unit": m.unit,
        "extra": {"article_url": ARTICLE_URL, "mom_pct": -1.5},
    }]


async def test_fetch_caaa_sow_row_failure_paths_return_empty():
    assert await _fetch_caaa_sow_row(PIG_INDUSTRY, client=FakeCaaaClient(None)) == []
    boom = await _fetch_caaa_sow_row(PIG_INDUSTRY, client=FakeCaaaClient(RuntimeError("upstream")))
    assert boom == [], "client 抛穿也不得影响 ingest 其他指标"


def test_caaa_row_puts_sow_into_covered_purge():
    # ingest 接线：akshare 行 + caaa 行合并后的 covered 集合须含 sow_inventory，
    # 从而 covered-purge 清掉其 mock 演示行（registry 源优先级裁决的前提）
    akshare_rows = [{"metric_key": k} for k in ("hog_price", "corn_price", "lh_future_main")]
    caaa_row = {
        "metric_key": "sow_inventory", "source": "caaa",
        "period": date(2026, 3, 31), "value": 3904.0,
    }

    covered = {r["metric_key"] for r in [*akshare_rows, caaa_row]}
    assert "sow_inventory" in covered
    assert "sow_inventory" in _covered_purge_keys(covered)


def test_registry_registers_caaa_source_with_mock_last():
    assert PIG_INDUSTRY.metric("sow_inventory").sources == ["stats_gov", "caaa", "mock"]
