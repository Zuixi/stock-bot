import { AutoComplete, Input } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { MOCK_STOCKS } from "@/shared/mocks/stocks";

export function SearchBar() {
  const [keyword, setKeyword] = useState("");
  const navigate = useNavigate();

  const options = useMemo(() => {
    if (!keyword.trim()) return [];
    const kw = keyword.trim().toLowerCase();
    return MOCK_STOCKS
      .filter((s) => s.symbol.includes(kw) || s.name.toLowerCase().includes(kw))
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
  }, [keyword]);

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
