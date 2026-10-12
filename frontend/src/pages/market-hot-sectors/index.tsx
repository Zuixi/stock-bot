import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Breadcrumb, Card, Input, Segmented, Space, Table, Tag, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType, TableProps } from "antd/es/table";
import { ChangeText, DEGRADED_REASON_TEXT, NumberText, formatCnDate } from "@/shared/ui";
import {
  fetchHotBoards,
  type HotBoardCategory,
  type HotBoardItem,
} from "@/shared/api/market";
import { hotBoardDegradedText } from "@/shared/api/marketEnvelope";
import { fetchConceptList, type ConceptBoardItem } from "@/shared/api/concept";
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

/**
 * 列表行。行业/地域来自东财实时信封（`HotBoardItem`）；概念分类来自本地聚合
 * `GET /api/v1/concepts`（`ConceptBoardItem`），其中 `avg_pct` 可为 null（该板当日无可用行情），
 * 故把 `changePercent` 放宽为可空——渲染交给 `<ChangeText>` 的 `--` 分支，**绝不 0 填充**
 * （0 会被读成「平盘」，而 null 是「不可判」）。`amount` 概念侧没有对应字段 → null。
 */
type HotBoardRow = Omit<HotBoardItem, "changePercent"> & { changePercent: number | null };

type SortState = {
  sortBy?: keyof HotBoardRow;
  sortOrder?: "asc" | "desc";
};

function isValidCategory(value: string): value is HotBoardCategory {
  return value === "industry" || value === "concept" || value === "region";
}

/**
 * 缺失值（`null`/`undefined`）**恒排最后**，升序降序都一样。
 *
 * 不能拿 0 当占位：`changePercent: null` 是「不可判」，不是「平盘 0.00%」——按 0 参与降序
 * 会把无行情的板块插在下跌板块之前，等于用排序谎报它的相对强弱（评审 Important）。
 * 其余（tie / 数值序）语义不变。
 */
function sortRows(rows: HotBoardRow[], sort: SortState): HotBoardRow[] {
  if (!sort.sortBy) return rows;
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  const missing = direction < 0 ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY;
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const av = a[sort.sortBy!] ?? missing;
    const bv = b[sort.sortBy!] ?? missing;
    return av > bv ? direction : av < bv ? -direction : 0;
  });
  return sorted;
}

/**
 * `ConceptBoardItem` → 列表行：逐列只映射真实字段，没有对应的列（成交额）显式 `null`。
 *
 * 领涨股里 `change_percent` 为 null 的占位成分**过滤掉**（`<Tag>` 的 `toFixed` 会崩，且渲染 0
 * 是谎报「平盘」）：缺失就是缺失，宁可该列少一个 tag，也不 0 填充。
 */
function conceptItemToRow(item: ConceptBoardItem): HotBoardRow {
  return {
    id: `concept-${item.board_code}`,
    name: item.board_name,
    code: item.board_code,
    changePercent: item.avg_pct, // null = 无可用行情 → `<ChangeText>` 渲染 `--`
    upCount: item.up_count,
    flatCount: item.flat_count,
    downCount: item.down_count,
    leaders: item.leaders.flatMap((leader) =>
      leader.change_percent == null
        ? []
        : [{ symbol: leader.symbol, name: leader.name, changePercent: leader.change_percent }]
    ),
    mainNetInflow: item.main_net_inflow, // 东财快照缺行 → null → `--`
    mainNetRatio: item.main_net_ratio,
    amount: null, // 本地聚合不产成交额（概念侧没有这个字段）→ `--`
  };
}

/** 概念列表降级文案：复用全站 `degraded_reason` 词表，未知词原样上屏（便于发现契约漂移）。 */
function conceptDegradedText(reason: string | null): string | null {
  if (!reason) return null;
  return DEGRADED_REASON_TEXT[reason] ?? reason;
}

// 概念列表是 T-1 本地聚合 + 服务端 300s 缓存：用 300s 轮询对齐缓存 TTL，
// **不继承** `useMarketPolling` 的实时间隔（那是东财板块路径的节奏）。
const CONCEPT_LIST_REFETCH_MS = 300_000;

export default function MarketHotSectorsPage() {
  const navigate = useNavigate();
  const { category = "industry" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeCategory: HotBoardCategory = isValidCategory(category) ? category : "industry";
  const isConcept = activeCategory === "concept";
  const [sort, setSort] = useState<SortState>({ sortBy: "changePercent", sortOrder: "desc" });
  const [keyword, setKeyword] = useState("");
  const [boardTarget, setBoardTarget] = useState<BoardDrilldownTarget | null>(null);

  const { refetchInterval } = useMarketPolling();
  // 与 /market 的「A股热门板块」卡共用 key：同一端点全局只有一个缓存条目（此前
  // `hot-boards-page` 与 `hot-boards` 双 key → 双请求、两份可能漂移的缓存）
  // 行业/地域保持东财实时板块信封（`main` 行为）；概念分类改走本地聚合列表端点：
  // 东财 `pz` 上限 100 且 `_hot_board_items_from_eastmoney` 只切 Top-10，「查看全部」在这里才兑现。
  // 两个查询 `enabled` 互斥，分类切换不会同时打两个端点。
  const { data: boardEnvelope } = useQuery({
    queryKey: ["market", "hot-boards", activeCategory],
    queryFn: () => fetchHotBoards(activeCategory),
    enabled: !isConcept,
    refetchInterval,
  });
  const {
    data: conceptEnvelope,
    isError: conceptIsError,
  } = useQuery({
    queryKey: ["market", "concepts", "list"],
    queryFn: () => fetchConceptList(),
    enabled: isConcept,
    refetchInterval: CONCEPT_LIST_REFETCH_MS,
  });

  const conceptRows = useMemo(
    () => (conceptEnvelope?.items ?? []).map(conceptItemToRow),
    [conceptEnvelope],
  );
  const boardRows: HotBoardRow[] = isConcept ? conceptRows : (boardEnvelope?.items ?? []);
  const selectedBoardCode = searchParams.get("board");
  const degradedText = isConcept
    ? conceptIsError
      ? "概念数据加载失败，稍后重试"
      : conceptDegradedText(conceptEnvelope?.degraded_reason ?? null)
    : boardEnvelope
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

  const columns: ColumnsType<HotBoardRow> = [
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
      render: (value: number | null) => <ChangeText value={value} />,
    },
    {
      title: "成交额",
      dataIndex: "amount",
      key: "amount",
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

  // 概念侧本地聚合不产成交额（`amount` 恒为 null）：整列隐藏，而不是留一个点了没反应的
  // 排序表头（假交互比少一列更糟）。行业/地域仍由东财信封提供成交额，保持原样。
  const visibleColumns = isConcept ? columns.filter((column) => column.key !== "amount") : columns;

  const onTableChange: TableProps<HotBoardRow>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof HotBoardRow,
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

          {/* 概念分类口径披露（契约要求，不是装饰）：东财实时板块 ≠ 本地成分聚合。行情判据日
              与**成分快照日**是两个独立口径，必须同时上屏；成分名录来自东财 clist，每日刷新。
              列表页的 `membership_as_of` 是**全库**成分表的最大快照日，可能比某个板块自己的
              快照更新 —— 故此处标注「全库」，不得读成「本板块成分截至该日」（详情页逐板口径
              在 `/market/concept/:code`，措辞不同）。 */}
          {isConcept && conceptEnvelope ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              按本地成分聚合 · 行情截至{" "}
              {conceptEnvelope.as_of ? formatCnDate(conceptEnvelope.as_of) : "--"} · 成分快照{" "}
              {conceptEnvelope.membership_as_of
                ? formatCnDate(conceptEnvelope.membership_as_of)
                : "--"}
              （全库 · 东财）
            </Typography.Text>
          ) : null}

          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索板块名称或代码"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            style={{ maxWidth: 320 }}
          />

          <Table<HotBoardRow>
            size="small"
            rowKey="id"
            columns={visibleColumns}
            dataSource={rows}
            pagination={{
              pageSize: 10,
              showSizeChanger: false,
              // 概念分类的分页总数用信封的 `total`（全库启用板块数，与分页无关）：这是「查看全部」
              // 的兑现证明。有搜索词 / 降级空页时退回行数，否则分页器会指向并不存在的页。
              total:
                keyword.trim() || !conceptEnvelope?.items.length
                  ? rows.length
                  : conceptEnvelope.total,
            }}
            onChange={onTableChange}
            rowClassName={(record) =>
              selectedBoardCode && selectedBoardCode === record.code ? "ant-table-row-selected" : ""
            }
            onRow={(record) => ({
              style: { cursor: record.code ? "pointer" : "default" },
              onClick: () => {
                // 回落本地分组的空 code 无可下钻身份：既不高亮也不打开抽屉
                if (!record.code) return;
                // 概念分类走概念详情页（落库成分 + 连板 + 板内梯队 + 涨停 KPI），**不开抽屉**；
                // 行业/地域没有详情页，保持抽屉下钻（main 行为）。
                if (isConcept) {
                  navigate(`/market/concept/${record.code}`);
                  return;
                }
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
