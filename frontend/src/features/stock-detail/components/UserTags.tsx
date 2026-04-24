import { useState } from "react";
import { Button, Input, Space, Spin, Tag, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  addStockUserTag,
  fetchStockUserTags,
  removeStockUserTag,
} from "@/shared/api/userTags";
import type { Exchange } from "@/shared/types";

interface Props {
  exchange: Exchange;
  symbol: string;
}

const STALE_TIME = 5 * 60 * 1000;

export function UserTags({ exchange, symbol }: Props) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [inputVisible, setInputVisible] = useState(false);
  const [inputValue, setInputValue] = useState("");

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ["stock-user-tags", exchange, symbol],
    queryFn: () => fetchStockUserTags(exchange, symbol),
    staleTime: STALE_TIME,
  });

  const addMutation = useMutation({
    mutationFn: (tagName: string) => addStockUserTag(exchange, symbol, tagName),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["stock-user-tags", exchange, symbol],
      });
      await queryClient.invalidateQueries({ queryKey: ["all-tags"] });
      message.success("标签已添加");
      setInputValue("");
      setInputVisible(false);
    },
    onError: () => {
      message.error("添加失败");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (tagName: string) => removeStockUserTag(exchange, symbol, tagName),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["stock-user-tags", exchange, symbol],
      });
      await queryClient.invalidateQueries({ queryKey: ["all-tags"] });
      message.success("标签已删除");
    },
    onError: () => {
      message.error("删除失败");
    },
  });

  const handleInputConfirm = () => {
    const value = inputValue.trim();
    if (value && !tags.some((t) => t.tag_name === value)) {
      addMutation.mutate(value);
    } else {
      setInputVisible(false);
      setInputValue("");
    }
  };

  const handleTagClick = (tagName: string) => {
    navigate(`/tags/${encodeURIComponent(tagName)}`);
  };

  if (isLoading) return <Spin size="small" />;

  return (
    <Space size={4} wrap>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        自定义标签：
      </Typography.Text>
      {tags.length === 0 && !inputVisible && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          暂无
        </Typography.Text>
      )}
      {tags.map((t) => (
        <Tag
          key={t.tag_name}
          color="green"
          closable
          onClose={(e) => {
            e.preventDefault();
            removeMutation.mutate(t.tag_name);
          }}
          style={{ cursor: "pointer" }}
          onClick={() => handleTagClick(t.tag_name)}
        >
          {t.tag_name}
        </Tag>
      ))}
      {inputVisible ? (
        <Input
          size="small"
          style={{ width: 100 }}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onBlur={handleInputConfirm}
          onPressEnter={handleInputConfirm}
          autoFocus
          maxLength={64}
          placeholder="标签名"
        />
      ) : (
        <Button
          type="dashed"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setInputVisible(true)}
        >
          添加
        </Button>
      )}
    </Space>
  );
}
