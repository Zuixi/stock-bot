import { useMemo, useState } from "react";
import { Card, List, Segmented, Space, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { ChangeText, FreshnessNote } from "@/shared/ui";
import { Link } from "react-router-dom";
import { fetchHotBoards, type HotBoardCategory } from "@/shared/api/market";
import { hotBoardDegradedText } from "@/shared/api/marketEnvelope";
import { useMarketPolling } from "../hooks/useMarketPolling";
import { BoardDrilldownDrawer, type BoardDrilldownTarget } from "./BoardDrilldownDrawer";

const HOT_BOARD_CATEGORIES: { key: HotBoardCategory; label: string }[] = [
  { key: "industry", label: "行业板块" },
  { key: "concept", label: "概念板块" },
  { key: "region", label: "地域板块" },
];

const STALE_TIME = 5 * 60 * 1000;

function getHotBoardCategoryLabel(category: HotBoardCategory): string {
  return HOT_BOARD_CATEGORIES.find((item) => item.key === category)?.label ?? "热门板块";
}

export function HotSectors() {
  const [category, setCategory] = useState<HotBoardCategory>("industry");
  const [boardTarget, setBoardTarget] = useState<BoardDrilldownTarget | null>(null);
  const { refetchInterval } = useMarketPolling();
  const { data: boardEnvelope, isLoading } = useQuery({
    queryKey: ["market", "hot-boards", category],
    queryFn: () => fetchHotBoards(category),
    staleTime: STALE_TIME,
    refetchInterval,
  });
  const boardRows = boardEnvelope?.items ?? [];
  const rows = useMemo(
    () => [...boardRows].sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent)).slice(0, 6),
    [boardRows]
  );
  // 产地降级文案：东财来源为 null，只有回落本地分组才上屏
  const degradedText = boardEnvelope
    ? hotBoardDegradedText(boardEnvelope.source, boardEnvelope.degradedReason)
    : null;

  return (
    <Card
      title="A股热门板块"
      size="small"
      extra={
        // 跳转必须给真 href（<Link>）：非语义 <a onClick> 键盘不可达、读屏不报链接
        <Link to={`/market/hot-sectors/${category}`}>查看全部</Link>
      }
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Segmented
          block
          value={category}
          options={HOT_BOARD_CATEGORIES.map((item) => ({ label: item.label, value: item.key }))}
          onChange={(value) => setCategory(value as HotBoardCategory)}
        />
        <Spin spinning={isLoading}>
          <FreshnessNote asOf={boardEnvelope?.asOf} quality={boardEnvelope?.asOfQuality} />
          {degradedText ? (
            <Tag color="warning" style={{ marginTop: 4 }}>
              {degradedText}
            </Tag>
          ) : null}
          <List
            size="small"
            dataSource={rows}
            renderItem={(item) => {
              // 行内容是共用骨架；「可下钻」与否只决定外层用哪种语义元素。
              const content = (
                <div className="hot-board-row">
                  <Typography.Text strong style={{ width: 96 }}>{item.name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ width: 52 }}>{item.code}</Typography.Text>
                  <ChangeText value={item.changePercent} style={{ width: 76 }} />
                  <Typography.Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
                    上涨 {item.upCount} | 平盘 {item.flatCount} | 下跌 {item.downCount}
                  </Typography.Text>
                  <div style={{ display: "flex", gap: 4 }}>
                    {(item.leaders ?? []).slice(0, 2).map((s) => (
                      <Tag key={s.symbol} color={s.changePercent > 0 ? "red" : "green"} style={{ margin: 0, fontSize: 11 }}>
                        {s.name} {s.changePercent > 0 ? "+" : ""}{s.changePercent.toFixed(2)}%
                      </Tag>
                    ))}
                  </div>
                </div>
              );
              return (
                <List.Item style={{ padding: "8px 0" }}>
                  {item.code ? (
                    // 打开抽屉是**原地展开**，不改变 URL：语义上是按钮而不是链接（Task 16）。
                    // 真 <button> 自带 Tab 聚焦 + Enter/Space 触发，不需要手写 onKeyDown。
                    <button
                      type="button"
                      className="hot-board-row__button"
                      onClick={() => setBoardTarget({ code: item.code, name: item.name })}
                    >
                      {content}
                    </button>
                  ) : (
                    // 回落本地分组时 `code` 为空串：没有真实板块码就没有可下钻的成分股。
                    // 不渲染成按钮——不可点的行不该被读屏报成「有操作」。
                    content
                  )}
                </List.Item>
              );
            }}
          />
        </Spin>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {getHotBoardCategoryLabel(category)}：点击条目可查看该板块成分股。
        </Typography.Text>
      </Space>
      <BoardDrilldownDrawer
        board={boardTarget}
        open={boardTarget !== null}
        onClose={() => setBoardTarget(null)}
      />
    </Card>
  );
}
