import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { ChangeText } from "@/shared/ui";
import { fetchConceptsBySymbol } from "@/shared/api/concept";

/** 默认展示的概念数（§3 触点 C）；超出部分折叠成 `+N`。 */
const DEFAULT_VISIBLE = 8;

/**
 * 个股详情「所属概念」（§3 触点 C）：查询失败或 `items` 为空 → **整块不渲染**（零占位），
 * 保证既有版式零扰动（e2e 断言 `concept-tags` 数量为 0）。
 * `pct_change` 为 `null` 时只显示概念名（缺失 ≠ 0.00%）。
 */
export function ConceptTags({ symbol }: { symbol: string }) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);

  const { data } = useQuery({
    queryKey: ["stock-concepts", symbol],
    queryFn: () => fetchConceptsBySymbol(symbol),
    staleTime: 300_000,
  });

  const items = data?.items ?? [];
  if (items.length === 0) return null;

  const shown = expanded ? items : items.slice(0, DEFAULT_VISIBLE);

  return (
    <Space size={4} wrap data-testid="concept-tags">
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        所属概念
      </Typography.Text>
      {shown.map((item) => (
        <Tag
          key={item.board_code}
          style={{ cursor: "pointer" }}
          onClick={() => navigate(`/market/concept/${item.board_code}`)}
        >
          {item.board_name}
          {item.pct_change != null ? (
            <>
              {" "}
              <ChangeText value={item.pct_change} />
            </>
          ) : null}
        </Tag>
      ))}
      {items.length > DEFAULT_VISIBLE ? (
        <Tag.CheckableTag checked={expanded} onChange={setExpanded}>
          {expanded ? "收起" : `+${items.length - DEFAULT_VISIBLE}`}
        </Tag.CheckableTag>
      ) : null}
    </Space>
  );
}
