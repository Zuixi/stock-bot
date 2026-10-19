import { useMemo, useState } from "react";
import { Button, Modal, Select, Space, Spin, Tag, Typography, message } from "antd";
import { EditOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchSwOptions, fetchStockSwTags, updateStockSwTags } from "@/shared/api/swIndustry";
import type { Exchange } from "@/shared/types";

interface Props {
  exchange: Exchange;
  symbol: string;
  /**
   * 渲染形态：
   * - `full`（默认）：标签前缀 + chips + 编辑入口，自成一块（历史行为，其他调用方不受影响）；
   * - `controls`：**只出「编辑」按钮 + 弹窗**。chips 由 `StockTagRow` 与其它两组标签合并到
   *   同一行统一渲染（F14：三块不再各占一行）。此形态仍用同一个 query key
   *   `["stock-sw-tags", exchange, symbol]`，与 `StockTagRow` 共享缓存，**不新增请求**。
   */
  variant?: "full" | "controls";
}

const TAG_COLORS: Record<number, string> = { 2: "blue", 3: "purple" };
const STALE_TIME = 5 * 60 * 1000;

export function CustomSwTags({ exchange, symbol, variant = "full" }: Props) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);

  const { data: tags = [], isLoading, isError } = useQuery({
    queryKey: ["stock-sw-tags", exchange, symbol],
    queryFn: () => fetchStockSwTags(exchange, symbol),
    staleTime: STALE_TIME,
  });

  /**
   * 当前选中项是否可信（= 后端真的回了一份"现有标签"）。
   *
   * 加载中与**失败**都不可信：两者的 `tags` 都是默认值 `[]`，拿它当弹窗初始值再提交，
   * PUT 出去的 `industry_codes: []` 会把用户已有的申万分类清空（全量覆盖语义）。
   * 失败态不是"额外发挥"：它和加载中是同一条数据销毁路径，只堵一半等于没堵。
   */
  const tagsReady = !isLoading && !isError;

  const { data: l2Options = [] } = useQuery({
    queryKey: ["sw-options", 2],
    queryFn: () => fetchSwOptions(2),
    staleTime: STALE_TIME,
    enabled: open,
  });

  const { data: l3Options = [] } = useQuery({
    queryKey: ["sw-options", 3],
    queryFn: () => fetchSwOptions(3),
    staleTime: STALE_TIME,
    enabled: open,
  });

  const mutation = useMutation({
    mutationFn: (codes: string[]) => updateStockSwTags(exchange, symbol, codes),
    onSuccess: async (newTags) => {
      queryClient.setQueryData(["stock-sw-tags", exchange, symbol], newTags);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["market", "sw-industry-tree"] }),
        queryClient.invalidateQueries({ queryKey: ["sw-level1-stocks"] }),
        queryClient.invalidateQueries({ queryKey: ["sw-level2-stocks"] }),
        queryClient.invalidateQueries({ queryKey: ["sw-level3-stocks"] }),
      ]);
      message.success("分类标签已更新");
      setOpen(false);
    },
    onError: () => {
      message.error("更新失败，请重试");
    },
  });

  /**
   * ⚠️ 选中项不可信时**不得**开弹窗（否则会清空用户标签）。
   *
   * 弹窗的初始选中项来自 `tags` 的快照。查询还没落地（或已失败）时 `tags` 是默认值 `[]`，
   * 于是弹窗以"一个都没选"打开，用户点确定就 PUT `industry_codes: []`
   * （`updateStockSwTags` 是全量覆盖）—— 一次误点就把这个人手动挂的申万分类抹掉。
   * 旧的全量形态靠 `if (isLoading) return <Spin/>` 把这条路径挡住；`controls` 形态只剩按钮，
   * 没有那层兜底，所以这里显式早退，按钮侧同时置 `disabled`（双保险：键盘/程序化触发也进不来）。
   */
  const handleOpen = () => {
    if (!tagsReady) return;
    setSelectedCodes(tags.map((t) => t.industryCode));
    setOpen(true);
  };

  const handleSave = () => {
    mutation.mutate(selectedCodes);
  };

  const l2Values = selectedCodes.filter((c) => l2Options.some((o) => o.code === c));
  const selectedL2CodeSet = useMemo(() => new Set(l2Values), [l2Values]);
  const filteredL3Options = useMemo(
    () => l3Options.filter((o) => o.parentCode && selectedL2CodeSet.has(o.parentCode)),
    [l3Options, selectedL2CodeSet]
  );
  const filteredL3CodeSet = useMemo(
    () => new Set(filteredL3Options.map((o) => o.code)),
    [filteredL3Options]
  );

  const l3Values = selectedCodes.filter((c) => filteredL3CodeSet.has(c));

  const handleL2Change = (codes: string[]) => {
    const nextL2Set = new Set(codes);
    const allowedL3CodeSet = new Set(
      l3Options
        .filter((o) => o.parentCode && nextL2Set.has(o.parentCode))
        .map((o) => o.code)
    );
    const nextL3 = selectedCodes.filter((code) => allowedL3CodeSet.has(code));

    setSelectedCodes([...codes, ...nextL3]);
  };

  const handleL3Change = (codes: string[]) => {
    setSelectedCodes([...l2Values, ...codes]);
  };

  // `controls` 形态下加载中不需要骨架：chips 由 StockTagRow 渲染，这里只关心按钮。
  // 但按钮必须**禁用**（见 handleOpen 的注释：选中项不可信时开弹窗会以空选中覆盖用户标签）。
  if (isLoading && variant === "full") return <Spin size="small" />;

  const editButton = (
    <Button
      type="link"
      size="small"
      icon={<EditOutlined />}
      disabled={!tagsReady}
      // `tagsReady === false`（加载中或**失败**）时按钮不可点，必须说明原因：
      // 置灰而无解释会被读成"这个功能坏了"，而真实原因只是标签还没就绪。
      title={tagsReady ? undefined : "标签未就绪，稍后重试"}
      onClick={handleOpen}
    >
      编辑
    </Button>
  );

  return (
    <>
      {variant === "controls" ? (
        editButton
      ) : (
        <Space size={4} wrap>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            自定义分类：
          </Typography.Text>
          {tags.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              暂无
            </Typography.Text>
          )}
          {tags.map((t) => (
            <Tag key={t.industryCode} color={TAG_COLORS[t.level] ?? "default"}>
              {t.industryName}
            </Tag>
          ))}
          {editButton}
        </Space>
      )}

      <Modal
        title="编辑自定义申万分类"
        open={open}
        onOk={handleSave}
        onCancel={() => setOpen(false)}
        confirmLoading={mutation.isPending}
        width={520}
        destroyOnClose
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Typography.Text strong>申万二级行业</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              placeholder="选择二级行业（可多选）"
              style={{ width: "100%", marginTop: 8 }}
              value={l2Values}
              onChange={handleL2Change}
              options={l2Options.map((o) => ({ label: o.name, value: o.code }))}
              filterOption={(input, option) =>
                (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
              }
              showSearch
            />
          </div>
          <div>
            <Typography.Text strong>申万三级行业</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              placeholder={l2Values.length === 0 ? "请先选择二级行业" : "选择三级行业（可多选）"}
              style={{ width: "100%", marginTop: 8 }}
              value={l3Values}
              onChange={handleL3Change}
              options={filteredL3Options.map((o) => ({ label: o.name, value: o.code }))}
              filterOption={(input, option) =>
                (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
              }
              showSearch
              disabled={l2Values.length === 0}
            />
          </div>
        </Space>
      </Modal>
    </>
  );
}
