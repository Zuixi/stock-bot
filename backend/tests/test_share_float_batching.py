"""批量 upsert 必须分片：asyncpg 单语句 bind 参数上限 32767（实测解禁任务因此静默崩了 13 天）。

share_float 近 7 日窗口一次可拉到数千行 × 8 列 → 单条 INSERT 的绑定参数直接越界，
`InterfaceError: the number of query arguments cannot exceed 32767`；APScheduler 只看到
"job 函数返回了"，日志里却是成功。分片是仓库既有惯例（quotes 由调用方按 500 分批、
index_dailies 按 2000 分批），这里把它收敛成一个可测的纯函数。

契约不只落在纯函数上：`upsert_share_floats` 必须**真的**逐批 execute（下面用计数假
session 把"删掉 for batch in chunk_rows(...) 循环"变成测试红灯）。
"""

from typing import Any

import pytest

from app.repositories.market_data_repo import UPSERT_CHUNK, chunk_rows, upsert_share_floats


def test_chunk_rows_splits_large_batches() -> None:
    rows = [{"a": i} for i in range(1200)]
    chunks = list(chunk_rows(rows))
    assert [len(c) for c in chunks] == [500, 500, 200]


def test_chunk_rows_keeps_small_batch_whole() -> None:
    assert [len(c) for c in chunk_rows([{"a": 1}])] == [1]
    assert list(chunk_rows([])) == []


def test_chunk_rows_rejects_non_positive_size() -> None:
    """size<=0 必须给出可读的 ValueError，而不是 range 的原始报错。"""
    with pytest.raises(ValueError, match="size"):
        list(chunk_rows([{"a": 1}], size=0))
    with pytest.raises(ValueError, match="size"):
        list(chunk_rows([{"a": 1}], size=-5))


def test_upsert_chunk_leaves_headroom_below_asyncpg_limit() -> None:
    """share_float 每行 8 列：单批绑定参数 = 500 × 8 = 4000，远低于 32767。"""
    assert UPSERT_CHUNK == 500
    assert UPSERT_CHUNK * 8 < 32767


class _FakeCursorResult:
    """Postgres Core INSERT 的 execute 返回 CursorResult（只有 rowcount 被用到）。"""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _CountingSession:
    """假 session：数 execute 次数、回放固定 rowcount，借此把"是否真的逐批写"变成断言。"""

    def __init__(self, rowcount: int) -> None:
        self._rowcount = rowcount
        self.execute_calls = 0
        self.flush_calls = 0

    async def execute(self, statement: Any) -> _FakeCursorResult:
        self.execute_calls += 1
        return _FakeCursorResult(self._rowcount)

    async def flush(self) -> None:
        self.flush_calls += 1


def _share_float_rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "float_date": f"2026-09-{(i % 28) + 1:02d}",
            "ts_code": f"{i:06d}.SZ",
            "ann_date": "2026-08-01",
            "float_share": 1.0,
            "float_ratio": 1.0,
            "holder_name": "holder",
            "share_type": "首发原股东限售股份",
        }
        for i in range(n)
    ]


async def test_upsert_share_floats_executes_one_statement_per_chunk() -> None:
    """6000 行 → 12 批（500/批）；rowcount 累加为 12×N；flush 仍只有一次。

    这是把"删掉分片循环、恢复单条 execute"钉死的关键断言：删掉循环后 execute_calls == 1。
    """
    db = _CountingSession(rowcount=3)
    total = await upsert_share_floats(db, _share_float_rows(6000))  # type: ignore[arg-type]
    assert db.execute_calls == 12
    assert total == 12 * 3
    assert db.flush_calls == 1


async def test_upsert_share_floats_small_batch_single_statement() -> None:
    """小批次不该被无谓拆多（保持原语义：1 批 = 1 条语句）。"""
    db = _CountingSession(rowcount=2)
    total = await upsert_share_floats(db, _share_float_rows(7))  # type: ignore[arg-type]
    assert db.execute_calls == 1
    assert total == 2
