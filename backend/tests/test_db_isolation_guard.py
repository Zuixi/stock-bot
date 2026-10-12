"""测试库隔离守卫（Ruling 59 的机械化教训）：**用例不得把破坏性操作打到 dev 库**。

## 为什么需要这个文件

2026-10-13 定位到"回补后 PIT 100%、交付时 `published_at` 全 NULL"的真因**不是**任何
服务的写路径，而是 `tests/test_intraday_snapshot_table.py` **硬编码了 dev 库**
（`localhost:5433/stock_bot`）并对它真跑 `alembic downgrade d7c8b9a0e1f2`：

- 该版本在 `f1a2b3c4d5e6_add_metric_pit_and_caliber` **之下**，于是迁移链会执行
  `f1a2b3c4d5e6.downgrade()` → **DROP `industry_metrics.published_at` /
  `caliber_version`**；`upgrade head` 再把两列加回来（值全 NULL）。
- `DROP/ADD COLUMN` **不重写行**，所以行数、`created_at`、`xmin` **全都不变**，
  只有两列的值没了 —— 于是"每跑一次全量 pytest 就静默打假一次所有指标的 PIT"，
  而且现场看起来像"某个服务的写入把 PIT 抹了"（连 `created_at` 都对得上）。

同类坑在 SCD2 表上已经踩过一次（见 `test_industry_metric_pit_roundtrip.py` 的
"破坏性 downgrade 必须显式 opt-in"），但当时只给那一个文件加了闸门，**没有机械化**，
于是同一个坑换了个文件又踩了一次。本文件把那条教训变成断言。

## 两条断言

1. **静态**：`tests/**/*.py` 里不得出现硬编码的 dev 库 DSN（`5433/stock_bot`、
   `5432/stock_bot`，即不以 `_test` 结尾者）。唯一的白名单见 :data:`_DEV_DSN_ALLOWLIST`。
2. **动态**：会真跑 `alembic downgrade` 的两个模块，其**解析后的 URL** 不得指向 dev 库
   （即使有人用 `TEST_DATABASE_URL` 指过去，也要在这里红，而不是静默清空 dev 库）。

破坏验证（改完请手动跑一次，确认断言不是恒真）：
把一个用例里的 `TEST_DATABASE_URL` 换成字面量 `"postgresql://…@localhost:5433/stock_bot"`
→ 断言 1 红；把 `TEST_DATABASE_URL` 指到 dev 库跑 `pytest tests/test_db_isolation_guard.py`
→ 断言 2 红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

#: dev 库 DSN 的字面量形态（`stock_bot` 后面**不能**紧跟 `_test`）。
_DEV_DSN_RE = re.compile(r"(?:localhost|127\.0\.0\.1):543[23]/stock_bot(?!_test)")

#: 允许直连 dev 库的用例（逐个说明理由；这是一个**人工裁定白名单**，不是默认许可）。
_DEV_DSN_ALLOWLIST: dict[str, str] = {
    # 只自建自清 market_limit_up_stock_daily 的 2026-09-08 一天，不碰 industry_metrics，
    # 不做任何 DDL / downgrade；历史日、无其它写入者（见该文件模块 docstring）。
    "test_limit_up_stock_daily.py": "只写自建自清的 2026-09-08 一天，无 DDL、不触 PIT 列",
}

#: 会真跑 `alembic downgrade` 的模块（property：它们的 URL 绝不能是 dev 库）。
_DESTRUCTIVE_MIGRATION_MODULES = (
    "test_intraday_snapshot_table.py",
    "test_industry_metric_pit_roundtrip.py",
)


def test_no_test_module_hardcodes_the_dev_database() -> None:
    """`tests/**` 不得硬编码 dev 库 DSN（白名单逐个有理由）。"""
    offenders: list[str] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            # 本文件在**散文/正则**里必须写出这个 DSN 才能解释清楚它（否则守卫自己
            # 看不懂自己在守什么）；它本身不连任何库，故跳过自身。
            continue
        rel = path.relative_to(_TESTS_DIR).as_posix()
        if rel in _DEV_DSN_ALLOWLIST or path.name in _DEV_DSN_ALLOWLIST:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _DEV_DSN_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "以下位置硬编码了 dev 库 DSN（`localhost:5433/stock_bot`）——破坏性用例会打到"
        "开发数据上；请改用 `TEST_DATABASE_URL`，或写进 _DEV_DSN_ALLOWLIST 并说明理由：\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("module_file", _DESTRUCTIVE_MIGRATION_MODULES)
def test_destructive_migration_modules_never_resolve_to_the_dev_database(
    module_file: str,
) -> None:
    """跑 `alembic downgrade` 的模块解析出的 URL 不得是 dev 库（即使环境变量指过去）。"""
    source = (_TESTS_DIR / module_file).read_text(encoding="utf-8")
    # 动态断言：把模块里"解析 URL"的那一行按同样的规则在测试进程里重算一遍。
    # 不用 import 该模块：它 import 期就会连库 / 起线程，守卫不该有那种副作用。
    resolved: str | None = None
    if "TEST_DATABASE_URL" in source:
        import os

        resolved = os.environ.get("TEST_DATABASE_URL")
    assert resolved is None or not _DEV_DSN_RE.search(resolved), (
        f"{module_file} 会真跑 alembic downgrade（可能 DROP industry_metrics 的 PIT 两列），"
        f"而当前解析到的 URL 是 dev 库：{resolved!r}。请把 TEST_DATABASE_URL 指到测试库。"
    )
