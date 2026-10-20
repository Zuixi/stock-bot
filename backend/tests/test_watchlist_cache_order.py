"""自选写面的**缓存失效时序**（2026-10-19 事故的机械门禁）。

事故：`add/remove/reorder` 原来在 service 里（事务内、commit 之前）删缓存。
并发读（另一个标签页/设备、或前端 invalidate 触发的 GET）会在这条缝里把**旧列表**
重新写回 Redis（TTL 300s）。表现是：

- 刚加的自选**时有时无**，刷新也不出现（读到的是 15 分钟前的列表）；
- 刚删的还在，再点删除变 404（"移除自选失败"），看起来像"不支持删除"。

正确顺序只能是 **commit → 失效**。这条纪律靠代码评审守不住（删一行 `cache.delete` 位置
看起来毫无差别），因此把它变成**源码级机械检查**：按路由块切分，断言每个含
`cache.delete(` 的处理函数里 `db.commit()` 出现在它前面。
"""

from __future__ import annotations

import re
from pathlib import Path

ROUTES_PATH = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "watchlists.py"
SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "watchlist_service.py"


def _route_blocks(source: str) -> list[tuple[str, str]]:
    """按 `@router.` 切块：返回 [(路由名, 块文本)]。"""
    parts = re.split(r"(?m)^@router\.", source)
    blocks: list[tuple[str, str]] = []
    for part in parts[1:]:
        name = part.split("(", 1)[1].split(")", 1)[0] if "(" in part else "?"
        blocks.append((name, part))
    return blocks


def test_cache_invalidated_after_commit_in_every_write_route() -> None:
    source = ROUTES_PATH.read_text(encoding="utf-8")
    blocks = _route_blocks(source)
    assert blocks, "没解析到任何路由块 —— 切分规则失效，别让门禁假绿"

    checked = 0
    for name, block in blocks:
        if "cache.delete(" not in block:
            continue
        checked += 1
        commit_at = block.find("db.commit()")
        delete_at = block.find("cache.delete(")
        assert commit_at != -1, f"{name} 里失效了缓存却没有 commit —— 写面必须显式提交"
        assert commit_at < delete_at, (
            f"{name} 的 `cache.delete` 出现在 `db.commit()` 之前：并发读会把旧列表写回缓存 "
            "（TTL 300s），症状是'刚加的自选不显示 / 刚删的还在'。请改成 commit 之后再失效。"
        )

    assert checked == 3, f"应有 3 个写路由（add/remove/reorder）失效缓存，实际 {checked}"


def test_service_does_not_touch_cache_on_write() -> None:
    """写面（service）不得自己删缓存：它跑在调用方事务里，commit 之前失效就是上面那个 bug。"""
    source = SERVICE_PATH.read_text(encoding="utf-8")
    # `list_user_watchlists` 是读面，允许 get/set；写函数里不允许出现 delete。
    for fn in ("add_watchlist_item", "remove_watchlist_item", "reorder_watchlist_items"):
        start = source.find(f"async def {fn}(")
        assert start != -1, f"找不到 {fn}"
        end = source.find("\nasync def ", start + 1)
        body = source[start : end if end != -1 else len(source)]
        # 只看**代码**（docstring 里提到 cache 是解释，不算违规）
        assert "cache.delete(" not in body and "cache.set(" not in body, (
            f"{fn} 仍在事务内失效/写缓存 —— 失效必须由路由层在 commit 之后做（见模块 docstring）"
        )
