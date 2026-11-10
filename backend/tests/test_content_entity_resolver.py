"""实体解析器（纯函数，不连库）的不变量。

判据来自 ``plans/2026-11-11-wave2-content-foundation.md`` Task 2 与
``docs/specs/S3-content.md`` §S3.1：实体关联只写 ``content_entities``，
``entity_type='stock'``、``entity_id`` 为 6 位代码（示例 ``002956``）。
"""

from __future__ import annotations

from app.services.content_ingest.entity_resolver import resolve_stock_entities

NAMES = {"西麦食品": "002956", "贵州茅台": "600519"}


def test_resolve_by_code():
    assert resolve_stock_entities("西麦食品(002956)发布三季报", NAMES) == [("stock", "002956")]


def test_resolve_by_name():
    assert resolve_stock_entities("贵州茅台发布公告", {"贵州茅台": "600519"}) == [
        ("stock", "600519")
    ]


def test_ignore_non_ashare_numbers():
    assert resolve_stock_entities("合同金额 123456 万元", NAMES) == []


def test_dedupe_code_and_name():
    assert resolve_stock_entities("002956 西麦食品", NAMES) == [("stock", "002956")]


def test_longest_name_first_blocks_substring():
    assert resolve_stock_entities(
        "平安银行", {"平安": "000001", "平安银行": "000001"}
    ) == [("stock", "000001")]


def test_empty_text():
    assert resolve_stock_entities("", NAMES) == []
