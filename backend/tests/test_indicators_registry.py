"""因子登记自洽性 + 口径字典同步（**无 DB、无 marker**）.

`industry_registry.FACTORS` 是因子元数据的**唯一登记地**，
`docs/design/metric-dictionary.md` §4.2 是它的**转录**。本文件把两者机械绑起来：

* 登记的 `func` 必须在 `indicators.py` 里**真实存在、可调用、签名匹配**（抄错函数名 → 红）
* 登记的 `window` 必须与函数签名的默认值**逐字一致**（口径漂移 → 红）
* 登记必须在字典表里**逐字出现**（名称 / 单位 / 窗口列）——这是
  `scripts/check_metric_dictionary.py` 之外的**第二道**机械锁（那个脚本只查 metric）
* `factor_key` **不得与 `metric_key` 撞名**（共用命名空间）
* `storage=materialized` 的 `materialized_column` 必须是 `stock_features` 的**既有列**
  ——这就是 spec §S2.1「新增列，不新增表」的机械判据：列名不在模型里即红

本文件不依赖数据库：不需要 `DATABASE_URL`，也不注册 marker。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.models.feature import StockFeature
from app.services import indicators as ind
from app.services.industry_registry import (
    FACTOR_GROUPS,
    FACTOR_STORAGE_MATERIALIZED,
    FACTOR_STORAGE_READ_TIME,
    FACTORS,
    INDUSTRIES,
    FactorDef,
    get_factor,
    validate_factors,
)

_DOC = Path(__file__).resolve().parents[2] / "docs" / "design" / "metric-dictionary.md"

# 因子表的列数（本文件与 check_metric_dictionary.py 都要能解析它）
_FACTOR_TABLE_CELLS = 5


def _metric_keys() -> set[str]:
    return {m.key for cfg in INDUSTRIES.values() for m in cfg.metrics}


def _doc_factor_rows() -> list[list[str]]:
    """解析 §4.2 里那 5 列*因子*表（识别方法：第一列 key 是 FACTORS 的子集）。"""
    if not _DOC.exists():  # pragma: no cover - 仓库内恒存在
        pytest.skip(f"缺口径字典: {_DOC}")
    lines = _DOC.read_text(encoding="utf-8").splitlines()
    in_section = False
    rows: list[list[str]] = []
    for line in lines:
        if line.startswith("## §4.2"):
            in_section = True
            continue
        if in_section and line.startswith("## §4.3"):
            break
        if not in_section or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != _FACTOR_TABLE_CELLS:
            continue
        key = cells[0].strip("`")
        if key in FACTORS:
            rows.append(cells)
    return rows


class TestRegistrySelfConsistency:
    def test_validate_factors_passes(self):
        validate_factors()  # 不抛异常即通过

    def test_every_factor_is_addressable_and_unique(self):
        assert len(FACTORS) == len({f.key for f in FACTORS.values()})
        for key, f in FACTORS.items():
            assert isinstance(f, FactorDef)
            assert f.key == key
            assert get_factor(key) is f
            assert f.name, f"{key} 缺名称"
            assert f.group, f"{key} 缺展示分组"

    def test_get_factor_rejects_unregistered_key(self):
        assert get_factor("no_such_factor_key") is None

    def test_factor_keys_do_not_collide_with_metric_keys(self):
        collisions = set(FACTORS) & _metric_keys()
        assert collisions == set(), f"factor_key 与 metric_key 撞名: {sorted(collisions)}"

    def test_input_metrics_are_registered(self):
        known = _metric_keys()
        for key, f in FACTORS.items():
            missing = [m for m in f.input_metrics if m not in known]
            assert missing == [], f"{key} 引用了未注册指标: {missing}"

    def test_storage_values_are_legal(self):
        for key, f in FACTORS.items():
            assert f.storage in (FACTOR_STORAGE_READ_TIME, FACTOR_STORAGE_MATERIALIZED), key

    def test_groups_are_declared(self):
        used = {f.group for f in FACTORS.values()}
        assert used <= set(FACTOR_GROUPS), f"缺中文标签的分组: {sorted(used - set(FACTOR_GROUPS))}"
        assert all(FACTOR_GROUPS.values())


class TestFunctionBinding:
    """`func` 名字 ↔ `indicators.py` 实现：存在性 + 签名 + 窗口默认值口径。"""

    def test_declared_function_exists_and_is_callable(self):
        for key, f in FACTORS.items():
            fn = getattr(ind, f.func, None)
            assert fn is not None, f"{key} 登记的 func {f.func!r} 在 indicators.py 不存在"
            assert callable(fn), f"{key} 登记的 func {f.func!r} 不可调用"
            assert not inspect.iscoroutinefunction(fn), f"{key} 的 func 不得是 async"

    def test_declared_function_is_exported(self):
        for key, f in FACTORS.items():
            assert f.func in ind.__all__, f"{key} 的 func {f.func!r} 未进 indicators.__all__"

    def test_window_param_exists_on_function(self):
        for key, f in FACTORS.items():
            if f.window_param is None:
                continue
            fn = getattr(ind, f.func)
            params = inspect.signature(fn).parameters
            assert f.window_param in params, (
                f"{key} 声明 window_param={f.window_param!r}，但 {f.func} 签名里没有该参数"
            )

    def test_declared_window_matches_function_default(self):
        """registry 的 `window` 不得与函数签名默认值**矛盾**（口径只住一处）。

        函数可以是 keyword-only 且**无默认值**（如 `percentile_rank`：窗口是必填口径，
        由 registry/调用方给），此时唯一约束是"类型是 int 参数"。若函数**有**默认值，
        则必须与 registry 逐字相等——否则同一口径存在两个值，谁先被读到谁生效。
        """
        for key, f in FACTORS.items():
            if f.window_param is None:
                assert f.window is None, f"{key}: window_param=None 时 window 必须为 None"
                continue
            fn = getattr(ind, f.func)
            param = inspect.signature(fn).parameters[f.window_param]
            assert param.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ), f"{key}: {f.window_param} 必须是普通/关键字参数（不得是 *args/**kwargs）"
            if param.default is not inspect.Parameter.empty:
                assert param.default == f.window, (
                    f"{key}: registry window={f.window} 与 {f.func} 签名默认值 "
                    f"{param.default} 不一致（口径漂移）"
                )

    def test_declared_params_match_function_defaults(self):
        """`FactorDef.params` 必须逐字等于函数签名默认值（**口径参数不得只活在函数里**）。"""
        for key, f in FACTORS.items():
            fn = getattr(ind, f.func)
            params = inspect.signature(fn).parameters
            for name, value in f.params:
                assert name in params, f"{key}: params 里的 {name!r} 不在 {f.func} 签名里"
                assert params[name].default == value, (
                    f"{key}: registry 登记 {name}={value!r}，但 {f.func} 签名默认值是 "
                    f"{params[name].default!r}（口径漂移）"
                )

    def test_params_do_not_duplicate_window_param(self):
        for key, f in FACTORS.items():
            names = [n for n, _ in f.params]
            assert len(names) == len(set(names)), f"{key}: params 有重名"
            if f.window_param is not None:
                assert f.window_param not in names, f"{key}: window_param 重复出现在 params"

    def test_window_param_is_none_iff_function_has_no_window(self):
        """`window_param=None` ⟺ 函数没有"窗口"语义参数（全序列口径）。

        全序列口径是**声明**，不是省略：`drawdown` / `max_drawdown` /
        `consecutive_negative` 对整条序列生效，注册一个窗口值会是谎言。
        """
        for key, f in FACTORS.items():
            fn = getattr(ind, f.func)
            params = inspect.signature(fn).parameters
            if f.window_param is None:
                assert f.window is None, f"{key}: 无窗口参数时 window 必须为 None"
                continue
            assert f.window_param in params
            assert f.window is not None, f"{key}: 有窗口参数就必须登记窗口值"

    def test_units_are_declared_for_dimensioned_factors(self):
        """有量纲的因子单位不得为空串（空串 = 无量纲，是**声明**不是省略）。"""
        for key, f in FACTORS.items():
            assert isinstance(f.unit, str)
            if f.unit:
                assert f.unit.strip() == f.unit and f.unit, f"{key} 的单位含首尾空白"

    def test_description_is_present(self):
        for key, f in FACTORS.items():
            assert len(f.description) >= 10, f"{key} 缺描述（口径不明的因子禁止登记）"


class TestMaterializedFactorsReuseExistingColumns:
    """spec §S2.1：昂贵因子复用 stock_features 既有列 —— **新增列/新增表都不许**。"""

    def test_materialized_factors_name_existing_columns(self):
        columns = {c.key for c in StockFeature.__table__.columns}
        for key, f in FACTORS.items():
            if f.storage != FACTOR_STORAGE_MATERIALIZED:
                continue
            assert f.materialized_column, f"{key} 缺 materialized_column"
            assert f.materialized_column in columns, (
                f"{key} 的 materialized_column={f.materialized_column!r} 不是 "
                f"stock_features 既有列（新增列需要迁移，本 spec 不许）"
            )

    def test_no_two_factors_share_a_materialized_column(self):
        claimed = [
            f.materialized_column
            for f in FACTORS.values()
            if f.storage == FACTOR_STORAGE_MATERIALIZED
        ]
        assert len(claimed) == len(set(claimed))

    def test_materialized_and_read_time_are_both_populated(self):
        """留档：既有 4 个物化列之外，其余因子都是读时算（spec：低成本不落库）。"""
        materialized = {
            f.materialized_column
            for f in FACTORS.values()
            if f.storage == FACTOR_STORAGE_MATERIALIZED
        }
        read_time = {k for k, f in FACTORS.items() if f.storage == FACTOR_STORAGE_READ_TIME}
        assert materialized == {
            "return_percentile",
            "max_drawdown",
            "trend_slope",
            "annual_volatility",
        }
        assert len(read_time) >= 8, "读时算因子不应少于 8 个（spec 的因子面）"

    def test_read_time_factors_are_not_materialized(self):
        for key, f in FACTORS.items():
            if f.storage == FACTOR_STORAGE_READ_TIME:
                assert f.materialized_column is None, f"{key} 是读时算，不得声明物化列"


class TestMetricDictionarySync:
    """`FACTORS` ↔ `docs/design/metric-dictionary.md` §4.2 因子表：逐字转录。"""

    def test_factor_table_is_parseable(self):
        rows = _doc_factor_rows()
        assert len(rows) == len(FACTORS), (
            f"字典因子表解析到 {len(rows)} 行，registry 有 {len(FACTORS)} 个因子；两者必须逐条对应"
        )

    def test_every_registered_factor_appears_in_dictionary(self):
        assert set(_doc_factor_rows_keys()) == set(FACTORS), (
            "字典 §4.2 因子表与 registry FACTORS 的 key 集合不一致 —— 新增因子必须两处登记"
        )

    def test_names_units_and_windows_match_verbatim(self):
        rows = {r[0].strip("`"): r for r in _doc_factor_rows()}
        for key, f in FACTORS.items():
            cells = rows[key]
            doc_name, doc_unit, doc_window = cells[1], cells[2], cells[3]
            assert doc_name == f.name, f"{key}: 名称 {doc_name!r} != registry {f.name!r}"
            assert doc_unit == (f.unit or "—"), (
                f"{key}: 单位 {doc_unit!r} != registry {f.unit or '—'!r}"
            )
            expected_window = "—" if f.window is None else str(f.window)
            assert doc_window == expected_window, (
                f"{key}: 窗口 {doc_window!r} != registry {expected_window!r}"
            )

    def test_dictionary_factor_rows_are_not_metric_keys(self):
        """因子行不得是 metric_key（否则 check_metric_dictionary.py 会判 MISSING）。"""
        collisions = _doc_factor_rows_keys() & _metric_keys()
        assert collisions == set()


def _doc_factor_rows_keys() -> set[str]:
    return {r[0].strip("`") for r in _doc_factor_rows()}


class TestNoNewTablesOrMigrations:
    """spec 验收判据：本 spec **不新增任何表**（迁移文件中无 `create_table`）。

    注意判据是**迁移内容**，不是文件名：既有一个迁移叫
    `..._add_daily_basic_indicators_table.py`（"indicators" 出现在文件名里），
    按文件名 grep 会误报——所以这里只扫**建表语句本身**。
    """

    _MIGRATIONS = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"

    #: 本 spec 明令不建的表（"为不存在的因子写框架"）
    _FORBIDDEN_TABLES = (
        "factor_defs",
        "signal_rules",
        "signals",
        "backtest_events",
    )

    def test_migrations_directory_exists(self):
        assert self._MIGRATIONS.exists(), f"迁移目录不存在: {self._MIGRATIONS}"
        assert list(self._MIGRATIONS.glob("*.py")), "迁移目录为空（路径变了？）"

    def test_no_forbidden_table_is_created_in_any_migration(self):
        patterns = [
            re.compile(rf"create_table\s*\(\s*['\"]{t}['\"]") for t in self._FORBIDDEN_TABLES
        ]
        hits: list[str] = []
        for path in sorted(self._MIGRATIONS.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if pattern.search(text):
                    hits.append(f"{path.name}: {pattern.pattern}")
        assert hits == [], f"迁移里出现本 spec 禁止的表: {hits}"

    def test_no_factor_defs_table_in_metadata(self):
        assert "factor_defs" not in set(StockFeature.metadata.tables)

    def test_stock_features_columns_are_unchanged_by_this_task(self):
        """因子层**没有**动 stock_features 的表结构（列集合即判据）。

        若本任务真的加了列，列集合会变、且必然多出一个迁移文件——这条与
        `test_no_forbidden_table_is_created_in_any_migration` 合起来就是
        "不新增表、不新增列"的机械判据。
        """
        columns = {c.key for c in StockFeature.__table__.columns}
        assert columns == {
            "id",
            "stock_id",
            "asof_date",
            "window_days",
            "total_return",
            "return_percentile",
            "annual_volatility",
            "max_drawdown",
            "downside_vol",
            "trend_slope",
            "trend_r2",
            "ma_bullish",
            "trend_reversals",
            "avg_volume",
            "volume_volatility",
            "extra",
            "created_at",
        }
        assert "uq_stock_features_key" in {
            c.name for c in StockFeature.__table__.constraints if c.name
        }
