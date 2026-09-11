"""sw SQL seed 解析单测（纯函数，不触 DB）。

背景：custom-tag overlay 曾因「注释头 + 巨型 INSERT」在按分号切分时被整块
跳过（导入恒为 0 行），且 dump 头行误带分号导致 INSERT 被截断——本组用例
锁定 _split_sql_statements 对这两类畸形的行为。
"""

from app.services.sw_industry_service import _split_sql_statements


def test_comment_header_does_not_swallow_insert():
    sql = (
        "-- header comment\n"
        "-- more comment\n"
        "INSERT INTO t (a) VALUES\n"
        "  ('1', 'x'),\n"
        "  ('2', 'y');\n"
        "-- trailing comment\n"
    )
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 1
    assert stmts[0].startswith("INSERT INTO t")
    assert stmts[0].endswith("('2', 'y')")


def test_multiple_statements_and_blank_lines():
    sql = "DELETE FROM t;\n\nINSERT INTO t VALUES (1);\n"
    assert _split_sql_statements(sql) == [
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
    ]


def test_full_comment_script_yields_nothing():
    assert _split_sql_statements("-- only\n-- comments\n") == []


def test_trailing_inline_comment_is_not_a_statement():
    """行尾注释（`; -- note`）落在分号之后，不能当成语句执行（会语法错）。"""
    sql = "DELETE FROM t; -- 说明文字\nINSERT INTO t VALUES (1);\n"
    assert _split_sql_statements(sql) == [
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
    ]


def test_comment_only_tail_chunk_dropped():
    sql = "INSERT INTO t VALUES (1);\n-- trailer\n"
    assert _split_sql_statements(sql) == ["INSERT INTO t VALUES (1)"]
