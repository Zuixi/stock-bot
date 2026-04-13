import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Breadcrumb, Card, Segmented, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType, TableProps } from "antd/es/table";
import { ChangeText } from "@/shared/ui";
import {
  fetchHotBoards,
  type HotBoardCategory,
  type HotBoardItem,
} from "@/shared/api/market";

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
  const [searchParams] = useSearchParams();
  const activeCategory: HotBoardCategory = isValidCategory(category) ? category : "industry";
  const [sort, setSort] = useState<SortState>({ sortBy: "changePercent", sortOrder: "desc" });

  const { data: boardRows = [] } = useQuery({
    queryKey: ["hot-boards-page", activeCategory],
    queryFn: () => fetchHotBoards(activeCategory),
  });
  const rows = useMemo(() => sortRows(boardRows, sort), [boardRows, sort]);
  const selectedBoardCode = searchParams.get("board");

  const columns: ColumnsType<HotBoardItem> = [
    {
      title: "板块名称",
      key: "name",
      width: 220,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.code}
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
        extra={<Typography.Text type="secondary">点击上方分类可切换细分板块列表</Typography.Text>}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Segmented
            block
            value={activeCategory}
            options={HOT_BOARD_CATEGORIES.map((item) => ({ label: item.label, value: item.key }))}
            onChange={(value) => navigate(`/market/hot-sectors/${value as HotBoardCategory}`)}
          />

          <Table<HotBoardItem>
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            onChange={onTableChange}
            rowClassName={(record) => (selectedBoardCode && selectedBoardCode === record.code ? "ant-table-row-selected" : "")}
            scroll={{ x: 900 }}
          />
        </Space>
      </Card>
    </Space>
  );
}
