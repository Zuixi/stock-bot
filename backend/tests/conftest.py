"""
Pytest fixtures — tests hit the real running API via HTTP.
Tests connect to the API's DATABASE_URL pointing to stock_bot_test,
so all queries run against the isolated test database.
"""

import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

# Point to the real test database (same as what the running API uses)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://stock_user:stock_pass@localhost:5432/stock_bot_test"
)

# API runs inside Docker; use host.docker.internal to reach host port 8000
API_BASE_URL = os.environ.get("API_BASE_URL", "http://host.docker.internal:8000")

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
_ALEMBIC_BIN = str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic")


@pytest.fixture(scope="session", autouse=True)
def _schema_at_head() -> None:
    """会话开始前把测试库收敛到 ``alembic heads``（架子不变量，不动数据）。

    为什么需要（Fix round 1）：库内**迁移状态会被别的测试文件改**——
    ``test_industry_metric_pit_roundtrip.py``（现已 opt-in）与
    ``test_intraday_snapshot_table.py`` 都会真跑 ``alembic downgrade``；后者在前置
    downgrade 失败时曾把库留在**丢表的中间版本**上，于是后面所有用例面对的是一张
    缺表的库（失败原因与它们自身的代码毫无关系，极难定位）。

    本 fixture 只做 ``upgrade head``：**不改任何行**、不 DROP、不清表，
    无库时静默跳过（``-m "not e2e"`` 之类的无库环境不该因此红）。
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL
    env["TUSHARE_TOKEN"] = ""
    try:
        subprocess.run(
            [_ALEMBIC_BIN, "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=_BACKEND_DIR,
            env=env,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        # alembic 不在（极简环境）或库不可达 → 交给各用例自己的 requires_pg 处理
        return


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """
    httpx AsyncClient that hits the real HTTP API.
    No ASGI transport, no event-loop conflicts with the DB driver.
    """
    async with AsyncClient(base_url=API_BASE_URL, timeout=30.0) as ac:
        yield ac
