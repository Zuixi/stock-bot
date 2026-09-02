"""Securities service: ETF/可转债日线 ingest（TuShare）+ 查询侧序列组装.

读取面与 industry_metric_service 同构：registry 下发代码清单（单一事实源），
ingest 幂等 upsert；查询侧按代码分组返回 latest + 最近 N 日序列，供工作台
行情表（表格 + 迷你走势）消费。外部源失败 log+跳过，绝不抛穿（AGENTS 约定）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.securities import CbDaily, FundEtfDaily
from app.repositories import securities_repo as repo
from app.schemas.industry import (
    IndustrySecuritiesOut,
    SecurityDailyPointOut,
    SecuritySeriesOut,
)
from app.services.industry_metric_service import UnknownIndustryError
from app.services.industry_registry import IndustryConfig, get_industry

if TYPE_CHECKING:
    from app.core.providers.tushare_client import TuShareClient

logger = logging.getLogger(__name__)

SEC_TYPE_ETF = "etf"
SEC_TYPE_CB = "cb"
SEC_TYPES = (SEC_TYPE_ETF, SEC_TYPE_CB)

# 调度器日增量窗口：工作日 17:10 只需覆盖最近若干交易日（幂等 upsert 兜底缺口）
SCHEDULED_BACKFILL_DAYS = 10


def _require_industry(industry_key: str) -> IndustryConfig:
    cfg = get_industry(industry_key)
    if cfg is None:
        raise UnknownIndustryError(f"Industry '{industry_key}' is not configured in registry")
    return cfg


def map_daily_rows(records) -> list[dict]:
    """TuShare fund_daily/cb_daily 原始行 → 落库行（纯函数，离线单测锁定）.

    接受任意 ts_code/trade_date/open/high/low/close/pre_close/vol/amount 映射
    （DataFrame 行或 dict 均可）；脏行（缺 ts_code/trade_date/close）跳过并计数日志。
    """
    rows: list[dict] = []
    skipped = 0
    for r in records:
        try:
            ts_code = str(r["ts_code"])
            trade_date = date.fromisoformat(
                f"{str(r['trade_date'])[:4]}-{str(r['trade_date'])[4:6]}-{str(r['trade_date'])[6:8]}"
            )
            close = float(r["close"])
            if close <= 0:
                raise ValueError(f"non-positive close {close}")
            rows.append({
                "ts_code": ts_code,
                "trade_date": trade_date,
                "open": float(r["open"]) if r.get("open") is not None else None,
                "high": float(r["high"]) if r.get("high") is not None else None,
                "low": float(r["low"]) if r.get("low") is not None else None,
                "close": close,
                "pre_close": float(r["pre_close"]) if r.get("pre_close") is not None else None,
                "volume": float(r["vol"]) if r.get("vol") is not None else None,
                "amount": float(r["amount"]) if r.get("amount") is not None else None,
            })
        except (KeyError, TypeError, ValueError) as exc:
            skipped += 1
            logger.warning("Skip malformed securities daily row (%s): %r", exc, r)
    return rows


def _point_out(row) -> SecurityDailyPointOut:
    return SecurityDailyPointOut(
        trade_date=row.trade_date,
        open=float(row.open) if row.open is not None else None,
        high=float(row.high) if row.high is not None else None,
        low=float(row.low) if row.low is not None else None,
        close=float(row.close) if row.close is not None else None,
        pre_close=float(row.pre_close) if row.pre_close is not None else None,
        volume=float(row.volume) if row.volume is not None else None,
        amount=float(row.amount) if row.amount is not None else None,
    )


def build_code_series(ts_code: str, name: str | None, rows: list) -> SecuritySeriesOut:
    """一代码的 ORM 行序列（升序）→ API 载荷：latest 一行 + 全序列 + 涨跌幅（纯函数）."""
    points = [_point_out(r) for r in rows]
    latest = points[-1] if points else None
    change_pct: float | None = None
    if latest is not None and latest.pre_close:
        change_pct = round((latest.close - latest.pre_close) / latest.pre_close * 100, 2)
    return SecuritySeriesOut(
        ts_code=ts_code, name=name, latest=latest, change_pct=change_pct, series=points
    )


async def _ingest_one(
    db: AsyncSession, fetch, upsert, sec_type: str, ts_code: str, start_date: str, end_date: str
) -> tuple[int, str | None]:
    """单代码 fetch→map→upsert；失败只跳过该代码，不牵连其他（外部源容错约定）.

    返回 (upsert 数, 错误摘要)；错误摘要进入任务 result，避免"逐项容错"把
    接线类 bug 吞成日志里的一条 WARNING 而任务仍报 completed（2026-09-03 实跑教训）。
    """
    try:
        df = await fetch(ts_code=ts_code, start_date=start_date, end_date=end_date)
        rows = map_daily_rows(df.to_dict("records"))
        return await upsert(db, rows), None
    except Exception as exc:
        logger.warning("%s %s fetch failed (skipped): %s", sec_type, ts_code, exc)
        return 0, f"{sec_type} {ts_code}: {exc}"


async def ingest_industry_securities(
    db: AsyncSession,
    industry_key: str = "pig",
    backfill_days: int = 365,
    client: TuShareClient | None = None,
) -> dict:
    """registry etf_codes/cb_codes → TuShare fund_daily/cb_daily 逐代码幂等回补."""
    cfg = _require_industry(industry_key)
    if client is None:
        from app.core.providers.tushare_client import get_tushare_client

        client = get_tushare_client()

    end = date.today()
    start = end - timedelta(days=backfill_days)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    etf_upserted = 0
    errors: list[str] = []
    for code in cfg.etf_codes:
        n, err = await _ingest_one(
            db, client.fetch_fund_daily, repo.upsert_fund_etf_daily,
            "fund_daily", code, start_s, end_s,
        )
        etf_upserted += n
        if err:
            errors.append(err)
    cb_upserted = 0
    for code in cfg.cb_codes:
        n, err = await _ingest_one(
            db, client.fetch_cb_daily, repo.upsert_cb_daily, "cb_daily", code, start_s, end_s
        )
        cb_upserted += n
        if err:
            errors.append(err)

    return {
        "industry_key": cfg.key,
        "backfill_days": backfill_days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "etf_codes": cfg.etf_codes,
        "cb_codes": cfg.cb_codes,
        "etf_upserted": etf_upserted,
        "cb_upserted": cb_upserted,
        "errors": errors,  # 空列表 = 全部代码成功；非空便于从任务 result 直读故障
    }


async def get_industry_securities(
    db: AsyncSession, industry_key: str, sec_type: str, limit: int = 90
) -> IndustrySecuritiesOut:
    """查询侧：按 registry 代码清单分组返回 latest + 最近 N 日序列（未拉取时 series 空）."""
    if sec_type not in SEC_TYPES:
        raise ValueError(f"Unknown securities type: {sec_type} (expected one of {SEC_TYPES})")
    cfg = _require_industry(industry_key)
    model = FundEtfDaily if sec_type == SEC_TYPE_ETF else CbDaily
    codes = cfg.etf_codes if sec_type == SEC_TYPE_ETF else cfg.cb_codes

    series = [
        build_code_series(
            code,
            cfg.securities_names.get(code),
            await repo.get_daily_series(db, model, code, limit=limit),
        )
        for code in codes
    ]
    return IndustrySecuritiesOut(type=sec_type, codes=series)
