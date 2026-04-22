import { useState } from "react";
import { Button, Modal, Select, Space, Spin, Tag, Typography, message } from "antd";
import { EditOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchSwOptions, fetchStockSwTags, updateStockSwTags } from "@/shared/api/swIndustry";
import type { Exchange } from "@/shared/types";

interface Props {
  exchange: Exchange;
  symbol: string;
}

const TAG_COLORS: Record<number, string> = { 2: "blue", 3: "purple" };
const STALE_TIME = 5 * 60 * 1000;

export function CustomSwTags({ exchange, symbol }: Props) {
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
    onSuccess: (newTags) => {
      queryClient.setQueryData(["stock-sw-tags", exchange, symbol], newTags);
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
  const l3Values = selectedCodes.filter((c) => l3Options.some((o) => o.code === c));

  const handleL2Change = (codes: string[]) => {
    setSelectedCodes([...codes, ...l3Values]);
  };

  const handleL3Change = (codes: string[]) => {
    setSelectedCodes([...l2Values, ...codes]);
  };

  if (isLoading) return <Spin size="small" />;

  return (
    <>
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
        <Button type="link" size="small" icon={<EditOutlined />} onClick={handleOpen}>
          编辑
        </Button>
      </Space>

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
              placeholder="选择三级行业（可多选）"
              style={{ width: "100%", marginTop: 8 }}
              value={l3Values}
              onChange={handleL3Change}
              options={l3Options.map((o) => ({ label: o.name, value: o.code }))}
              filterOption={(input, option) =>
                (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
              }
              showSearch
            />
          </div>
        </Space>
      </Modal>
    </>
  );
}
