import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Breadcrumb, Card, Input, Segmented, Space, Table, Tag, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType, TableProps } from "antd/es/table";
import { ChangeText, NumberText } from "@/shared/ui";
import {
  fetchHotBoards,
  type HotBoardCategory,
  type HotBoardItem,
} from "@/shared/api/market";
import { hotBoardDegradedText } from "@/shared/api/marketEnvelope";
import { useMarketPolling } from "@/features/market/hooks/useMarketPolling";
import {
  BoardDrilldownDrawer,
  type BoardDrilldownTarget,
} from "@/features/market/components/BoardDrilldownDrawer";

const HOT_BOARD_CATEGORIES: { key: HotBoardCategory; label: string }[] = [
  { key: "industry", label: "行业板块" },
  { key: "concept", label: "概念板块" },
  { key: "region", label: "地域板块" },
];

function getHotBoardCategoryLabel(category: HotBoardCategory): string {
  return HOT_BOARD_CATEGORIES.find((item) => item.key === category)?.label ?? "热门板块";
}

type SortState = {
  sortBy?: keyof HotBoardItem;
  sortOrder?: "asc" | "desc";
};

function isValidCategory(value: string): value is HotBoardCategory {
  return value === "industry" || value === "concept" || value === "region";
}

function sortRows(rows: HotBoardItem[], sort: SortState): HotBoardItem[] {
  if (!sort.sortBy) return rows;
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const av = a[sort.sortBy!] ?? 0;
    const bv = b[sort.sortBy!] ?? 0;
    return av > bv ? direction : av < bv ? -direction : 0;
  });
  return sorted;
}

export default function MarketHotSectorsPage() {
  const navigate = useNavigate();
  const { category = "industry" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeCategory: HotBoardCategory = isValidCategory(category) ? category : "industry";
  const [sort, setSort] = useState<SortState>({ sortBy: "changePercent", sortOrder: "desc" });
  const [keyword, setKeyword] = useState("");
  const [boardTarget, setBoardTarget] = useState<BoardDrilldownTarget | null>(null);

  const { refetchInterval } = useMarketPolling();
  // 与 /market 的「A股热门板块」卡共用 key：同一端点全局只有一个缓存条目（此前
  // `hot-boards-page` 与 `hot-boards` 双 key → 双请求、两份可能漂移的缓存）
  const { data: boardEnvelope } = useQuery({
    queryKey: ["market", "hot-boards", activeCategory],
    queryFn: () => fetchHotBoards(activeCategory),
    refetchInterval,
  });
  const boardRows = boardEnvelope?.items ?? [];
  const selectedBoardCode = searchParams.get("board");
  const degradedText = boardEnvelope
    ? hotBoardDegradedText(boardEnvelope.source, boardEnvelope.degradedReason)
    : null;

  const filteredRows = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return boardRows;
    return boardRows.filter(
      (item) => item.name.toLowerCase().includes(q) || item.code.toLowerCase().includes(q),
    );
  }, [boardRows, keyword]);
  const rows = useMemo(() => sortRows(filteredRows, sort), [filteredRows, sort]);

  const columns: ColumnsType<HotBoardItem> = [
    {
      title: "板块名称",
      key: "name",
      width: 220,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.code || "--"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "板块涨跌幅",
      dataIndex: "changePercent",
      width: 120,
      sorter: true,
      render: (value: number) => <ChangeText value={value} />,
    },
    {
      title: "成交额",
      dataIndex: "amount",
      width: 130,
      sorter: true,
      render: (value: number | null | undefined) => <NumberText value={value} unit="cap" />,
    },
    {
      title: "主力净额",
      dataIndex: "mainNetInflow",
      width: 130,
      sorter: true,
      render: (value: number | null | undefined) => <NumberText value={value} unit="cap" />,
    },
    {
      title: "上涨家数",
      dataIndex: "upCount",
      width: 100,
      sorter: true,
    },
    {
      title: "平盘家数",
      dataIndex: "flatCount",
      width: 100,
      sorter: true,
    },
    {
      title: "下跌家数",
      dataIndex: "downCount",
      width: 100,
      sorter: true,
    },
    {
      title: "领涨股",
      key: "leaders",
      render: (_value, record) => (
        <Space wrap>
          {(record.leaders ?? []).map((stock) => (
            <Tag key={stock.symbol} color={stock.changePercent >= 0 ? "red" : "green"}>
              {stock.name} {stock.changePercent > 0 ? "+" : ""}
              {stock.changePercent.toFixed(2)}%
            </Tag>
          ))}
        </Space>
      ),
    },
  ];

  const onTableChange: TableProps<HotBoardItem>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof HotBoardItem,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <a onClick={() => navigate("/market")}>市场</a> },
          { title: "A股热门板块" },
          { title: getHotBoardCategoryLabel(activeCategory) },
        ]}
      />

      <Card
        title="A股热门板块"
        size="small"
        extra={<Typography.Text type="secondary">点击行可查看该板块成分股</Typography.Text>}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Segmented
            block
            value={activeCategory}
            options={HOT_BOARD_CATEGORIES.map((item) => ({ label: item.label, value: item.key }))}
            onChange={(value) => {
              // 切分类保留 `?board=`：选中板块切走再切回时高亮不能丢（"where meaningful"——
              // 新分类里是否存在该 code 由行高亮自行判定，参数只负责不丢身份）。
              const query = selectedBoardCode ? `?board=${selectedBoardCode}` : "";
              navigate(`/market/hot-sectors/${value as HotBoardCategory}${query}`);
            }}
          />

          {degradedText ? (
            <Tag color="warning" style={{ margin: 0 }}>
              {degradedText}
            </Tag>
          ) : null}

          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索板块名称或代码"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            style={{ maxWidth: 320 }}
          />

          <Table<HotBoardItem>
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            onChange={onTableChange}
            rowClassName={(record) =>
              selectedBoardCode && selectedBoardCode === record.code ? "ant-table-row-selected" : ""
            }
            onRow={(record) => ({
              style: { cursor: record.code ? "pointer" : "default" },
              onClick: () => {
                // 回落本地分组的空 code 无可下钻身份：既不高亮也不打开抽屉
                if (!record.code) return;
                setBoardTarget({ code: record.code, name: record.name });
                setSearchParams({ board: record.code });
              },
            })}
            scroll={{ x: 1100 }}
          />
        </Space>
      </Card>

      <BoardDrilldownDrawer
        board={boardTarget}
        open={boardTarget !== null}
        onClose={() => setBoardTarget(null)}
      />
    </Space>
  );
}
