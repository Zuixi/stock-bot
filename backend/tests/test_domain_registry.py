"""结构门禁（G1）：域注册表、完整性模式、覆盖率口径必须自洽。"""

from __future__ import annotations

from app.services import reconciliation_service as rc


def test_every_domain_has_refetch_or_is_explicitly_non_backfillable() -> None:
    for domain in rc.DOMAINS:
        assert callable(getattr(rc, f"_refetch_{domain}", None)), (
            f"{domain} 在 DOMAINS 里但没有 _refetch_{domain} —— 这是「看着纳入、永不补漏」的假绿"
        )


def test_every_domain_declares_completeness_mode() -> None:
    assert set(rc.DOMAINS) == set(rc.DOMAIN_COMPLETENESS)
    for domain, mode in rc.DOMAIN_COMPLETENESS.items():
        assert mode in ("rows", "batch"), f"{domain} 的完整性模式未知：{mode}"


def test_non_backfillable_domains_are_explicit() -> None:
    """不可补域必须留在 INSPECT_DOMAINS（带原因），不能悄悄塞进 DOMAINS。"""
    assert set(rc.INSPECT_DOMAINS) & set(rc.DOMAINS) == set()
    assert "sector_moneyflow" in rc.INSPECT_DOMAINS  # A 类：上游无历史接口
    assert "sentiment_intraday" in rc.INSPECT_DOMAINS  # 盘中快照：跨日不可重建


def test_coverage_report_covers_all_coverage_sources() -> None:
    assert set(rc.COVERAGE_DOMAINS) == set(rc._COVERAGE_SOURCES)


def test_batch_domains_include_the_supplementary_ones() -> None:
    batch = {d for d, mode in rc.DOMAIN_COMPLETENESS.items() if mode == "batch"}
    for domain in (
        "limit_up_stock_daily",
        "dragon_tiger",
        "block_trade",
        "share_float",
        "repurchase",
        "northbound",
        "market_moneyflow",
        "global_index",
    ):
        assert domain in batch, f"{domain} 必须是 batch 完整性（空数据可能是合法事实）"
