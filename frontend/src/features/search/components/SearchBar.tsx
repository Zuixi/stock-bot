import { AutoComplete, Input } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchStocksMerged } from "@/shared/api/stocks";

export function SearchBar() {
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(keyword.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  const { data: candidates = [] } = useQuery({
    queryKey: ["search-stocks", debouncedKeyword],
    queryFn: () => fetchStocksMerged({ keyword: debouncedKeyword, page_size: 30 }),
    enabled: debouncedKeyword.length > 0,
  });

  const options = useMemo(() => {
    if (!debouncedKeyword) return [];
    const kw = debouncedKeyword.toLowerCase();
    return candidates
      .filter((s) => s.symbol.toLowerCase().includes(kw) || s.name.toLowerCase().includes(kw))
      .slice(0, 8)
      .map((s) => ({
        value: s.symbol,
        label: (
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{s.name}</span>
            <span style={{ color: "#999", fontFamily: "monospace" }}>{s.symbol}</span>
          </div>
        ),
      }));
  }, [debouncedKeyword, candidates]);

  return (
    <AutoComplete
      options={options}
      onSelect={(val) => {
        navigate(`/stock/${val}`);
        setKeyword("");
      }}
      onSearch={setKeyword}
      value={keyword}
      style={{ width: 220 }}
    >
      <Input
        prefix={<SearchOutlined />}
        placeholder="搜索代码/名称"
        allowClear
        size="middle"
      />
    </AutoComplete>
  );
}
