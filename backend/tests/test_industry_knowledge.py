"""纯单元测试：P6 知识库 — 种子内容形状 + 装配纯函数（离线，无 DB/网络）。

种子内容单点维护在 app/services/industry_knowledge_seed.py，迁移与单测共用；
断言锚定结构（四分组/徽章 tier/树可序列化），不锚定文案细节。
"""

import json

from app.services.industry_knowledge_seed import build_pig_knowledge_rows
from app.services.industry_knowledge_service import assemble_knowledge

VALID_TIERS = {"official", "highfreq", "calc", "manual", "derived"}
KNOWLEDGE_GROUPS = ["官方", "协会", "数据平台", "期货"]


# ── 种子行形状 ────────────────────────────────────────────────────────


def test_org_rows_cover_four_groups_with_valid_tiers():
    rows = [r for r in build_pig_knowledge_rows() if r["kind"] == "org"]
    assert len(rows) >= 12
    groups = {r["payload"]["group"] for r in rows}
    assert groups == set(KNOWLEDGE_GROUPS)

    names = [r["payload"]["name"] for r in rows]
    assert len(names) == len(set(names)), "机构名不得重复"

    for r in rows:
        p = r["payload"]
        assert set(p) >= {"name", "group", "tier", "desc", "urls"}
        assert p["tier"] in VALID_TIERS
        assert p["desc"] and isinstance(p["urls"], list)

    # 官方组全部为官方基准徽章（权威性最高的分组）
    official_tiers = {r["payload"]["tier"] for r in rows if r["payload"]["group"] == "官方"}
    assert official_tiers == {"official"}


def test_org_sort_follows_group_order():
    rows = [r for r in build_pig_knowledge_rows() if r["kind"] == "org"]
    order = [r["payload"]["group"] for r in rows]
    ranks = [KNOWLEDGE_GROUPS.index(g) for g in order]
    assert ranks == sorted(ranks), f"org 行 sort 未按分组顺序：{order}"


def test_principle_has_at_least_four_items():
    rows = [r for r in build_pig_knowledge_rows() if r["kind"] == "principle"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["title"] == "数据权威性使用原则"
    assert len(payload["items"]) >= 4
    assert all(isinstance(i, str) and i for i in payload["items"])


def test_mindmap_serializable_nonempty_two_level_tree():
    rows = [r for r in build_pig_knowledge_rows() if r["kind"] == "mindmap"]
    assert len(rows) == 1

    payload = rows[0]["payload"]
    roundtrip = json.loads(json.dumps(payload))  # JSONB 落表可序列化
    assert roundtrip == payload

    assert payload["name"]
    branches = payload["children"]
    assert len(branches) >= 4  # 供给/需求/成本/政策/金融
    for branch in branches:
        assert branch["name"] and branch["children"]
        # 叶子 ≤2 层深：分支下不再有 children
        assert all("children" not in leaf or not leaf["children"] for leaf in branch["children"])


def test_all_rows_are_pig_with_known_kinds():
    rows = build_pig_knowledge_rows()
    assert rows
    for r in rows:
        assert r["industry_key"] == "pig"
        assert r["kind"] in {"org", "principle", "mindmap"}
        assert isinstance(r["payload"], dict)
        assert isinstance(r["sort"], int)


# ── 装配纯函数 ────────────────────────────────────────────────────────


def test_assemble_from_seed_rows():
    out = assemble_knowledge([(r["kind"], r["payload"]) for r in build_pig_knowledge_rows()])
    assert len(out.org) >= 12
    assert out.principle is not None and len(out.principle.items) >= 4
    assert out.mindmap is not None and len(out.mindmap["children"]) >= 4


def test_assemble_empty_and_malformed_rows_tolerated():
    # 已知行业无内容 → 空形状 200（前端显示待录入）
    empty = assemble_knowledge([])
    assert empty.org == [] and empty.principle is None and empty.mindmap is None

    # 脏行（形状不合法/非 dict）跳过不抛穿；principle/mindmap 只取首行
    out = assemble_knowledge([
        ("org", {"name": "X", "group": "官方", "tier": "official"}),
        ("org", {"name": "坏行", "group": "官方"}),            # 缺 tier → 跳过
        ("org", "not-a-dict"),                                  # 非 dict → 跳过
        ("principle", {"title": "t", "items": ["a"]}),
        ("principle", {"title": "t2", "items": ["b"]}),         # 第二条不覆盖
        ("mindmap", {"name": "树", "children": []}),
    ])
    assert [o.name for o in out.org] == ["X"]
    assert out.principle is not None and out.principle.title == "t"
    assert out.mindmap is not None and out.mindmap["name"] == "树"
