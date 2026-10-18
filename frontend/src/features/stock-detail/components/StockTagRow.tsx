import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchConceptsBySymbol } from "@/shared/api/concept";
import { fetchStockSwTags } from "@/shared/api/swIndustry";
import { ChangeText, useIsNarrowViewport } from "@/shared/ui";
import type { Exchange } from "@/shared/types";
import { CustomSwTags } from "./CustomSwTags";
import { UserTags } from "./UserTags";
import "./StockTagRow.css";

/** 自定义申万标签的配色沿用 CustomSwTags 的既有映射（二级蓝 / 三级紫）。 */
const TAG_COLORS: Record<number, string> = { 2: "blue", 3: "purple" };

/**
 * 合并后一行里**最多同时出现**的芯片数：超出折叠成 `+N`（点开展开）。
 *
 * 为什么窄屏只留 2 枚：`<768` 的验收判据是「标签区 ≤2 行」（计划 §5 / F14）。390px 下
 * 一枚带涨跌幅的概念芯片约 100–110px、可用宽度约 350px，再算上「编辑」「登录后添加标签」
 * 「所属概念」这些固定文案，3 枚就会溢到第 3 行。少于这个数不是信息缺失 —— 末尾的 `+N`
 * 一键展开，且概念详情本身还有入口。
 */
const MAX_VISIBLE_NARROW = 2;
/** 宽屏 8 枚 = 改造前 `ConceptTags` 的默认展示数，不减少既有信息量；1440 下正好一行。 */
const MAX_VISIBLE_WIDE = 8;

interface Props {
  exchange: Exchange;
  symbol: string;
}

/**
 * 个股分类/标签**单行**（F14）：把「自定义申万分类 / 自定义标签 / 所属概念」三块合并成
 * 一个 `flex-wrap` 行，超出折叠 `+N`。改造前三块各占一行，把首屏的 K 线整整推下去三段。
 *
 * 数据来源与旧版**逐个对齐**（同一个 query key，命中缓存 ⇒ 零新增请求）：
 * - SW 分类：`["stock-sw-tags", exchange, symbol]`（`CustomSwTags` 同键）
 * - 概念：`["stock-concepts", symbol]`（`ConceptTags` 同键）
 * - 个人标签：整块交给 `UserTags`（它自带增删/登录态分支，是可编辑控件组，作为一个原子块
 *   参与这一行；匿名态下它只是一枚「登录后添加标签」。）
 *
 * `data-testid="concept-tags"` 仍挂在概念组上并用 `display: contents` 参与整行排布 ——
 * 概念为空/请求失败时整组不渲染（零占位），与 `conceptBoard.spec.ts` 的契约一致。
 */
export function StockTagRow({ exchange, symbol }: Props) {
  const navigate = useNavigate();
  const narrow = useIsNarrowViewport();
  const [expanded, setExpanded] = useState(false);

  const { data: swTags = [] } = useQuery({
    queryKey: ["stock-sw-tags", exchange, symbol],
    queryFn: () => fetchStockSwTags(exchange, symbol),
    staleTime: 5 * 60 * 1000,
  });

  const { data: conceptData, isError: conceptError } = useQuery({
    queryKey: ["stock-concepts", symbol],
    queryFn: () => fetchConceptsBySymbol(symbol),
    staleTime: 300_000,
  });

  const conceptItems = conceptData?.items ?? [];
  const max = narrow ? MAX_VISIBLE_NARROW : MAX_VISIBLE_WIDE;

  // 预算按 SW → 概念的顺序分配（个人标签块不占这份预算）
  const swShown = expanded ? swTags : swTags.slice(0, max);
  const conceptShown = expanded
    ? conceptItems
    : conceptItems.slice(0, Math.max(0, max - swShown.length));
  const hiddenCount =
    swTags.length + conceptItems.length - swShown.length - conceptShown.length;

  return (
    <div className="stock-tag-row" data-testid="stock-tag-row">
      {swShown.length > 0 && (
        <>
          <span className="stock-tag-row__label">分类</span>
          {swShown.map((tag) => (
            <Tag key={tag.industryCode} color={TAG_COLORS[tag.level] ?? "default"}>
              {tag.industryName}
            </Tag>
          ))}
        </>
      )}

      {/* 只出「编辑」按钮 + 弹窗；chips 已在上面合并渲染 */}
      <CustomSwTags exchange={exchange} symbol={symbol} variant="controls" />

      <UserTags exchange={exchange} symbol={symbol} />

      {conceptShown.length > 0 && (
        <span className="stock-tag-row__group" data-testid="concept-tags">
          <span className="stock-tag-row__label">所属概念</span>
          {conceptShown.map((item) => (
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
        </span>
      )}

      {hiddenCount > 0 && (
        <Tag.CheckableTag checked={expanded} onChange={() => setExpanded((prev) => !prev)}>
          {expanded ? "收起" : `+${hiddenCount}`}
        </Tag.CheckableTag>
      )}

      {/* 概念为空但**成功**时才给一句人话：失败态沿用「零占位」契约（不伪装成"没有"） */}
      {swTags.length === 0 && conceptItems.length === 0 && !conceptError && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          暂无分类标签
        </Typography.Text>
      )}
    </div>
  );
}
