"""Industry knowledge service (P6): 机构图谱 / 数据权威性原则 / 思维导图 读路径.

内容表为纯数据（迁移内 seed），本服务只做 registry 行业校验 + 行分组装配；
未知行业 404 语义与 metric 服务共用同一 UnknownIndustryError。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import industry_knowledge_repo as repo
from app.schemas.industry import (
    IndustryKnowledgeOut,
    KnowledgeOrgOut,
    KnowledgePrincipleOut,
)
from app.services.industry_metric_service import UnknownIndustryError
from app.services.industry_registry import get_industry

logger = logging.getLogger(__name__)


def assemble_knowledge(entries: list[tuple[str, dict]]) -> IndustryKnowledgeOut:
    """(kind, payload) 行列表 → 知识库装配（纯函数，离线单测锁定）.

    org 全量保留（按传入顺序，repo 已按 sort 排好）；principle/mindmap 各取首个匹配行；
    形状不合法的行 log 后跳过（内容表容错，不让单行脏数据打挂整个 Tab）。
    """
    orgs: list[KnowledgeOrgOut] = []
    principle: KnowledgePrincipleOut | None = None
    mindmap: dict | None = None
    for kind, payload in entries:
        if not isinstance(payload, dict):
            logger.warning("knowledge row kind=%s has non-dict payload (skipped)", kind)
            continue
        try:
            if kind == "org":
                orgs.append(KnowledgeOrgOut(**payload))
            elif kind == "principle" and principle is None:
                principle = KnowledgePrincipleOut(**payload)
            elif kind == "mindmap" and mindmap is None:
                mindmap = payload
        except Exception as exc:  # pydantic 校验失败：跳过该行而非打挂 Tab
            logger.warning("knowledge row kind=%s malformed (skipped): %s", kind, exc)
    return IndustryKnowledgeOut(org=orgs, principle=principle, mindmap=mindmap)


async def get_industry_knowledge(
    db: AsyncSession, industry_key: str
) -> IndustryKnowledgeOut:
    """知识库聚合：已知行业无内容 → 空形状 200；未知行业 → UnknownIndustryError(404)。"""
    if get_industry(industry_key) is None:
        raise UnknownIndustryError(f"Industry '{industry_key}' is not configured in registry")
    rows = await repo.list_knowledge(db, industry_key)
    return assemble_knowledge([(r.kind, r.payload) for r in rows])
