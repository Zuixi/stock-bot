"""批量 upsert 必须分片：asyncpg 单语句 bind 参数上限 32767（实测解禁任务因此静默崩了 13 天）。

share_float 近 7 日窗口一次可拉到数千行 × 8 列 → 单条 INSERT 的绑定参数直接越界，
`InterfaceError: the number of query arguments cannot exceed 32767`；APScheduler 只看到
"job 函数返回了"，日志里却是成功。分片是仓库既有惯例（quotes 由调用方按 500 分批、
index_dailies 按 2000 分批），这里把它收敛成一个可测的纯函数。
"""

from app.repositories.market_data_repo import UPSERT_CHUNK, chunk_rows


def test_chunk_rows_splits_large_batches() -> None:
    rows = [{"a": i} for i in range(1200)]
    chunks = list(chunk_rows(rows))
    assert [len(c) for c in chunks] == [500, 500, 200]


def test_chunk_rows_keeps_small_batch_whole() -> None:
    assert [len(c) for c in chunk_rows([{"a": 1}])] == [1]
    assert list(chunk_rows([])) == []


def test_upsert_chunk_leaves_headroom_below_asyncpg_limit() -> None:
    """share_float 每行 8 列：单批绑定参数 = 500 × 8 = 4000，远低于 32767。"""
    assert UPSERT_CHUNK == 500
    assert UPSERT_CHUNK * 8 < 32767
