"""Unified metric read surface (spec §S1.2) — one home for every indicator.

`industry_metrics` 是统一指标表（ADR 0013）：行业/情绪/宏观/策略因子都落在这一张表，
scope 用 `industry_key`（语义已泛化为通用 scope 键）区分。本路由是该表的**通用读面**，
与行业工作台的 `/api/v1/industries/{key}/metrics/*` **共用同一 service 层**：

- `GET /api/v1/metrics/latest?scope_keys=&metric_keys=`  每指标最新值 + 四徽章
- `GET /api/v1/metrics/series?scope_key=&metric_key=&range=`  升序序列（Redis 缓存）

行业端点服务行业 scope（含 spark/预警带，工作台用）；本路由服务"跨 scope 一次问多个
指标"（情绪/宏观/策略消费），不为新指标新建表、也不复制一套读逻辑。

错误面：未登记 `metric_key` / `scope_key` → **404**（registry 即白名单，是"不存在"）；
非法 `range` / 空 `metric_keys` / naive `published_at` → **422**（是"参数不合法"，
不是"资源不存在"，故不用 not_found_response）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CacheDep, DbDep, require_permissions
from app.core.exceptions import not_found_response
from app.schemas.industry import MetricLatestOut, MetricSeriesOut
from app.services import industry_metric_service as service

# 读面与行业工作台同权限口径（ADR 0008：`research:read`，router 级一处覆盖全部端点）。
router = APIRouter(dependencies=[require_permissions("research:read")])

SERIES_CACHE_KEY = "metrics:series:{scope}:{metric}:{range}"


def _series_cache_key(scope_key: str, metric_key: str, range_text: str) -> str:
    """缓存键用**规范化后的 range 文本**（`1Y` → `1y`）。

    键里放 range 本身而不是行数上界：range 现在是时间窗，窗口不同则结果不同，
    不能靠 limit 折叠（`1y` 与 `366d` 的 limit 相同但窗口相同、文本不同 —— 会被
    规范化成同一把键，这是有意的等价合并，而不是把两个不同窗口混为一谈）。
    """
    return SERIES_CACHE_KEY.format(scope=scope_key, metric=metric_key, range=range_text)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


@router.get("/latest", response_model=list[MetricLatestOut])
async def get_latest_metrics(
    db: DbDep,
    scope_keys: list[str] | None = Query(
        None, description="scope 键（现登记 'pig'/'broiler'；后续情绪/宏观为 'market'）；省略=全部"
    ),
    metric_keys: list[str] | None = Query(
        None, description="metric_key 白名单；省略=已登记 scope 的全部指标。未登记即 404"
    ),
) -> list[MetricLatestOut]:
    """每指标最新值 + `source/as_of/freq/caliber_version` 四徽章（registry 驱动）。

    `as_of_source` 标注 `as_of` 是 `published_at`（真 PIT）还是回退的 `period`。
    """
    if metric_keys is not None and not metric_keys:
        raise _unprocessable("metric_keys 不得为空列表：省略该参数才表示'全部已登记指标'")
    if scope_keys is not None and not scope_keys:
        raise _unprocessable("scope_keys 不得为空列表：省略该参数才表示'全部 scope'")
    try:
        return await service.get_latest_metrics_across_scopes(
            db, scope_keys=scope_keys, metric_keys=metric_keys
        )
    except service.UnknownIndustryError as exc:
        raise not_found_response("Scope", ", ".join(scope_keys or [])) from exc
    except service.UnknownMetricError as exc:
        raise not_found_response("Metric", ", ".join(metric_keys or [])) from exc


@router.get("/series", response_model=MetricSeriesOut)
async def get_series(
    db: DbDep,
    cache: CacheDep,
    scope_key: str = Query(description="scope 键（须已登记）"),
    metric_key: str = Query(description="metric_key 白名单；未登记即 404"),
    range: str = Query(
        default=service.SERIES_DEFAULT_RANGE, description="真实时间窗：30d / 6w / 3m / 1y / all"
    ),
) -> MetricSeriesOut:
    """**窗口内**升序序列（单一源 + 四徽章），Redis 缓存。

    `range` 是时间窗（`period >= today - window`），响应原样回传 `range` 与 `period_from`，
    窗口不因缓存而复用错配（键含 range 文本）。
    """
    parsed = service.parse_series_range(range)
    if parsed is None:
        raise _unprocessable(f"非法 range: {range!r}；应形如 30d / 6w / 3m / 1y 或 all")
    key = _series_cache_key(scope_key, metric_key, parsed.text)
    try:
        cached = await cache.get(key)
        if cached is not None:
            return MetricSeriesOut(**cached)
        out = await service.get_series(db, scope_key, metric_key, parsed.text)
    except service.UnknownIndustryError as exc:
        raise not_found_response("Scope", scope_key) from exc
    except service.UnknownMetricError as exc:
        raise not_found_response("Metric", metric_key) from exc
    await cache.set(key, out.model_dump(mode="json"), ttl=service.SERIES_CACHE_TTL)
    return out
