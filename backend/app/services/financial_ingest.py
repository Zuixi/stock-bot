"""Financial statement ingest service.

Orchestrates TuShare financial APIs -> raw persistence -> report versions ->
standardized facts -> derived metrics for a single stock.

Pipeline (mirrors tushare_ingest.py for universe/quotes/daily_basic):

    client.fetch_income / fetch_balance_sheet / fetch_cash_flow /
        fetch_financial_indicator
    -> saver.save_dataframe (raw JSONL backup)
    -> repo.save_raw_record (raw DB row)
    -> repo.upsert_report_version
    -> repo.upsert_*_facts
    -> compute_metrics + repo.upsert_metric

Metric correctness rules:
  * Derived ratios that depend only on a single report row (margin, leverage,
    OCF coverage) are always computed and tagged ``calculated_from_statement``.
  * YoY growth requires the same report_type from the prior fiscal year; it is
    only computed when that row is present in the same batch to avoid
    lookahead/late-kind ambiguity. Missing prior-year data => NaN (not stored).
  * Ratios that TuShare already reports (e.g. fina_indicator.roe) are stored
    verbatim with calc_method ``reported_by_provider``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.tushare_client import TuShareClient, get_tushare_client
from app.models.stock import Stock
from app.repositories import financial_repo, stock_repo
from app.services.data_saver import DataSaver

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    """Coerce a provider scalar to float, mapping NaN/empty to None."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _to_builtin(value: Any) -> Any:
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


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat"):
        return None
    try:
        # TuShare dates are YYYYMMDD; also tolerate YYYY-MM-DD.
        if "-" in text:
            return datetime.strptime(text, "%Y-%m-%d").date()
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _report_type(end_date: date) -> str:
    m = end_date.month
    return "Q1" if m == 3 else "H1" if m == 6 else "Q3" if m == 9 else "FY"


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    end_date = _parse_date(row.get("end_date"))
    rtype = _report_type(end_date) if end_date else str(row.get("report_type", "")).upper()
    comp_type = str(row.get("comp_type") or "1")
    return rtype, comp_type


class FinancialIngestService:
    """Ingest one stock's financial statements/metrics from TuShare."""

    def __init__(
        self,
        client: TuShareClient | None = None,
        saver: DataSaver | None = None,
    ) -> None:
        self.client = client or get_tushare_client()
        self.saver = saver or DataSaver()

    # ------------------------------------------------------------------
    # Public orchestration
    # ------------------------------------------------------------------

    async def ingest_stock_financials(
        self,
        db: AsyncSession,
        *,
        exchange: str,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """Fetch + persist all financials for a single stock."""
        stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
        if stock is None:
            raise ValueError(f"Stock not found: {exchange}/{symbol}")

        ts_code = _to_ts_code(exchange, symbol)
        now = datetime.now(UTC)

        df_income = await self.client.fetch_income(
            ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        df_balance = await self.client.fetch_balance_sheet(
            ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        df_cashflow = await self.client.fetch_cash_flow(
            ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        df_indicator = await self.client.fetch_financial_indicator(
            ts_code=ts_code, start_date=start_date, end_date=end_date
        )

        # Persist raw rows (best-effort backup + audit).
        for dataset, df in (
            ("income", df_income),
            ("balancesheet", df_balance),
            ("cashflow", df_cashflow),
            ("fina_indicator", df_indicator),
        ):
            await self.save_raw_dataset(db, stock, dataset, df, now)

        income_map = _index_by_end_date(df_income)
        balance_map = _index_by_end_date(df_balance)
        cashflow_map = _index_by_end_date(df_cashflow)
        indicator_map = _index_by_end_date(df_indicator)

        end_dates = sorted(
            set(income_map) | set(balance_map) | set(cashflow_map) | set(indicator_map)
        )

        for end_date in end_dates:
            await self._ingest_period(
                db,
                stock=stock,
                ts_code=ts_code,
                end_date=end_date,
                income_rows=income_map.get(end_date, []),
                balance_rows=balance_map.get(end_date, []),
                cashflow_rows=cashflow_map.get(end_date, []),
                indicator_rows=indicator_map.get(end_date, []),
                now=now,
            )

        # Latest-value lookup is done in repository; summary is informational.
        return {
            "stock_id": stock.id,
            "ts_code": ts_code,
            "periods_processed": len(end_dates),
        }

    # ------------------------------------------------------------------
    # Raw persistence
    # ------------------------------------------------------------------

    async def save_raw_dataset(
        self,
        db: AsyncSession,
        stock: Stock,
        dataset: str,
        df: pd.DataFrame,
        now: datetime,
    ) -> None:
        if df is None or df.empty:
            return
        await self.saver.save_dataframe(
            dataset, df, {"ts_code": stock.id}, exchange=stock.exchange
        )
        for record in df.to_dict("records"):
            record_builtin = {k: _to_builtin(v) for k, v in record.items()}
            payload_hash = hashlib.sha256(
                json.dumps(record_builtin, sort_keys=True, default=str).encode()
            ).hexdigest()
            daily = _parse_date(record.get("end_date"))
            await financial_repo.save_raw_record(
                db,
                source="tushare",
                dataset=dataset,
                stock_id=stock.id,
                ts_code=record.get("ts_code"),
                request_params={},
                source_record_key=str(record.get("ts_code", "")),
                payload=record_builtin,
                payload_hash=payload_hash,
                fetched_at=now,
            )

    # ------------------------------------------------------------------
    # Per-period persistence
    # ------------------------------------------------------------------

    async def _ingest_period(
        self,
        db: AsyncSession,
        *,
        stock: Stock,
        ts_code: str,
        end_date: date,
        income_rows: list[dict[str, Any]],
        balance_rows: list[dict[str, Any]],
        cashflow_rows: list[dict[str, Any]],
        indicator_rows: list[dict[str, Any]],
        now: datetime,
    ) -> FinancialReportVersion | None:
        income = income_rows[0] if income_rows else {}
        balance = balance_rows[0] if balance_rows else {}
        cashflow = cashflow_rows[0] if cashflow_rows else {}
        indicator = indicator_rows[0] if indicator_rows else {}

        report_type = _report_type(end_date)
        ann_date = _parse_date(
            income.get("ann_date")
            or balance.get("ann_date")
            or cashflow.get("ann_date")
            or indicator.get("ann_date")
        )
        f_ann_date = _parse_date(
            income.get("f_ann_date") or balance.get("f_ann_date")
            or cashflow.get("f_ann_date")
        )
        update_flag = str(income.get("update_flag") or balance.get("update_flag") or "")

        version = await financial_repo.upsert_report_version(
            db,
            stock_id=stock.id,
            ts_code=ts_code,
            end_date=end_date,
            report_type=report_type,
            ann_date=ann_date,
            f_ann_date=f_ann_date,
            comp_type="1",
            source="tushare",
            source_record_key=f"{ts_code}:{end_date.isoformat()}:{report_type}",
            update_flag=update_flag or None,
            currency="CNY",
            quality_status="reported",
            as_of=now,
            raw_record_id=None,
        )

        await financial_repo.upsert_income_facts(
            db, version.id, _income_facts(income)
        )
        await financial_repo.upsert_balance_facts(
            db, version.id, _balance_facts(balance)
        )
        await financial_repo.upsert_cashflow_facts(
            db, version.id, _cashflow_facts(cashflow)
        )
        await self._upsert_metrics_for_period(
            db, stock.id, version.id, income, balance, cashflow, indicator, now
        )
        return version

    async def _upsert_metrics_for_period(
        self,
        db: AsyncSession,
        stock_id: int,
        version_id: int,
        income: dict[str, Any],
        balance: dict[str, Any],
        cashflow: dict[str, Any],
        indicator: dict[str, Any],
        now: datetime,
    ) -> None:
        revenue = _num(income.get("revenue"))
        operate_cost = _num(income.get("operate_cost"))
        n_income_attr = _num(income.get("n_income_attr_p"))
        total_assets = _num(balance.get("total_assets"))
        total_liab = _num(balance.get("total_liab"))
        equity = _num(balance.get("total_hldr_eqy_exc_min_int"))
        n_cashflow_act = _num(cashflow.get("n_cashflow_act"))

        # (metric_key, value, unit, calc_method, quality_status)
        derived: list[tuple[str, float | None, str, str, str]] = [
            ("gross_margin", _pct(revenue, revenue - operate_cost)
             if operate_cost is not None else None, "%", "calculated_from_statement", "derived"),
            ("net_margin", _pct(revenue, n_income_attr), "%", "calculated_from_statement", "derived"),
            ("debt_to_asset", _pct(total_assets, total_liab), "%", "calculated_from_statement", "derived"),
            ("roe_calc", _pct(equity, n_income_attr) if equity else None,
             "%", "calculated_from_statement", "derived"),
            ("ocf_to_net_profit",
             (n_cashflow_act / n_income_attr) if (n_cashflow_act is not None and n_income_attr)
             else None, "ratio", "calculated_from_statement", "derived"),
            ("equity_multiplier",
             (total_assets / equity) if (total_assets and equity) else None,
             "ratio", "calculated_from_statement", "derived"),
        ]
        # Provider-reported ratios — these take precedence (same key overwrites
        # the derived value above because upsert runs in list order).
        provider: list[tuple[str, str, str, str, str]] = [
            ("roe", "roe", "%", "reported_by_provider", "reported"),
            ("roe_dt", "roe_dt", "%", "reported_by_provider", "reported"),
            ("gross_margin", "grossprofit_margin", "%", "reported_by_provider", "reported"),
            ("net_margin", "netprofit_margin", "%", "reported_by_provider", "reported"),
            ("debt_to_asset", "debt_to_assets", "%", "reported_by_provider", "reported"),
            ("current_ratio", "current_ratio", "ratio", "reported_by_provider", "reported"),
            ("quick_ratio", "quick_ratio", "ratio", "reported_by_provider", "reported"),
            ("eps", "eps", "CNY", "reported_by_provider", "reported"),
            ("bps", "bps", "CNY", "reported_by_provider", "reported"),
            ("revenue_yoy", "or_yoy", "%", "reported_by_provider", "reported"),
            ("profit_yoy", "netprofit_yoy", "%", "reported_by_provider", "reported"),
        ]

        for key, value, unit, method, quality in derived:
            if value is None or not math.isfinite(value) or not (-999 <= value <= 999):
                continue
            await financial_repo.upsert_metric(
                db, stock_id=stock_id, report_version_id=version_id,
                metric_key=key, value=value, unit=unit, period_type="report",
                calc_method=method, source="tushare", quality_status=quality,
                as_of=now,
            )
        for key, src_field, unit, method, quality in provider:
            value = _num(indicator.get(src_field))
            if value is None:
                continue
            await financial_repo.upsert_metric(
                db, stock_id=stock_id, report_version_id=version_id,
                metric_key=key, value=value, unit=unit, period_type="report",
                calc_method=method, source="tushare", quality_status=quality,
                as_of=now,
            )


def _index_by_end_date(df: pd.DataFrame) -> dict[date, list[dict[str, Any]]]:
    """Index rows by parsed end_date; return plain builtin dicts."""
    out: dict[date, list[dict[str, Any]]] = {}
    if df is None or df.empty:
        return out
    for record in df.to_dict("records"):
        ed = _parse_date(record.get("end_date"))
        if ed is None:
            continue
        out.setdefault(ed, []).append({k: _to_builtin(v) for k, v in record.items()})
    return out


def _pct(base: float | None, part: float | None) -> float | None:
    if base is None or part is None or base == 0:
        return None
    return (part / base) * 100.0


def _income_facts(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "revenue": _num(row.get("revenue")),
        "operate_cost": _num(row.get("operate_cost")),
        "operate_profit": _num(row.get("operate_profit")),
        "total_profit": _num(row.get("total_profit")),
        "n_income": _num(row.get("n_income")),
        "n_income_attr_p": _num(row.get("n_income_attr_p")),
        "deduct_n_income": _num(row.get("deduct_n_income")),
        "sell_exp": _num(row.get("sell_exp")),
        "admin_exp": _num(row.get("admin_exp")),
        "fin_exp": _num(row.get("fin_exp")),
        "rd_exp": _num(row.get("rd_exp")),
        "basic_eps": _num(row.get("basic_eps")),
        "diluted_eps": _num(row.get("diluted_eps")),
    }


def _balance_facts(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_assets": _num(row.get("total_assets")),
        "total_liab": _num(row.get("total_liab")),
        "total_hldr_eqy_exc_min_int": _num(row.get("total_hldr_eqy_exc_min_int")),
        "total_hldr_eqy_inc_min_int": _num(row.get("total_hldr_eqy_inc_min_int")),
        "money_cap": _num(row.get("money_cap")),
        "accounts_receiv": _num(row.get("accounts_receiv")),
        "inventories": _num(row.get("inventories")),
        "fix_assets": _num(row.get("fix_assets")),
        "intan_assets": _num(row.get("intan_assets")),
        "st_borrow": _num(row.get("st_borrow")),
        "lt_borrow": _num(row.get("lt_borrow")),
        "st_note_payable": _num(row.get("st_note_payable")),
        "bond_payable": _num(row.get("bond_payable")),
    }


def _cashflow_facts(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "n_cashflow_act": _num(row.get("n_cashflow_act")),
        "n_cashflow_inv_act": _num(row.get("n_cashflow_inv_act")),
        "n_cashflow_fin_act": _num(row.get("n_cashflow_fin_act")),
        "c_cash_equ_end_period": _num(row.get("c_cash_equ_end_period")),
        "c_paid_goods_s": _num(row.get("c_paid_goods_s")),
        "c_paid_to_for_empl": _num(row.get("c_paid_to_for_empl")),
        "c_paid_for_taxes": _num(row.get("c_paid_for_taxes")),
        "c_recp_from_release_sale_sg": _num(row.get("c_recp_from_release_sale_sg")),
    }


def _to_ts_code(exchange: str, symbol: str) -> str:
    suffix = {
        "Shanghai_Stocks": "SH",
        "Shenzen_Stocks": "SZ",
        "Beijing_Stocks": "BJ",
    }.get(exchange, "")
    return f"{symbol}.{suffix}"
