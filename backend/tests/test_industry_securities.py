"""纯单元测试：P5 行情面 — registry 标的配置 / TuShare 行映射 / 序列组装 / 冲突列口径."""

from types import SimpleNamespace

from app.models.securities import CbDaily, FundEtfDaily
from app.repositories.securities_repo import SECURITIES_CONFLICT_COLS
from app.services.industry_registry import PIG_INDUSTRY
from app.services.securities_service import build_code_series, map_daily_rows

# ── registry 标的配置 ────────────────────────────────────────────────


def test_pig_etf_codes_non_empty_and_named():
    # 国泰中证畜牧养殖ETF（2026-09-03 fund_basic 核验）
    assert "159865.SZ" in PIG_INDUSTRY.etf_codes
    for code in PIG_INDUSTRY.etf_codes:
        assert PIG_INDUSTRY.securities_names.get(code), f"ETF {code} 缺少展示名"


def test_pig_cb_codes_active_with_issuer_names():
    # cb_basic 按 9 只成分股正股过滤、仅保留在市转债（delist_date 为空）
    assert set(PIG_INDUSTRY.cb_codes) <= {"127045.SZ", "123107.SZ", "127049.SZ"}
    for code in PIG_INDUSTRY.cb_codes:
        assert PIG_INDUSTRY.securities_names.get(code), f"转债 {code} 缺少展示名"
    # 已退市的希望转债(127015.SZ, 2026-01 摘牌)/正邦转债(128114.SZ)不得混入
    assert "127015.SZ" not in PIG_INDUSTRY.cb_codes
    assert "128114.SZ" not in PIG_INDUSTRY.cb_codes


def test_securities_names_covers_exactly_registered_codes():
    registered = set(PIG_INDUSTRY.etf_codes) | set(PIG_INDUSTRY.cb_codes)
    assert registered  # pig 至少有 1 只 ETF
    assert set(PIG_INDUSTRY.securities_names) == registered


# ── TuShare 原始行 → 落库行（map_daily_rows 纯函数） ─────────────────


def _raw(**overrides) -> dict:
    """模拟 DataFrame.to_dict('records') 的行（TuShare 字段名）。"""
    row = {
        "ts_code": "159865.SZ", "trade_date": "20260901",
        "open": 0.548, "high": 0.561, "low": 0.547, "close": 0.557,
        "pre_close": 0.548, "vol": 5527101.90, "amount": 307480.61,
    }
    row.update(overrides)
    return row


def test_map_daily_rows_parses_tushare_fields():
    rows = map_daily_rows([_raw()])
    assert len(rows) == 1
    r = rows[0]
    assert r["ts_code"] == "159865.SZ"
    assert str(r["trade_date"]) == "2026-09-01"
    assert r["close"] == 0.557 and r["pre_close"] == 0.548
    assert r["volume"] == 5527101.90 and r["amount"] == 307480.61  # vol → volume 字段改名


def test_map_daily_rows_skips_malformed_and_nonpositive():
    rows = map_daily_rows([
        _raw(ts_code="127045.SZ", open=119.5, high=120.4, low=119.5, close=120.05,
             pre_close=119.77, vol=136443.9, amount=16373.48),
        _raw(ts_code="BAD.SZ", trade_date="not-a-date"),  # 日期不可解析
        _raw(ts_code="ZERO.SZ", close=0.0),               # close<=0 脏行
    ])
    assert len(rows) == 1 and rows[0]["ts_code"] == "127045.SZ"


def test_map_daily_rows_tolerates_missing_optionals():
    rows = map_daily_rows([
        {"ts_code": "127049.SZ", "trade_date": "20260901", "close": 100.0},
    ])
    assert rows[0]["volume"] is None and rows[0]["pre_close"] is None


# ── 序列组装（build_code_series 纯函数） ────────────────────────────


def _orm_row(day: str, close: float, pre_close: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=__import__("datetime").date.fromisoformat(day),
        open=None, high=None, low=None, close=close,
        pre_close=pre_close, volume=1000.0, amount=None,
    )


def test_build_code_series_latest_change_pct_and_spark_source():
    out = build_code_series("159865.SZ", "国泰中证畜牧养殖ETF", [
        _orm_row("2026-08-31", 0.548, 0.551),
        _orm_row("2026-09-01", 0.557, 0.548),
        _orm_row("2026-09-02", 0.548, 0.557),
    ])
    assert out.ts_code == "159865.SZ"
    assert str(out.latest.trade_date) == "2026-09-02"
    assert out.change_pct == round((0.548 - 0.557) / 0.557 * 100, 2)  # close vs pre_close
    assert [p.close for p in out.series] == [0.548, 0.557, 0.548]     # 升序（sparkline 输入）


def test_build_code_series_empty_rows_has_no_latest():
    out = build_code_series("127045.SZ", "牧原转债", [])
    assert out.latest is None and out.change_pct is None and out.series == []


def test_build_code_series_missing_pre_close_change_pct_none():
    out = build_code_series("127045.SZ", None, [_orm_row("2026-09-01", 120.0, None)])
    assert out.latest.close == 120.0 and out.change_pct is None


# ── ingest 接线（db 透传给 upsert + 错误摘要进 result） ─────────────


async def test_ingest_passes_db_to_upsert_and_reports_errors(monkeypatch):
    """回归锁定：upsert 必须收到 db（2026-09-03 实跑曾因缺参被逐项容错吞掉）。"""
    import pandas as pd

    from app.services import securities_service as svc

    class _FakeClient:
        async def fetch_fund_daily(self, ts_code, start_date, end_date):
            return pd.DataFrame([{
                "ts_code": ts_code, "trade_date": "20260901", "open": 0.5, "high": 0.6,
                "low": 0.5, "close": 0.55, "pre_close": 0.54, "vol": 100.0, "amount": 55.0,
            }])

        async def fetch_cb_daily(self, ts_code, start_date, end_date):
            raise RuntimeError("permission denied")  # 模拟单代码失败

    calls: list[tuple[object, list]] = []

    async def _fake_upsert(db, rows):
        calls.append((db, rows))
        return len(rows)

    monkeypatch.setattr(svc.repo, "upsert_fund_etf_daily", _fake_upsert)
    monkeypatch.setattr(svc.repo, "upsert_cb_daily", _fake_upsert)

    marker = object()  # 假 db 句柄，验证原样透传
    result = await svc.ingest_industry_securities(
        marker, "pig", backfill_days=30, client=_FakeClient()
    )

    assert len(calls) == 1  # 只有 ETF 成功；3 只 cb 全部失败被跳过
    assert calls[0][0] is marker
    assert calls[0][1][0]["ts_code"] == "159865.SZ"
    assert result["etf_upserted"] == 1 and result["cb_upserted"] == 0
    assert len(result["errors"]) == 3  # 每只失败转债一条错误摘要（进任务 result）
    assert all("permission denied" in e for e in result["errors"])


# ── 冲突列口径：repo 常量 = 模型唯一约束 = 迁移 ─────────────────────


def test_conflict_cols_match_model_unique_constraints():
    from sqlalchemy import UniqueConstraint

    assert SECURITIES_CONFLICT_COLS == ("ts_code", "trade_date")
    for model in (FundEtfDaily, CbDaily):
        constraints = [c for c in model.__table_args__ if isinstance(c, UniqueConstraint)]
        assert len(constraints) == 1
        assert tuple(constraints[0].columns.keys()) == SECURITIES_CONFLICT_COLS
