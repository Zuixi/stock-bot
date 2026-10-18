"""离线产物自愈（spec §S12.3）：reconcile 尾步把三个 JSON 产物推到 ``day``。

三个产物（随仓库入库、读面直接读文件）此前**只**由手工脚本更新：

==============================  ==========================================
产物                             重建入口（纯函数，唯一实现）
==============================  ==========================================
``market_regime_history.json``    ``scripts.build_market_regime.build``
``peer_history_stats.json``      ``scripts.build_peer_history_stats.build``
``triage_rule_stats.json``       ``scripts.build_triage_rule_stats.build``
==============================  ==========================================

它们的 ``as_of`` 是"数据自身的最大交易日"，所以**不需要新 cron**：把"重建"挂在
对账链的尾巴上（行情刚被对齐的那一天），产物自然会跟着走。判据是幂等的：
``as_of >= day`` ⇒ 跳过（新鲜时零查询、零重建）；``as_of < day`` ⇒ 重建。

**为什么复用 script 的 ``build`` 而不是在本模块再写一份 SQL**：两处实现必然漂移
（一处改了阈值/窗口，另一处还是旧口径），而且产物是"可评审、可 diff"的证据——
它必须与手工脚本产出**逐字节一致**，否则每次自愈都会制造一个假 diff。故脚本被
重构为「``build(session)`` 纯函数 + ``write_document`` + ``__main__`` 薄壳」，
本模块 import 它们。

**单个产物失败不扩散**（spec §S12.3）：

- 失败**保留旧文件**（先写同目录临时文件再 ``os.replace``，写一半崩了也不破坏旧产物）；
- 告警走既有 :func:`app.services.job_alert_service.record_job_failure`
  （id = ``artifacts_refresh:<产物名>``，``/market/data-freshness`` 的 ``failed_jobs`` 可见）；
- 继续处理其它产物，并把失败名放进返回值的 ``failed``。

**只读纪律**：三个 ``build`` 只接收调用方的会话、不改它的事务属性。手工脚本那条
轨道自建会话并显式 ``SET TRANSACTION READ ONLY``；挂在 reconcile 上时复用对账会话
（同一事务里还有写操作，在那里设只读会打断对账）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import job_alert_service, market_regime_service, peer_history_service
from scripts import build_market_regime, build_peer_history_stats, build_triage_rule_stats

logger = logging.getLogger(__name__)

__all__ = ["ARTIFACTS", "Artifact", "refresh_stale_artifacts", "stale_artifacts"]


@dataclass(frozen=True)
class Artifact:
    """一个离线产物：写到哪里、怎么建、怎么写。

    ``build`` / ``write`` 都指回 ``scripts/build_*.py`` 的同一份实现（禁止两处）；
    测试用 :func:`dataclasses.replace` 把 ``path`` 换到 tmp 目录、把 ``build`` 换成
    计数替身，从而在不碰真库/真产物的前提下断言"新鲜时零重建"。
    """

    path: Path
    #: ``async (session) -> document``：读库算产物，**不写文件**
    build: Callable[[AsyncSession], Awaitable[dict[str, Any]]]
    #: ``(document, path) -> Path``：确定性序列化（同一文档两次运行逐字节一致）
    write: Callable[[dict[str, Any], Path], Path]
    #: 代码侧**当前**版本（产物里的 ``version`` 与它不一致 ⇒ 视为过期，即使 ``as_of`` 很新）。
    #: 为什么需要：口径改了（阈值/统计窗口）但 ``as_of`` 没变时，只按日期判会让"新规则 + 旧数字"
    #: 静默并存（2026-10-19 regime 的 basis 文案落后就是这么来的）。
    #: ``None`` = 该产物**没有版本字段**（如 `triage_rule_stats`）→ 只按 ``as_of`` 判，避免
    #: 每轮都重建一个 35s 的产物。
    version: str | None = None


#: 产物登记表（键 = 返回值里用的名字，也是日志/告警里的标识）。
#:
#: 顺序即处理顺序；三者互不依赖，故单个失败不影响后面的产物。
ARTIFACTS: dict[str, Artifact] = {
    "market_regime_history": Artifact(
        path=build_market_regime.OUT_PATH,
        build=build_market_regime.build,
        write=build_market_regime.write_document,
        version=market_regime_service.REGIME_VERSION,
    ),
    "peer_history_stats": Artifact(
        path=build_peer_history_stats.OUT_PATH,
        build=build_peer_history_stats.build,
        write=build_peer_history_stats.write_document,
        version=peer_history_service.PEER_HISTORY_VERSION,
    ),
    "triage_rule_stats": Artifact(
        path=build_triage_rule_stats.OUT_PATH,
        build=build_triage_rule_stats.build,
        write=build_triage_rule_stats.write_document,
        version=None,  # 该产物没有 version 字段：只按 as_of 判（避免每轮重建 35s）
    ),
}


def _as_of_from_document(document: Any) -> date | None:
    """文档里的 ``as_of``（ISO 日期串，与三个产物一致）；缺失/不可解析 → ``None``。"""
    if not isinstance(document, dict):
        return None
    raw = document.get("as_of")
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        # 只取日期部分：产物写的是纯日期，但容忍 "2026-10-16T00:00:00" 这类带时刻的写法
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _read_as_of(path: Path) -> date | None:
    """读产物现有 ``as_of``；文件不存在/读不了/不是合法 JSON → ``None``（= 需重建）。

    损坏的产物不该让 reconcile 尾巴报错：读不出来就当成"没有产物"，重建一份覆盖它
    （``os.replace`` 保证不会留下半截文件）。
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Artifact unreadable, will rebuild: %s", path, exc_info=True)
        return None
    return _as_of_from_document(document)


def _read_version(path: Path) -> str | None:
    """产物里的 ``version``（口径版本）；缺失/读不了 → ``None``（= 需重建）。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    raw = document.get("version")
    return raw if isinstance(raw, str) else None


def _is_stale(artifact: Artifact, day: date) -> bool:
    """过期 = ``as_of`` 缺失/落后 **或** 产物版本与代码不一致（口径变了）。"""
    as_of = _read_as_of(artifact.path)
    if as_of is None or as_of < day:
        return True
    if artifact.version is None:
        return False
    return _read_version(artifact.path) != artifact.version


def _write_atomic(artifact: Artifact, document: dict[str, Any]) -> None:
    """写同目录临时文件再 ``os.replace``：要么旧文件、要么完整新文件，不存在半截。"""
    tmp = artifact.path.with_name(f".{artifact.path.name}.tmp")
    try:
        artifact.write(document, tmp)
        os.replace(tmp, artifact.path)
    finally:
        # 成功时 tmp 已被 replace 掉；失败时清掉残留（不能留下"看起来像产物"的 .tmp）
        tmp.unlink(missing_ok=True)


def stale_artifacts(day: date) -> list[str]:
    """哪些产物 ``as_of < day``（**只读文件、零 DB**）：job 层据此决定要不要开会话。

    与 :func:`refresh_stale_artifacts` 用同一个判据；拆出来是因为"新鲜"是绝大多数轮次，
    为了它去开一个数据库会话（job 层测试里甚至等于连真库）不值得。
    """
    stale: list[str] = []
    for name, artifact in ARTIFACTS.items():
        if _is_stale(artifact, day):
            stale.append(name)
    return stale


async def refresh_stale_artifacts(db: AsyncSession, day: date) -> dict[str, list[str]]:
    """把 ``as_of < day`` 的产物重建到最新（``as_of >= day`` 的跳过）。

    参数：
        db: 复用调用方的会话（**只读**：三个 build 只发 SELECT、不写库）
        day: 目标交易日（reconcile 的 ``expected_latest`` —— 过了齐备门禁的那天）

    返回 ``{"refreshed": [...], "skipped": [...], "failed": [...]}``，元素是
    :data:`ARTIFACTS` 的键；``failed`` 里的产物**旧文件保持原样**且已记 1 条告警。

    三种"不重建"的语义（都进 ``skipped``，都不告警）：

    1. 产物新鲜（``as_of >= day``）—— 正常情况，绝大多数轮次走这条；
    2. 产物缺失/损坏但重建出的 ``as_of`` 仍不前进——行情还没到目标日，下一轮再试；
    3. 产物存在、重建出的 ``as_of`` 没有前进（``<=`` 现有值）——不写文件，
       避免把一份"更旧的数据"盖到更新的产物上。
    """
    refreshed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for name, artifact in ARTIFACTS.items():
        if not _is_stale(artifact, day):
            skipped.append(name)
            continue
        # 重建前先记下现有 as_of：若重建出来的值与它相同或更旧，不写文件（不覆盖更新的产物）
        as_of = _read_as_of(artifact.path)
        try:
            document = await artifact.build(db)
            new_as_of = _as_of_from_document(document)
            if new_as_of is None:
                raise ValueError(f"产物文档缺少可解析的 as_of（{artifact.path.name}）")
            if as_of is not None and new_as_of <= as_of:
                logger.info(
                    "Artifact rebuild did not advance: %s (new as_of=%s <= current %s)",
                    name,
                    new_as_of,
                    as_of,
                )
                skipped.append(name)
                continue
            _write_atomic(artifact, document)
        except Exception as exc:  # noqa: BLE001 — 单产物失败不得扩散（spec §S12.3）
            failed.append(name)
            logger.warning("Artifact refresh failed: %s", name, exc_info=True)
            # 告警不能反过来打断本函数：record_job_failure 自己吞一切异常
            await job_alert_service.record_job_failure(f"artifacts_refresh:{name}", repr(exc))
            continue
        refreshed.append(name)
        logger.info("Artifact refreshed: %s as_of %s -> %s", name, as_of, new_as_of)
    return {"refreshed": refreshed, "skipped": skipped, "failed": failed}
