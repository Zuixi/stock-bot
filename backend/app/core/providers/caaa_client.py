"""CAAA 猪业分会（pig.caaa.cn）客户端 — 能繁母猪存栏月度数据源.

行业动态栏目不定期转载五部委（农业农村部等）"全国生猪产品数据"月度文章，
正文段落含"能繁母猪存栏XXXX万头，环比下降/上升X%"（无表格，纯正则可解析）；
文章标题形如"2026年3月份全国生猪产品数据"，URL 形如 ``/html/pig_rd/pig_hydt/{Y}/{MMDD}/{id}.html``。

实测（2026-09-03）：年份目录列表（如 ``/pig_hydt/2026/``）403，但栏目列表页
``/html/pig_rd/pig_hydt/`` 200，按时间倒序列出文章（每页 8-9 条）——取第一个
标题含"生猪产品数据"的链接即为最新一期。容错策略：任何失败（列表被封/文章
缺失/正文改版）log warning 后返回 None，绝不向调用方抛异常（外部源不阻塞 ingest）。

Usage::

    from app.core.providers.caaa_client import get_caaa_client
    data = await get_caaa_client().fetch_latest_sow_inventory()
    # {"period": date(2026, 3, 31), "inventory_wan_tou": 3904.0,
    #  "mom_pct": -1.5, "article_url": "...", "article_date": "2026-04-27"}
"""

from __future__ import annotations

import calendar
import html as html_mod
import logging
import re
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://pig.caaa.cn"
_COLUMN_URL = f"{_BASE}/html/pig_rd/pig_hydt/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 15.0
# 列表页倒序 8-9 条/页，数据文章约隔月发布：扫 3 页足够覆盖近半年
_INDEX_PAGES = 3
_ARTICLE_KEYWORD = "生猪产品数据"

# 正文：能繁母猪存栏绝对数（万头）。指标说明里的"存栏正常保有量为3900万头"
# 因"存栏"后跟汉字不匹配，天然被排除；取首个命中（生产情况段落在正文最前）。
_SOW_RE = re.compile(r"能繁母猪存栏\s*(\d+(?:\.\d+)?)\s*万头")
# 环比方向词可为一字（降/升/涨/跌）或两字（下降/增长/回落…），? 单字符类匹配不了两字词
_MOM_RE = re.compile(
    r"环比(下降|降低|回落|下跌|减少|上升|增长|升高|上涨|增加|降|升|涨|跌)?\s*(\d+(?:\.\d+)?)\s*%"
)
_NEGATIVE_VERBS = {"下降", "降低", "回落", "下跌", "减少", "降", "跌"}
_POSITIVE_VERBS = {"上升", "增长", "升高", "上涨", "增加", "升", "涨"}
# 数据期：标题"2026年3月份全国生猪产品数据"→2026-03 月末（与正文"1季度末"一致）
_TITLE_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月份")
# 兜底：正文句内"2026年4月末/1季度末能繁母猪存栏…"→对应月末
_TEXT_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:末|底)")
_TEXT_QUARTER_RE = re.compile(r"(20\d{2})\s*年\s*([1-4])\s*季度(?:末|底)")
_PUBLISH_DATE_RE = re.compile(r"(?:原发表日期|发布日期|发布时间)[：:]\s*(20\d{2})-(\d{1,2})-(\d{1,2})")
_URL_DATE_RE = re.compile(r"/(20\d{2})/(\d{2})(\d{2})/\d+\.html")
# 栏目列表页条目：链接与标题（<a …><div class="news_title_fz …">标题</div>…）
_INDEX_ITEM_RE = re.compile(
    r'<a\s+href="([^"]*pig_hydt/\d+/\d+/\d+\.html)"[^>]*>.*?news_title_fz[^>]*>([^<]+)</div>',
    re.S,
)


def _strip_tags(raw: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", raw))


def _month_end(year: int, month: int) -> date | None:
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_sow_article(raw_html: str, url: str) -> dict | None:
    """解析"全国生猪产品数据"文章页 → 能繁母猪存栏行（纯函数，离线可测）.

    Returns: ``{"period": date(月末), "inventory_wan_tou": float, "mom_pct": float|None,
    "article_url": str, "article_date": str}``；正文无能繁数据或月份不可得 → None。
    """
    text = _strip_tags(raw_html)

    sow = _SOW_RE.search(text)
    if sow is None:
        return None
    inventory = float(sow.group(1))
    if not 0 < inventory < 100000:  # 万头量级护栏，防误抓乱码数字
        return None

    # 环比取同一句（到下一个句号）：能繁句后紧跟"环比…%，同比…%"
    tail = text[sow.end():]
    stop = tail.find("。")
    sentence = tail if stop < 0 else tail[:stop]
    mom_pct: float | None = None
    mom = _MOM_RE.search(sentence)
    if mom is not None:
        value = float(mom.group(2))
        mom_pct = -value if mom.group(1) in _NEGATIVE_VERBS else value

    # 数据期优先级：标题月份（数据所属月）> 句内"X年X月末" > URL 日期月份
    period: date | None = None
    title_match = _TITLE_MONTH_RE.search(text)
    if title_match is not None:
        period = _month_end(int(title_match.group(1)), int(title_match.group(2)))
    if period is None:
        context = text[max(0, sow.start() - 40): sow.start()]
        text_match = _TEXT_MONTH_RE.search(context)
        if text_match is not None:
            period = _month_end(int(text_match.group(1)), int(text_match.group(2)))
        else:
            quarter_match = _TEXT_QUARTER_RE.search(context)
            if quarter_match is not None:
                period = _month_end(int(quarter_match.group(1)), int(quarter_match.group(2)) * 3)
    if period is None:
        url_match = _URL_DATE_RE.search(url)
        if url_match is not None:
            period = _month_end(int(url_match.group(1)), int(url_match.group(2)))

    if period is None:
        return None

    # 发表日期：页面"原发表日期：YYYY-MM-DD" > URL /YYYY/MMDD/
    article_date = ""
    publish = _PUBLISH_DATE_RE.search(text)
    if publish is not None:
        article_date = f"{int(publish.group(1)):04d}-{int(publish.group(2)):02d}-{int(publish.group(3)):02d}"
    else:
        url_match = _URL_DATE_RE.search(url)
        if url_match is not None:
            y, m, d = (int(g) for g in url_match.groups())
            article_date = f"{y:04d}-{m:02d}-{d:02d}"

    return {
        "period": period,
        "inventory_wan_tou": inventory,
        "mom_pct": mom_pct,
        "article_url": url,
        "article_date": article_date,
    }


def find_latest_data_article(index_html: str) -> str | None:
    """栏目列表页 → 最新"生猪产品数据"文章链接（倒序列表，取首个命中；纯函数）."""
    for href, title in _INDEX_ITEM_RE.findall(index_html):
        if _ARTICLE_KEYWORD in title.strip():
            return href if href.startswith("http") else f"{_BASE}{href}"
    return None


class CaaaClient:
    """pig.caaa.cn 文章抓取（httpx 异步，UA + 15s 超时 + 跟随重定向）."""

    async def _get_text(self, url: str) -> str:
        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def fetch_latest_sow_inventory(self) -> dict | None:
        """最新一期能繁母猪存栏；任何失败 log warning 并返回 None（不抛穿）.

        ``settings.caaa_sow_article_url`` 非空时直接抓该文章（列表页改版的逃生通道）。
        """
        try:
            explicit = settings.caaa_sow_article_url.strip()
            if explicit:
                return parse_sow_article(await self._get_text(explicit), explicit)

            for page in range(1, _INDEX_PAGES + 1):
                index_url = _COLUMN_URL if page == 1 else f"{_COLUMN_URL}{page}.html"
                link = find_latest_data_article(await self._get_text(index_url))
                if link is not None:
                    article_html = await self._get_text(link)
                    result = parse_sow_article(article_html, link)
                    if result is not None:
                        return result
                    logger.warning("CAAA article %s parsed no sow data (skipped)", link)
        except Exception as exc:
            logger.warning("CAAA sow inventory fetch failed (skipped): %s", exc)
        return None


_default_client: CaaaClient | None = None


def get_caaa_client() -> CaaaClient:
    """Return the module-level singleton ``CaaaClient`` (lazy init)."""
    global _default_client
    if _default_client is None:
        _default_client = CaaaClient()
    return _default_client
