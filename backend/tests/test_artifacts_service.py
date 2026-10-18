"""spec §S12.3 离线产物自愈：新鲜零重建 / 过期重建 / 单产物失败不扩散。

三条验收判据（与 spec 逐条对应）：

1. **产物新鲜 ⇒ 零重建**：三个 ``build`` 一次都不许被调用（它们各有几十秒的 SQL）；
2. **产物过期 ⇒ 各重建一次且 ``as_of`` 前进**（幂等：同一天再跑不会重复重建）；
3. **一个产物抛错 ⇒ 其它两个仍完成**、``failed`` 含它、**旧文件逐字节不变**、告警 1 条。

测试**不连库**：``build`` 换成计数替身（真实 SQL 由各脚本自己的契约测试覆盖），
产物目录换到 ``tmp_path``；``write`` 也换成一个最小的确定性 writer ——
真实 writer 的形状约束（``dump_document`` 的字段表）不是本模块的契约，
本模块负责的是"什么时候调 build / 什么时候写 / 失败怎么隔离"。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services import artifacts_service as svc

#: 目标交易日（reconcile 的 ``expected_latest``）
DAY = date(2026, 10, 20)
NAMES: tuple[str, ...] = ("market_regime_history", "peer_history_stats", "triage_rule_stats")


def _write_minimal(document: dict[str, Any], path: Path) -> Path:
    """最小确定性 writer（真实 writer 的字段形状由各脚本自己钉住）。"""
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    as_of: date | None,
    failures: frozenset[str] = frozenset(),
    advance_to: date = DAY,
) -> tuple[dict[str, int], dict[str, Path]]:
    """把登记表整体换到 tmp 目录 + 计数替身（返回调用次数与文件路径）。"""
    calls: dict[str, int] = {}
    paths: dict[str, Path] = {}
    registry: dict[str, svc.Artifact] = {}
    for name in NAMES:
        base = svc.ARTIFACTS[name]
        path = tmp_path / base.path.name
        if as_of is not None:
            path.write_text(
                json.dumps({"as_of": as_of.isoformat(), "name": name}) + "\n", encoding="utf-8"
            )
        calls[name] = 0
        paths[name] = path

        def _build(name: str = name) -> Any:
            async def build(db: Any) -> dict[str, Any]:
                calls[name] += 1
                if name in failures:
                    raise RuntimeError(f"boom: {name}")
                return {"as_of": advance_to.isoformat(), "name": name}

            return build

        # 版本置 None：本文件测的是"as_of 新鲜度"这一条判据；版本比对由
        # `test_version_mismatch_counts_as_stale` 单独覆盖（否则假产物没有 version 字段，
        # 会被版本判据判成永远过期）。
        registry[name] = replace(
            base, path=path, build=_build(), write=_write_minimal, version=None
        )

    monkeypatch.setattr(svc, "ARTIFACTS", registry)
    return calls, paths


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


async def test_fresh_artifacts_are_never_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §S12.3 验收①：产物 ``as_of >= day`` ⇒ 三个 build 调用次数都是 0。"""
    calls, paths = _install(tmp_path, monkeypatch, as_of=DAY)
    before = {name: path.read_bytes() for name, path in paths.items()}

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 0 for name in NAMES}
    assert payload == {"refreshed": [], "skipped": list(NAMES), "failed": []}
    # job 层的零 DB 预检（新鲜时不值得为它开一条连接）
    assert svc.stale_artifacts(DAY) == []
    assert {name: path.read_bytes() for name, path in paths.items()} == before


async def test_future_as_of_is_also_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``as_of > day`` 同样跳过（产物比目标日还新：不回退、不重写）。"""
    calls, _ = _install(tmp_path, monkeypatch, as_of=DAY + timedelta(days=3))

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 0 for name in NAMES}
    assert payload["skipped"] == list(NAMES)


async def test_stale_artifacts_are_rebuilt_once_and_as_of_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §S12.3 验收②：过期 ⇒ 各调用 1 次、``as_of`` 前进到 ``day``、无残留 tmp。"""
    stale = DAY - timedelta(days=1)
    calls, paths = _install(tmp_path, monkeypatch, as_of=stale)
    assert svc.stale_artifacts(DAY) == list(NAMES)

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 1 for name in NAMES}
    assert payload == {"refreshed": list(NAMES), "skipped": [], "failed": []}
    for name in NAMES:
        assert _read(paths[name])["as_of"] == DAY.isoformat(), name
        assert not (tmp_path / f".{paths[name].name}.tmp").exists(), "临时文件必须清掉"

    # 幂等：同一天再跑一轮 —— 已经新鲜，零重建
    again = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]
    assert again == {"refreshed": [], "skipped": list(NAMES), "failed": []}
    assert calls == {name: 1 for name in NAMES}


async def test_missing_artifact_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件不存在 ⇒ 视为过期并重建（自愈的起点：产物被删/首次部署）。"""
    calls, paths = _install(tmp_path, monkeypatch, as_of=None)
    assert not paths["market_regime_history"].exists()

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 1 for name in NAMES}
    assert payload["refreshed"] == list(NAMES)
    assert _read(paths["market_regime_history"])["as_of"] == DAY.isoformat()


async def test_single_failure_keeps_old_file_and_alerts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §S12.3 验收③：一个 build 抛错 ⇒ 其它两个完成、旧文件不变、告警 1 条。"""
    stale = DAY - timedelta(days=1)
    broken = "peer_history_stats"
    calls, paths = _install(tmp_path, monkeypatch, as_of=stale, failures=frozenset({broken}))
    old_bytes = paths[broken].read_bytes()

    alerts: list[tuple[str, str]] = []

    async def _record(job_id: str, error: str, **kwargs: Any) -> None:
        alerts.append((job_id, error))

    # 告警走既有 job_alert_service（spec §S12.3）：这里只数调用，不碰 Redis
    monkeypatch.setattr(svc.job_alert_service, "record_job_failure", _record)

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 1 for name in NAMES}, "失败不得中断后面的产物"
    assert payload["failed"] == [broken]
    assert payload["refreshed"] == [name for name in NAMES if name != broken]
    # 旧产物**逐字节**不变（不是"内容等价"，是根本没被写）
    assert paths[broken].read_bytes() == old_bytes
    # 其它两个已经前进
    for name in NAMES:
        if name == broken:
            continue
        assert _read(paths[name])["as_of"] == DAY.isoformat(), name
    assert len(alerts) == 1, f"单个产物失败只记 1 条告警：{alerts}"
    job_id, error = alerts[0]
    assert job_id == f"artifacts_refresh:{broken}"
    assert "boom" in error


async def test_rebuild_without_advancing_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重建没有前进（行情还没到目标日）⇒ 不写文件、不算失败（下一轮再试）。"""
    stale = DAY - timedelta(days=2)
    calls, paths = _install(tmp_path, monkeypatch, as_of=stale, advance_to=stale)
    before = {name: path.read_bytes() for name, path in paths.items()}

    payload = await svc.refresh_stale_artifacts(object(), DAY)  # type: ignore[arg-type]

    assert calls == {name: 1 for name in NAMES}
    assert payload == {"refreshed": [], "skipped": list(NAMES), "failed": []}
    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_builders_are_importable_from_the_app_package() -> None:
    """镜像里必须能 import 三个 builder（`backend/.dockerignore` 曾整体忽略 `scripts/`）。

    为什么这条要存在：`artifacts_service` 在 reconcile 尾步直接 import `scripts.build_*`，
    而 `scripts/` 一度被 `.dockerignore` 排除 ⇒ 测试全绿、容器里 `ModuleNotFoundError`。
    这条测试只能证明"测试环境可导入"，镜像侧的对应检查是
    `docker run --rm <image> python -c "import scripts.build_market_regime"`（收尾验证跑）。
    """
    import importlib

    for name in (
        "scripts.build_market_regime",
        "scripts.build_peer_history_stats",
        "scripts.build_triage_rule_stats",
    ):
        assert importlib.import_module(name) is not None, (
            f"{name} 必须可导入（artifacts_service 依赖它）"
        )


def test_version_mismatch_counts_as_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """产物 `as_of` 很新但**版本对不上** ⇒ 仍要重建。

    为什么需要：口径改了（阈值/统计窗口）而 `as_of` 没变时，只按日期判会让"新规则 + 旧数字"
    静默并存（2026-10-19 regime 的 basis 文案落后就是这么来的）。
    """
    base = svc.ARTIFACTS["market_regime_history"]
    path = tmp_path / "regime.json"
    path.write_text(json.dumps({"version": "s-old", "as_of": "2026-10-16"}), encoding="utf-8")
    artifact = replace(base, path=path, version="s-new")
    monkeypatch.setattr(svc, "ARTIFACTS", {"market_regime_history": artifact})
    assert svc.stale_artifacts(date(2026, 10, 16)) == ["market_regime_history"]

    # 版本对上 + 日期新鲜 ⇒ 不重建
    path.write_text(json.dumps({"version": "s-new", "as_of": "2026-10-16"}), encoding="utf-8")
    assert svc.stale_artifacts(date(2026, 10, 16)) == []
