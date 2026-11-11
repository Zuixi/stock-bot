"""P5 摄取管道：行级校验 / 原因码 / 参数哈希（纯函数，不连库）。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.services.ingest_pipeline import (
    FieldRule,
    IngestSpec,
    RowRules,
    _params_hash,
    validate_rows,
)


def _rules() -> RowRules:
    return RowRules(
        required_columns=("ts_code", "close"),
        fields=(
            FieldRule("ts_code", required=True, dtype="str"),
            FieldRule("close", required=True, dtype="float", min=0.01),
            FieldRule("pct_chg", dtype="float", min=-44, max=44),
        ),
        unique_key=("ts_code",),
    )


def test_good_rows_all_accepted() -> None:
    df = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "close": 10.5, "pct_chg": 1.2},
            {"ts_code": "600000.SH", "close": 8.0, "pct_chg": -0.5},
        ]
    )
    outcome = validate_rows(df, _rules())
    assert outcome.schema_error is None
    assert outcome.rejected == []
    assert len(outcome.accepted) == 2


def test_missing_column_is_schema_drift_not_row_error() -> None:
    df = pd.DataFrame([{"ts_code": "000001.SZ"}])
    outcome = validate_rows(df, _rules())
    assert outcome.schema_error == "schema.missing_column:close"
    assert outcome.accepted.empty
    assert outcome.rejected == []


def test_required_null_and_range_and_duplicate() -> None:
    df = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "close": 10.0, "pct_chg": 1.0},
            {"ts_code": "000002.SZ", "close": None, "pct_chg": 1.0},
            {"ts_code": "000003.SZ", "close": -1.0, "pct_chg": 1.0},
            {"ts_code": "000004.SZ", "close": 5.0, "pct_chg": 99.0},
            {"ts_code": "000001.SZ", "close": 11.0, "pct_chg": 1.0},
        ]
    )
    outcome = validate_rows(df, _rules())
    assert len(outcome.accepted) == 1
    reasons = [item["reason_code"] for item in outcome.rejected]
    assert reasons == [
        "value.required_null",
        "value.out_of_range",
        "value.out_of_range",
        "key.duplicate",
    ]
    # row_key 供按实体检索；payload 必须可进 JSONB
    assert outcome.rejected[0]["row_key"] == "000002.SZ"
    assert set(outcome.rejected[0]["payload"]) >= {"ts_code", "close", "pct_chg"}


def test_params_hash_is_order_insensitive_and_stable() -> None:
    assert _params_hash({"b": 2, "a": 1}) == _params_hash({"a": "1", "b": "2"})


def test_spec_params_hash_changes_with_date() -> None:
    a = IngestSpec(
        domain="daily_quotes",
        business_date=date(2026, 11, 10),
        source="tushare",
        api_name="daily",
        params={"trade_date": "20261110"},
    )
    b = IngestSpec(
        domain="daily_quotes",
        business_date=date(2026, 11, 10),
        source="tushare",
        api_name="daily",
        params={"trade_date": "20261111"},
    )
    assert a.params_hash != b.params_hash
