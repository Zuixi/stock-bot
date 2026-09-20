import { Alert, Drawer, Empty, Space, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { ChangeText, NumberText } from "@/shared/ui";
import { ApiError } from "@/shared/api/client";
import { fetchBoardStocks, type BoardStockRow } from "@/shared/api/market";

/** 抽屉要展示的板块身份。后端成分股端点**不回显** code/name，必须由调用方带入。 */
export interface BoardDrilldownTarget {
  code: string;
  name: string;
}

interface Props {
  board: BoardDrilldownTarget | null;
  open: boolean;
  onClose: () => void;
}

const BOARD_STOCKS_STALE_TIME = 60 * 1000;

/**
 * 板块成分股下钻抽屉（Task 15）。
 *
 * 三种状态必须分开处理，不得合并：
 * - **加载中** → Spin；
 * - **失败（含上游 502）** → 错误态：上游挂了不等于「板块没有成分股」，渲染空表会谎报；
 * - **成功但空** → 空态：这才是「该板块真的没有成分股」。
 *
 * query key 必须带 `board.code`：成分股响应是裸数组、不回显板块码，若共用 key，
 * 切换板块时会读到上一个板块的成分股（服务端缓存也按 code 分桶，前端 key 不能更宽）。
 */
export function BoardDrilldownDrawer({ board, open, onClose }: Props) {
  const code = board?.code ?? "";
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["market", "board-stocks", code],
    queryFn: () => fetchBoardStocks(code),
    enabled: open && code.length > 0,
    staleTime: BOARD_STOCKS_STALE_TIME,
  });

  const rows = data ?? [];
  const upstreamDown = error instanceof ApiError && error.status === 502;
  const errorDescription = upstreamDown
    ? "上游（东方财富）成分股数据不可用，请稍后重试。此处不展示空表，以免被误读为「该板块没有成分股」。"
    : "成分股数据加载失败，请稍后重试。";

  const columns: ColumnsType<BoardStockRow> = [
    {
      title: "代码",
      dataIndex: "symbol",
      width: 96,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "名称",
      dataIndex: "name",
      render: (value: string | null) => value ?? "--",
    },
    {
      title: "涨跌幅",
      dataIndex: "pctChange",
      width: 100,
      render: (value: number | null) => <ChangeText value={value} />,
    },
    {
      title: "主力净额",
      dataIndex: "mainNetInflow",
      width: 120,
      render: (value: number | null) => <NumberText value={value} unit="cap" />,
    },
  ];

  return (
    <Drawer
      title={
        board ? (
          <Space direction="vertical" size={0}>
            <Space size={8}>
              <Typography.Text strong>{board.name}</Typography.Text>
              <Tag>{board.code}</Tag>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              成分股（主力净流入降序）
            </Typography.Text>
          </Space>
        ) : (
          "板块成分股"
        )
      }
      width={560}
      open={open}
      onClose={onClose}
    >
      {isLoading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      ) : isError ? (
        <Alert
          type="error"
          showIcon
          message="成分股数据不可用"
          description={errorDescription}
          action={
            <a
              onClick={() => {
                void refetch();
              }}
            >
              重试
            </a>
          }
        />
      ) : rows.length === 0 ? (
        <Empty description="该板块暂无成分股数据" />
      ) : (
        <Table<BoardStockRow>
          size="small"
          rowKey="symbol"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      )}
    </Drawer>
  );
}
