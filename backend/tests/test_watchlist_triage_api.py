"""``GET /api/v1/watchlists/triage`` 契约：登录即可读、>50 只 422、载荷字段齐全.

为什么这些断言必须存在：
- **登录即可读**（spec §S8.1 的鉴权判据）：数据是公开行情 + 用户自己的自选，若被
  ``research:read`` 之类的细粒度权限挡住，普通用户就看不到自己自选的体检；
- **>50 只 422 而不是静默截断**（spec §S8.3）：截断会让用户以为"其余都没事"；
- **载荷字段齐全**：``as_of`` / ``state`` / ``quiet_count`` / ``folded`` / ``unavailable`` /
  ``unavailable_keys`` / ``disclaimer`` 缺一个前端就会退化成空态或丢掉合规文案
  （前端不硬编码免责声明）。``state`` 缺了更糟：三种空态只能靠猜。
- **``unavailable`` 是 label 不是 key**：机器串上屏（``sector_move``）用户读不懂。

本文件不连库：``get_triage`` / ``user_symbols`` 打桩，只验证 HTTP 契约与鉴权。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, get_args

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db
from app.api.v1 import router as api_v1_router
from app.api.v1 import watchlist_triage as api
from app.config import settings
from app.core.auth.jwks import jwks_client
from app.schemas.triage import TriageItemOut, TriageOut
from app.services import triage_detectors as td
from app.services import triage_service as svc

AS_OF = date(2026, 10, 16)


# ── 鉴权：静态 RSA 公钥 + 自签 assertion ────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return priv, priv.public_key(), "test-triage-key-2026"


@pytest.fixture(autouse=True)
def _static_jwks(rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]) -> None:
    _, pub, kid = rsa_key_pair
    jwks_client.set_static_key(kid, pub)


def _assertion(rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]) -> str:
    priv, _, kid = rsa_key_pair
    now = int(time.time())
    payload = {
        "iss": settings.auth_issuer,
        "sub": str(uuid.uuid4()),
        "aud": settings.auth_audience,
        "username": "triage_user",
        "roles": ["researcher"],
        # 故意**不给** research:read / research:manage：体检是"登录即可"
        "permissions": [],
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": f"ast_{uuid.uuid4().hex}",
        "session_id": f"sess_{uuid.uuid4().hex[:8]}",
    }
    return jwt.encode(payload, priv, algorithm="RS256", headers={"kid": kid, "typ": "JWT"})


# ── 测试应用：真 v1 路由 + 打桩 DB ──────────────────────────────────────────


@pytest.fixture
def triage_app() -> FastAPI:
    app = FastAPI(title="Triage API Test App")
    app.include_router(api_v1_router)

    class _DummyDb:
        """DB 依赖替身：本文件所有用例都在 handler 里打桩，不该真查到库。"""

    async def _override_db() -> Any:
        yield _DummyDb()

    app.dependency_overrides[get_db] = _override_db
    return app


async def _get(
    app: FastAPI,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {"X-Principal-Assertion": token} if token else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers, params=params)


def _sample_result() -> svc.TriageResult:
    item = td.TriageItem(
        symbol="600000",
        name="浦发银行",
        detector="break_ma20",
        severity="risk",
        headline="跌破 20 日线",
        facts=(td.Fact("收盘", 9.2, "元"), td.Fact("MA20", 9.96, "元")),
        as_of=AS_OF,
        source="daily_quotes",
        verify="kline",
        rule_text=td.SPEC_BREAK_MA20.rule_text,
        rule_version=td.SPEC_BREAK_MA20.version,
        source_table=td.SPEC_BREAK_MA20.source_tables[0],
        day_pct=-8.0,
    )
    return svc.TriageResult(
        as_of=AS_OF,
        items=[item],
        quiet_count=3,
        folded=1,
        state="ok",
        unavailable=["所属板块在动"],
        unavailable_keys=["sector_move"],
    )


def test_schema_severity_enum_matches_registry() -> None:
    """响应契约的四枚举必须与探测器 registry 逐字一致（两处漂移 = 第五种分级上屏）。"""
    assert set(get_args(TriageItemOut.model_fields["severity"].annotation)) == set(td.SEVERITIES)


def test_schema_state_enum_matches_service() -> None:
    """``state`` 的三枚举必须与 ``triage_service.STATES`` 逐字一致。

    漏一个的后果不是报错而是**落进前端 `else`**——"没数据"会被画成"没自选"，
    正是这次集成要修的那个 bug。
    """
    assert set(get_args(TriageOut.model_fields["state"].annotation)) == set(svc.STATES)


# ── 用例 ────────────────────────────────────────────────────────────────────


async def test_triage_requires_login(triage_app: FastAPI) -> None:
    resp = await _get(triage_app, "/api/v1/watchlists/triage", token=None)
    assert resp.status_code in (401, 403)


async def test_triage_rejects_more_than_max_symbols(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """>50 只 → 422 并提示分组（**不静默截断**，spec §S8.3）。"""

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("超限请求不得进入取数层")

    monkeypatch.setattr(svc, "get_triage", _boom)
    token = _assertion(rsa_key_pair)
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage",
        token=token,
        params={"symbols": ",".join(["600000"] * (svc.MAX_SYMBOLS + 1))},
    )
    assert resp.status_code == 422
    assert str(svc.MAX_SYMBOLS) in resp.json()["detail"]


async def test_triage_rejects_limit_above_page_size(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """``limit`` 上限 = 单页上限（>8 会让噪声控制失效，spec §S8.4）。"""
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage",
        token=_assertion(rsa_key_pair),
        params={"symbols": "600000", "limit": svc.LIMIT + 1},
    )
    assert resp.status_code == 422


async def test_triage_returns_full_contract(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stub(_db: Any, codes: list[str], limit: int = svc.LIMIT) -> svc.TriageResult:
        assert codes == ["600000", "000001"]
        assert limit == svc.LIMIT
        return _sample_result()

    monkeypatch.setattr(svc, "get_triage", _stub)
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage",
        token=_assertion(rsa_key_pair),
        params={"symbols": "600000, 000001"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == AS_OF.isoformat()
    assert body["state"] == "ok"
    assert body["quiet_count"] == 3
    assert body["folded"] == 1
    assert body["disclaimer"] == api.DISCLAIMER
    assert "不构成投资建议" in body["disclaimer"], "合规页脚必须随载荷下发（spec §S8.5）"
    # 上屏字段是**人话 label**（不含 "_" 即不含探测器 key）；机器串只在 unavailable_keys
    assert body["unavailable"] == ["所属板块在动"]
    assert not any("_" in label for label in body["unavailable"])
    assert body["unavailable_keys"] == ["sector_move"]
    item = body["items"][0]
    assert item["symbol"] == "600000"
    assert item["severity"] == "risk"
    assert item["verify"] == "kline"
    assert item["as_of"] == AS_OF.isoformat()
    assert item["facts"][0] == {"label": "收盘", "value": 9.2, "unit": "元"}
    # 规则来源三字段（provenance 契约）：卡片据此显示"依据哪条规则（哪个版本）、来自哪张表"
    assert item["rule_text"] == td.SPEC_BREAK_MA20.rule_text
    assert item["rule_version"] == "r1"
    assert item["source_table"] == "daily_quotes"


async def test_triage_rules_returns_eight_rules_with_full_fields(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``GET /watchlists/triage/rules``：固定 8 条、字段齐全、登录即可（不查库）。

    契约字段名逐字照抄 ``.superpowers/sdd/triage-provenance/contract.md``：
    少一个字段前端就无法渲染规则的来源/失效场景，``stats`` 缺失更会被画成"0 信号"。
    """
    monkeypatch.setattr(api.triage_rules, "STATS_PATH", tmp_path / "missing.json")
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage/rules",
        token=_assertion(rsa_key_pair),
    )
    assert resp.status_code == 200
    rules = resp.json()["rules"]
    assert [rule["key"] for rule in rules] == [spec.key for spec in td.TRIAGE_DETECTORS]
    expected = {
        "key",
        "label",
        "severity",
        "version",
        "rule_text",
        "definition",
        "thresholds",
        "source_tables",
        "update_note",
        "failure_modes",
        "stats",
        "stats_note",
    }
    for rule, spec in zip(rules, td.TRIAGE_DETECTORS, strict=True):
        assert set(rule) == expected
        assert rule["severity"] == spec.severity
        assert rule["version"] == spec.version
        assert rule["rule_text"] == spec.rule_text
        assert rule["definition"] == spec.definition
        assert rule["thresholds"] == spec.thresholds
        assert rule["source_tables"] == list(spec.source_tables)
        assert rule["update_note"] == spec.update_note
        assert rule["failure_modes"] == list(spec.failure_modes)
        assert len(rule["failure_modes"]) >= 3
        # 统计产物缺失：stats 必须为 null 且**写明原因**（不许静默给空统计）
        assert rule["stats"] is None
        assert rule["stats_note"], f"{rule['key']} 的 stats 为空却没有说明原因"
        assert "尚未生成" in rule["stats_note"]


async def test_triage_rules_requires_login(triage_app: FastAPI) -> None:
    resp = await _get(triage_app, "/api/v1/watchlists/triage/rules", token=None)
    assert resp.status_code in (401, 403)


async def test_triage_rules_maps_static_stats(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """有统计产物时逐字段透传（``stats`` 的字段名就是标定报告的口径）。"""
    stats_path = tmp_path / "triage_rule_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "as_of": AS_OF.isoformat(),
                "window_months": 12,
                "basis": "超额=信号组中位收益−当日全市场中位收益",
                "rules": {
                    "drop_sharp": {
                        "stats": {
                            "signals": 1234,
                            "days": 240,
                            "per_day": 5.14,
                            "t1_excess_med": -0.3,
                            "t5_excess_med": -1.02,
                            "t20_excess_med": -1.23,
                            "t5_p05": -9.81,
                        },
                        "stats_note": None,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(api.triage_rules, "STATS_PATH", stats_path)
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage/rules",
        token=_assertion(rsa_key_pair),
    )
    assert resp.status_code == 200
    rules = {rule["key"]: rule for rule in resp.json()["rules"]}
    assert len(rules) == 9
    assert rules["drop_sharp"]["stats"] == {
        "as_of": AS_OF.isoformat(),
        "window_months": 12,
        "signals": 1234,
        "days": 240,
        "per_day": 5.14,
        "t1_excess_med": -0.3,
        "t5_excess_med": -1.02,
        "t20_excess_med": -1.23,
        "t5_p05": -9.81,
        "basis": "超额=信号组中位收益−当日全市场中位收益",
    }
    assert rules["drop_sharp"]["stats_note"] is None
    # 产物里没有的规则仍然要给 null + 原因（8 条必须一个不少）
    assert rules["break_ma20"]["stats"] is None
    assert rules["break_ma20"]["stats_note"]


async def test_triage_rules_reads_committed_artifact(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """**随仓库入库**的统计产物必须能被端点完整投影（产物损坏/缺规则 = 门禁红）。

    为什么不只测 tmp JSON：产物本身是交付物（``backend/app/services/triage_rule_stats.json``）。
    只测 tmp 的话，入库文件写坏了、或新增规则后忘了复跑脚本，端点会安静地退化成 8 条
    ``stats=null``，而这条用例就是那个"安静"的对手。
    """
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage/rules",
        token=_assertion(rsa_key_pair),
    )
    assert resp.status_code == 200
    rules = {rule["key"]: rule for rule in resp.json()["rules"]}
    assert set(rules) == {spec.key for spec in td.TRIAGE_DETECTORS}
    with_stats = 0
    for rule in rules.values():
        if rule["stats"] is None:
            assert rule["stats_note"], f"{rule['key']} 的 stats 为空却没有说明原因"
            continue
        with_stats += 1
        assert rule["stats"]["window_months"] == 12
        assert rule["stats"]["days"] >= 1
        assert rule["stats"]["signals"] >= rule["stats"]["days"]
    # 产物覆盖 8 条里的 6 条（公告/龙虎榜源表不足 12 个月 → 允许为 null + 说明）
    assert with_stats >= 6


async def test_triage_rejects_oversized_user_watchlist(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连自己的自选表超过 50 只也 422（提示分组），不降级、不静默截断。"""

    async def _too_many(
        _db: Any, _user_id: str, _watchlist_id: uuid.UUID | None = None
    ) -> list[str]:
        return [f"{600000 + i:06d}" for i in range(svc.MAX_SYMBOLS + 1)]

    monkeypatch.setattr(svc, "user_symbols", _too_many)
    resp = await _get(triage_app, "/api/v1/watchlists/triage", token=_assertion(rsa_key_pair))
    assert resp.status_code == 422


async def test_triage_defaults_to_user_watchlist(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺省 ``symbols`` → 读当前用户自选（前端 ``fetchTriage()`` 就是不带参数调用的）。"""
    seen: dict[str, Any] = {}

    async def _user_symbols(
        _db: Any, user_id: str, watchlist_id: uuid.UUID | None = None
    ) -> list[str]:
        seen["user_id"] = user_id
        seen["watchlist_id"] = watchlist_id
        return ["600000"]

    async def _stub(_db: Any, codes: list[str], limit: int = svc.LIMIT) -> svc.TriageResult:
        seen["codes"] = codes
        return _sample_result()

    monkeypatch.setattr(svc, "user_symbols", _user_symbols)
    monkeypatch.setattr(svc, "get_triage", _stub)
    resp = await _get(triage_app, "/api/v1/watchlists/triage", token=_assertion(rsa_key_pair))
    assert resp.status_code == 200
    assert seen["codes"] == ["600000"]
    assert seen["user_id"], "必须把登录用户的 id 传下去（自选是用户私有数据）"


async def test_triage_serializes_no_watchlist_state(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空自选 → ``state='no_watchlist'`` 必须真的到客户端（前端据此给"去添加自选"入口）。

    若这个字段丢了，前端只能靠 ``items=[] & quiet_count=0`` 猜，就会把"今天数据没就绪"
    也画成"还没有自选股"（本次集成修的正是这条）。
    """

    async def _empty(_db: Any, _user_id: str, _watchlist_id: uuid.UUID | None = None) -> list[str]:
        return []

    async def _stub(_db: Any, codes: list[str], limit: int = svc.LIMIT) -> svc.TriageResult:
        assert codes == []
        return svc.TriageResult(as_of=AS_OF, state="no_watchlist")

    monkeypatch.setattr(svc, "user_symbols", _empty)
    monkeypatch.setattr(svc, "get_triage", _stub)
    resp = await _get(triage_app, "/api/v1/watchlists/triage", token=_assertion(rsa_key_pair))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "no_watchlist"
    assert body["items"] == []
    assert body["quiet_count"] == 0
    assert body["unavailable"] == [] and body["unavailable_keys"] == []


async def test_triage_dedupes_repeated_symbols(
    triage_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复代码去重后才进取数层（否则 quiet_count 被重复计数污染）。"""
    seen: dict[str, Any] = {}

    async def _stub(_db: Any, codes: list[str], limit: int = svc.LIMIT) -> svc.TriageResult:
        seen["codes"] = codes
        return _sample_result()

    monkeypatch.setattr(svc, "get_triage", _stub)
    resp = await _get(
        triage_app,
        "/api/v1/watchlists/triage",
        token=_assertion(rsa_key_pair),
        params={"symbols": "600000,600000,000001"},
    )
    assert resp.status_code == 200
    assert seen["codes"] == ["600000", "000001"]
