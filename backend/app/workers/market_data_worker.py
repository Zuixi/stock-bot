"""Market-data ingestion worker (manual trigger via /tasks/fetch-market-data)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, get_args

from app.core.database import async_session_factory
from app.schemas.task import MarketDataJobType
from app.workers.base_worker import BaseWorker

_KNOWN_TYPES: frozenset[str] = frozenset(get_args(MarketDataJobType))


def _opt_date(v: Any) -> date | None:
    from app.services.market_data_service import _d

    return _d(str(v)) if v else None


def _opt_legs(v: Any) -> list[str] | None:
    """`legs` 参数：`None`/空 ⇒ 全跑；字符串（逗号分隔）或列表 ⇒ 只跑这些腿.

    腿名的合法性由 `derived_metrics_service.converge` 校验（未知腿**响亮失败**）——
    这里不另抄一份白名单，避免两处漂移。
    """
    if v is None or v == "":
        return None
    if isinstance(v, str):
        legs = [part.strip() for part in v.split(",") if part.strip()]
        return legs or None
    if isinstance(v, (list, tuple)):
        legs = [str(part).strip() for part in v if str(part).strip()]
        return legs or None
    raise ValueError(f"legs 参数类型不支持：{type(v).__name__}")


def _opt_target(v: Any) -> date | None:
    """`market_metrics` 的 `target` 参数：接受 `YYYY-MM-DD`（ISO）或 `YYYYMMDD`.

    两种都收的理由：本端点是**运维补跑口** —— 任务参数历来写 `YYYYMMDD`
    （`trade_date`），而"目标交易日"在排障语境里天然写成 ISO；只认一种会让人在
    422 与静默失败之间来回试。非法值**响亮失败**（不静默当"没给"）。
    """
    if v is None or v == "":
        return None
    raw = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"target 无法解析为日期（要 YYYY-MM-DD 或 YYYYMMDD）：{raw!r}")


async def _run(job: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import (
        announcement_service,
        concept_service,
        job_alert_service,
        limit_up_service,
        market_data_service,
        market_day_service,
    )

    # 各分支返回结构不同（计数 dict / 维度统计 dict / {"skipped": …}）→ 显式声明
    result: dict[str, Any]
    shortfall_alert: tuple[str, list[str]] | None = None

    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await market_data_service.ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await market_data_service.backfill_global_index_history(
                db, years=int(params.get("years", 2))
            )
        elif job == "sector_moneyflow":
            result = await market_data_service.ingest_sector_moneyflow(db)
        elif job == "board_snapshot_eod":
            # 双轨制：与 `board_snapshot_eod_job`（交易日 15:05）调同一个 service 方法 ——
            # **分页全量**收盘捕获（capture_kind='eod_full'；A 类数据当天不落就永久丢失）。
            result = await market_data_service.ingest_sector_moneyflow_eod_full(db)
            shortfall_alert = ("board_snapshot_eod", market_data_service.eod_alert_reasons(result))
        elif job == "board_snapshot_rewrite":
            # 双轨制：与 `board_snapshot_rewrite_job`（交易日 15:35）同方法 —— 当日**没有
            # eod_full 行**才补做收盘全量（容器重启错过 15:05 的兜底）；否则 {"skipped": true}。
            result = await market_data_service.ingest_sector_moneyflow_if_missing(db)
            shortfall_alert = (
                "board_snapshot_rewrite",
                market_data_service.eod_alert_reasons(result),
            )
        elif job == "northbound":
            result = await market_data_service.ingest_northbound(
                db, days=int(params.get("days", 30))
            )
        elif job == "dragon_tiger":
            result = await market_data_service.ingest_dragon_tiger(
                db, trade_date=_opt_date(params.get("trade_date"))
            )
        elif job == "market_moneyflow":
            result = await market_data_service.ingest_market_moneyflow_daily(
                db, days=int(params.get("days", 10))
            )
        elif job == "block_trades":
            result = await market_data_service.ingest_block_trades(
                db, trade_date=_opt_date(params.get("trade_date"))
            )
        elif job == "share_floats":
            result = await market_data_service.ingest_share_floats(
                db, days=int(params.get("days", 7))
            )
        elif job == "repurchases":
            result = await market_data_service.ingest_repurchases(
                db, days=int(params.get("days", 7))
            )
        elif job == "announcements":
            result = await announcement_service.ingest_announcements(
                db, days=int(params.get("days", 3))
            )
        elif job == "price_limits":
            result = await market_data_service.ingest_stock_price_limits(
                db,
                trade_date=_opt_date(params.get("trade_date")),
                window_days=int(params.get("window_days", 20)),
            )
        elif job == "sentiment_daily":
            # 盘后落库不需要缓存（cache=None）：避免命中 Redis JSON 后 as_of 变 str。
            result = await limit_up_service.persist_snapshot(db, None)
        elif job == "limit_up_stock_facts":
            # 双轨制：与 `limit_up_stock_facts_job` 调同一个 service 编排（落库 → commit →
            # 东财 EOD 增强）。trade_date 可选：指定时对该日补跑（涨停池端点保留约 14 个
            # 交易日（2026-10-12 实测：09-14 有数据、09-11 为 0）→ 17:20 那次外呼失败后
            # 可用同一条代码路径回补），不指定则取最新完整交易日（与定时轨道同参）。
            day = _opt_date(params.get("trade_date"))
            if day is None:
                md = await market_day_service.resolve_latest_complete_day(db)
                if md is None:
                    return {"status": "failed", "error": "no quotes; cannot resolve trade date"}
                day = md.day
            result = await limit_up_service.persist_and_enrich_stock_facts(db, day)
            reason = result.get("enrich_reason")
            if reason:
                # 与定时轨道同款可见性：A 类装饰丢失必须进 `job:failures`（不只留在
                # task.result 里）——手动补跑路径不该比定时路径更瞎。
                await job_alert_service.record_job_failure(
                    "limit_up_stock_facts", f"EOD enrichment {reason} (trade_date={day})"
                )
        elif job == "concept_members":
            result = await concept_service.ingest_concept_members(db)
        elif job == "market_metrics":
            # 双轨制：与 `reconcile_market_data_job` 的**后置收敛**调同一个 service
            # （`derived_metrics_service.converge`）—— 手动补跑与定时轨道零口径分叉。
            # 该 service **自持 session 与 advisory lock**，故这里不传 db。
            #
            # ⚠️ 结果**嵌一层**再返回：收敛自身的 payload 也有个 `status`（ok / gated /
            # locked / converged_with_gaps），平铺会被本函数末尾的
            # `{"status": "completed", **result}` 覆盖 —— "任务生命周期状态"与"收敛状态"
            # 是两件事，撞键会让"任务成功但收敛被闸门挡掉"在任务列表里看起来一样。
            from app.services import derived_metrics_service  # noqa: PLC0415

            default_window = derived_metrics_service.CONVERGE_WINDOW_TRADING_DAYS
            result = {
                "converge": await derived_metrics_service.converge(
                    target=_opt_target(params.get("target")),
                    legs=_opt_legs(params.get("legs")),
                    window=int(params.get("window") or default_window),
                    alert_job_id="market_metrics_refresh",
                )
            }
        elif job == "reconcile":
            from app.services import reconciliation_service  # noqa: PLC0415

            # 手动全量对账：幂等，无缺口时零 TuShare 请求
            result = await reconciliation_service.reconcile_market_data(db)
        else:
            # Defensive: unreachable via process() — the type is validated there.
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        await db.commit()
    if shortfall_alert is not None and shortfall_alert[1]:
        # 与定时轨道同款可见性（Task 1 的教训：手动路径不该比定时路径更瞎）：全量捕获
        # 覆盖不足 = 当日"全量"其实是子集，读表闸门却照开 → 必须进 `job:failures`。
        await job_alert_service.record_job_failure(
            shortfall_alert[0], "; ".join(shortfall_alert[1])
        )
    return {"status": "completed", "type": job, **result}


class MarketDataWorker(BaseWorker):
    """市场数据面采集任务（全球指数/资金流/北向/龙虎榜/大宗/解禁/回购/公告）。"""

    queue_key = "market_data.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        """Execute one ingest job; service exceptions propagate (BaseWorker marks the
        task failed). Expected payload keys (top-level or nested in "params"):
        global_index_daily/sector_moneyflow/concept_members — none;
        board_snapshot_eod — none（板块快照收盘**全量**捕获，与 15:05 job 同方法）;
        board_snapshot_rewrite — none（当日无 eod_full 行才补做全量，与 15:35 job 同方法）;
        backfill_global_index — years(=2);
        northbound/share_floats/repurchases — days(=30/7/7); dragon_tiger/block_trades —
        trade_date(yyyymmdd, optional); announcements — days(=3); limit_up_stock_facts —
        trade_date(可选，缺省取最新完整交易日；个股涨停事实落库 + 东财 EOD 增强).
        Unknown types return a failed dict without touching the DB.
        """
        job = str(payload.get("type") or "")
        if job not in _KNOWN_TYPES:
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        params = payload.get("params") or {}
        return await _run(job, {**payload, **params})
