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

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ["stock-sw-tags", exchange, symbol],
    queryFn: () => fetchStockSwTags(exchange, symbol),
    staleTime: STALE_TIME,
  });

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

  const handleOpen = () => {
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

  // `controls` 形态下加载中不需要骨架：chips 由 StockTagRow 渲染，这里只关心按钮
  if (isLoading && variant === "full") return <Spin size="small" />;

  const editButton = (
    <Button type="link" size="small" icon={<EditOutlined />} onClick={handleOpen}>
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
