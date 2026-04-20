"""TuShare-based stock universe ingest service.

Orchestrates TuShareClient -> DataSaver -> Repository to:
  1. Fetch stock data from TuShare Pro API
  2. Persist raw responses to local JSONL files
  3. Clean and upsert records into PostgreSQL
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.tushare_client import (
    EXCHANGE_TO_TUSHARE,
    TUSHARE_TO_EXCHANGE,
    TuShareClient,
    get_tushare_client,
)
from app.models.stock import Stock, StockHistory
from app.repositories import stock_repo
from app.services.data_saver import DataSaver

logger = logging.getLogger(__name__)

_STATUS_MAP = {"L": "上市", "D": "退市", "P": "暂停上市", "G": "过会未交易"}


def _to_builtin(value: Any) -> Any:
    """Convert numpy/pandas scalars to plain Python types."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return str(value)


def _parse_list_date(value: Any) -> datetime | None:
    """Parse TuShare YYYYMMDD date string into a date object."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _ts_code_to_exchange(ts_code: str) -> str:
    """Derive canonical exchange name from TuShare ts_code suffix."""
    suffix = ts_code.rsplit(".", 1)[-1].upper() if "." in ts_code else ""
    mapping = {"SZ": "Shenzen_Stocks", "SH": "Shanghai_Stocks", "BJ": "Beijing_Stocks"}
    return mapping.get(suffix, "")


def _ts_code_to_symbol(ts_code: str) -> str:
    """Extract numeric symbol from ts_code like '000001.SZ' -> '000001'."""
    return ts_code.split(".")[0] if "." in ts_code else ts_code


class TuShareIngestService:
    """High-level ingest operations backed by TuShare Pro API."""

    def __init__(
        self,
        client: TuShareClient | None = None,
        data_saver: DataSaver | None = None,
    ) -> None:
        self.client = client or get_tushare_client()
        self.saver = data_saver or DataSaver()

    # ------------------------------------------------------------------
    # Stock universe
    # ------------------------------------------------------------------

    async def ingest_stock_universe(
        self,
        db: AsyncSession,
        exchange: str,
        *,
        list_status: str = "L",
        enrich_company: bool = True,
    ) -> dict[str, int]:
        """Fetch stock list from TuShare, save raw data, and upsert into DB.

        Returns a summary dict with insert/skip counts.
        """
        ts_exchange = EXCHANGE_TO_TUSHARE.get(exchange, exchange)
        now = datetime.now(UTC)

        # 1. Fetch stock_basic
        df_basic = await self.client.fetch_stock_basic(exchange, list_status=list_status)
        if df_basic.empty:
            logger.warning("TuShare stock_basic returned empty for exchange=%s", exchange)
            return {"inserted": 0, "skipped": 0, "exchange": exchange}

        await self.saver.save_dataframe(
            "stock_basic", df_basic,
            {"exchange": ts_exchange, "list_status": list_status},
            exchange=ts_exchange,
        )

        # 2. Optionally fetch stock_company for enrichment
        company_map: dict[str, dict[str, Any]] = {}
        if enrich_company:
            try:
                df_company = await self.client.fetch_stock_company(exchange)
                if not df_company.empty:
                    await self.saver.save_dataframe(
                        "stock_company", df_company,
                        {"exchange": ts_exchange},
                        exchange=ts_exchange,
                    )
                    for row in df_company.to_dict("records"):
                        code = str(row.get("ts_code", "")).strip()
                        if code:
                            company_map[code] = row
            except Exception as exc:
                logger.warning("stock_company enrichment failed: %s", exc)

        # 3. Clean and upsert
        inserted = 0
        skipped = 0
        batch_size = 250

        for row in df_basic.to_dict("records"):
            ts_code = str(row.get("ts_code", "")).strip()
            symbol = _ts_code_to_symbol(ts_code)
            name = str(row.get("name", "")).strip()

            if not symbol or not name:
                skipped += 1
                continue

            row_exchange = _ts_code_to_exchange(ts_code) or exchange

            _tushare_direct_fields = {
                "ts_code", "symbol", "name", "area", "industry", "fullname",
                "enname", "cnspell", "market", "exchange", "curr_type",
                "list_status", "list_date", "delist_date", "is_hs",
                "act_name", "act_ent_type",
            }
            detail: dict[str, Any] = {
                k: _to_builtin(v) for k, v in row.items()
                if k not in _tushare_direct_fields
            }
            detail["ts_code"] = ts_code
            detail["source"] = "tushare::stock_basic"

            company_info = company_map.get(ts_code)
            if company_info:
                detail["company"] = {
                    k: _to_builtin(v) for k, v in company_info.items()
                }

            raw_list_status = _to_builtin(row.get("list_status")) or None
            stock = Stock(
                exchange=row_exchange,
                symbol=symbol,
                name=name,
                area=_to_builtin(row.get("area")) or None,
                industry=_to_builtin(row.get("industry")) or None,
                full_name=_to_builtin(row.get("fullname")) or None,
                enname=_to_builtin(row.get("enname")) or None,
                cnspell=_to_builtin(row.get("cnspell")) or None,
                market=_to_builtin(row.get("market")) or None,
                curr_type=_to_builtin(row.get("curr_type")) or None,
                list_status=raw_list_status,
                list_date=_parse_list_date(row.get("list_date")),
                delist_date=_parse_list_date(row.get("delist_date")),
                is_hs=_to_builtin(row.get("is_hs")) or None,
                act_name=_to_builtin(row.get("act_name")) or None,
                act_ent_type=_to_builtin(row.get("act_ent_type")) or None,
                # Legacy fields for backward compatibility
                category=row_exchange,
                csrc_code=None,
                csrc_desc=_to_builtin(row.get("industry")) or None,
                province=_to_builtin(row.get("area")) or None,
                status=_STATUS_MAP.get(str(raw_list_status or "").strip(), None),
                detail=detail if detail else None,
                asof=now,
            )

            raw_record = {k: _to_builtin(v) for k, v in row.items()}
            if company_info:
                raw_record["__company__"] = {
                    k: _to_builtin(v) for k, v in company_info.items()
                }

            history = StockHistory(
                exchange=stock.exchange,
                symbol=stock.symbol,
                name=stock.name,
                area=stock.area,
                industry=stock.industry,
                full_name=stock.full_name,
                enname=stock.enname,
                cnspell=stock.cnspell,
                market=stock.market,
                curr_type=stock.curr_type,
                list_status=stock.list_status,
                list_date=stock.list_date,
                delist_date=stock.delist_date,
                is_hs=stock.is_hs,
                act_name=stock.act_name,
                act_ent_type=stock.act_ent_type,
                category=stock.category,
                csrc_code=stock.csrc_code,
                csrc_desc=stock.csrc_desc,
                province=stock.province,
                status=stock.status,
                detail=stock.detail,
                source_url=f"tushare::stock_basic?exchange={ts_exchange}",
                asof=now,
                raw=raw_record,
            )

            await stock_repo.upsert_stock(db, stock)
            await stock_repo.insert_stock_history(db, history)
            inserted += 1

            if inserted % batch_size == 0:
                await db.commit()

        await db.commit()

        logger.info(
            "TuShare universe ingest done: exchange=%s inserted=%d skipped=%d",
            exchange, inserted, skipped,
        )
        return {
            "inserted": inserted,
            "skipped": skipped,
            "exchange": exchange,
            "source": "tushare",
        }

    # ------------------------------------------------------------------
    # Daily quotes
    # ------------------------------------------------------------------

    async def ingest_daily_quotes(
        self,
        db: AsyncSession,
        trade_date: str,
    ) -> dict[str, int]:
        """Fetch all-market daily OHLCV for a single trade date and persist to DB.

        Steps:
          1. Fetch daily data for the entire market via TuShare ``daily`` API.
          2. Save raw data to JSONL backup.
          3. Map ts_code -> stock_id via the ``stocks`` table.
          4. Batch upsert into ``daily_quotes``.
        """
        from datetime import datetime as _dt  # noqa: PLC0415

        from app.models.quote import DailyQuote  # noqa: PLC0415
        from app.repositories import quote_repo  # noqa: PLC0415

        df = await self.client.fetch_daily(trade_date=trade_date)
        if df.empty:
            logger.info("No daily data for trade_date=%s", trade_date)
            return {"saved": 0, "upserted": 0, "trade_date": trade_date}

        await self.saver.save_dataframe(
            "daily", df,
            {"trade_date": trade_date},
            exchange=f"all_{trade_date}",
        )

        stock_id_map = await self._build_stock_id_map(db)
        if not stock_id_map:
            logger.warning("No stocks in DB — run universe ingest first")
            return {"saved": len(df), "upserted": 0, "trade_date": trade_date}

        parsed_date = _dt.strptime(trade_date, "%Y%m%d").date()
        quotes: list[DailyQuote] = []
        skipped = 0

        for row in df.to_dict("records"):
            ts_code = str(row.get("ts_code", "")).strip()
            stock_id = stock_id_map.get(ts_code)
            if stock_id is None:
                skipped += 1
                continue

            close_val = _to_builtin(row.get("close"))
            if close_val is None:
                skipped += 1
                continue

            quotes.append(DailyQuote(
                stock_id=stock_id,
                trade_date=parsed_date,
                open=_to_builtin(row.get("open")),
                high=_to_builtin(row.get("high")),
                low=_to_builtin(row.get("low")),
                close=close_val,
                volume=_to_builtin(row.get("vol")),
                amount=_to_builtin(row.get("amount")),
                adj_factor=None,
                source="tushare:daily",
            ))

        upserted = 0
        batch_size = 500
        for i in range(0, len(quotes), batch_size):
            batch = quotes[i : i + batch_size]
            upserted += await quote_repo.upsert_quotes(db, batch)
            await db.commit()

        logger.info(
            "Daily ingest done: trade_date=%s fetched=%d upserted=%d skipped=%d",
            trade_date, len(df), upserted, skipped,
        )
        return {
            "saved": len(df),
            "upserted": upserted,
            "skipped": skipped,
            "trade_date": trade_date,
        }

    # ------------------------------------------------------------------
    # Index daily
    # ------------------------------------------------------------------

    async def ingest_index_daily(
        self,
        db: AsyncSession,
        ts_code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, int]:
        """Fetch index daily OHLCV for one ts_code and persist to DB.

        Uses TuShare ``index_daily`` API; upserts into ``index_dailies``.
        """
        from app.models.index_daily import IndexDaily  # noqa: PLC0415
        from app.repositories import index_repo  # noqa: PLC0415

        df = await self.client.fetch_index_daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df.empty:
            logger.info("No index_daily data for ts_code=%s", ts_code)
            return {"ts_code": ts_code, "upserted": 0}

        await self.saver.save_dataframe(
            "index_daily", df,
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            exchange=ts_code.replace(".", "_"),
        )

        rows: list[IndexDaily] = []
        for rec in df.to_dict("records"):
            close_val = _to_builtin(rec.get("close"))
            if close_val is None:
                continue
            td_str = str(rec.get("trade_date", "")).strip()
            if len(td_str) != 8:
                continue
            from datetime import datetime as _dt  # noqa: PLC0415
            parsed_date = _dt.strptime(td_str, "%Y%m%d").date()
            rows.append(IndexDaily(
                ts_code=ts_code,
                trade_date=parsed_date,
                open=_to_builtin(rec.get("open")),
                high=_to_builtin(rec.get("high")),
                low=_to_builtin(rec.get("low")),
                close=close_val,
                pre_close=_to_builtin(rec.get("pre_close")),
                volume=_to_builtin(rec.get("vol")),
                amount=_to_builtin(rec.get("amount")),
            ))

        upserted = 0
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            upserted += await index_repo.upsert_index_dailies(db, batch)
            await db.commit()

        logger.info(
            "Index daily ingest done: ts_code=%s fetched=%d upserted=%d",
            ts_code, len(df), upserted,
        )
        return {"ts_code": ts_code, "fetched": len(df), "upserted": upserted}

    async def _build_stock_id_map(self, db: AsyncSession) -> dict[str, int]:
        """Build a mapping from TuShare ts_code (e.g. '000001.SZ') to DB stock_id."""
        from sqlalchemy import select  # noqa: PLC0415

        from app.models.stock import Stock  # noqa: PLC0415

        result = await db.execute(select(Stock.id, Stock.exchange, Stock.symbol))
        mapping: dict[str, int] = {}
        suffix_map = {
            "Shanghai_Stocks": ".SH",
            "Shenzen_Stocks": ".SZ",
            "Beijing_Stocks": ".BJ",
        }
        for row in result:
            suffix = suffix_map.get(row.exchange, "")
            ts_code = f"{row.symbol}{suffix}"
            mapping[ts_code] = row.id
        return mapping
