"""S1.4 · 申万成分对账：本地 own-level 计数 vs 上游 ``index_member`` 计数。

复现 ``docs/design/data-source.md`` 附.6.2 **#11 / #13** 的对账证据（Fix round 1 的
Critical C1 关闭依据）：对每个申万指数，取上游 ``index_member`` 返回的行数，与本地
``sw_industry_member_periods`` 里该行业代码的 **own-level 当前成分数**逐条比较，
打印逐节点对照表与汇总，**任何不一致都以非零码退出**。

用法（backend/ 下）::

    .venv/bin/python scripts/reconcile_sw_member_counts.py                 # 31 个 L1
    .venv/bin/python scripts/reconcile_sw_member_counts.py --levels 1,2,3  # 全部 511 个

上游调用失败（无 token / 网络受限 / 限频）**响亮失败**，不静默打印空表。

Reproducibility contract（Ruling 51/52）：本脚本是附.6.2 #11/#13 的入库证据来源
——文档引用的实机数字必须能被已入库的脚本复现。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.services.sw_member_scd2_service import parse_sw_index_mapping  # noqa: E402

# 本地 own-level 当前成分计数（附.6.2 #13 的口径：改灌三级后与上游逐条一致的那一列）。
# `is_current` 取上游 is_new='Y'（见 normalize_member_rows），与"上游当前返回的成员行"同义。
_LOCAL_COUNT_SQL = text(
    """
    SELECT count(*) AS n
    FROM sw_industry_member_periods m
    WHERE m.industry_code = :code AND m.is_current
    """
)


async def _local_count(db, code: str) -> int:
    return int((await db.execute(_LOCAL_COUNT_SQL, {"code": code})).scalar_one())


def _load_token() -> str:
    """Load TUSHARE_TOKEN without printing it（与 spike_sw_member_history 同口径）。"""
    from dotenv import dotenv_values

    candidates = [
        # 本 worktree 一般没有 backend/.env（git-ignored）→ 回退主 checkout 的
        Path(__file__).resolve().parents[1] / ".env",
        Path("/Users/wqz/Developer/stock-bot/backend/.env"),
    ]
    for path in candidates:
        if path.exists():
            token = dotenv_values(path).get("TUSHARE_TOKEN") or ""
            if token:
                print(f"[ENV ] token loaded from {path} (len={len(token)}, value not printed)")
                return token
    raise SystemExit("[FATAL] no TUSHARE_TOKEN found in any candidate .env —— 对账必须真连上游")


async def _build_client() -> Any:
    """真上游客户端（独立成函数：测试/离线演练可整体替换，不必戳 ``TuShareClient`` 内部）。"""
    from app.core.providers.tushare_client import TuShareClient

    return TuShareClient(_load_token())


async def main() -> int:
    parser = argparse.ArgumentParser(description="申万成分 own-level 对账（local vs upstream）")
    parser.add_argument(
        "--levels",
        default="1",
        help="逗号分隔的层级（1/2/3）；默认 1（附.6.2 #13 的 31 个 L1）",
    )
    args = parser.parse_args()
    levels = tuple(int(x) for x in args.levels.split(",") if x.strip())

    refs = [r for r in parse_sw_index_mapping() if r.level in levels]
    if not refs:
        raise SystemExit(f"[FATAL] 对照表里 levels={levels} 没有任何条目——拒绝在空集合上对账")

    client: Any = await _build_client()

    mismatches: list[tuple[str, str, int, int]] = []
    failed: list[tuple[str, str]] = []
    print(f"[PLAN   ] levels={levels} 节点数={len(refs)}")
    print(f"{'代码':<8} {'名称':<12} {'local':>6} {'upstream':>9}  一致")
    async with async_session_factory() as db:
        for ref in refs:
            try:
                df = await client.fetch_index_member(index_code=ref.index_code)
            except Exception as exc:  # noqa: BLE001 — 逐节点记录，最后统一响亮失败
                failed.append((ref.index_code, f"{type(exc).__name__}: {exc}"))
                print(f"[FETCH-FAIL] {ref.index_code} {ref.industry_name}: {exc}")
                continue
            upstream = len(df)
            local = await _local_count(db, ref.industry_code)
            ok = local == upstream
            if not ok:
                mismatches.append((ref.industry_code, ref.industry_name, local, upstream))
            print(
                f"{ref.industry_code:<8} {ref.industry_name:<12} {local:>6} {upstream:>9}"
                f"  {'✓' if ok else '✗'}"
            )

    compared = len(refs) - len(failed)
    print(
        f"\n[SUMMARY] 对账 {compared}/{len(refs)} 个节点；不一致 {len(mismatches)}；"
        f"拉取失败 {len(failed)}"
    )
    for code, name, local, upstream in mismatches:
        print(f"[MISMATCH] {code} {name}: local={local} upstream={upstream}")
    if failed:
        print(f"[FATAL] {len(failed)} 个节点上游拉取失败，对账不完整——拒绝给出「一致」结论")
        return 2
    if mismatches:
        print("[FATAL] 存在 local ≠ upstream 的节点（见 [MISMATCH] 行）")
        return 1
    print(f"[OK] 全部 {compared} 个节点 local == upstream")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
