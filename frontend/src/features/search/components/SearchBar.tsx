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

  const { data: candidatesResponse } = useQuery({
    queryKey: ["search-stocks", debouncedKeyword],
    queryFn: () => fetchStocksMerged({ keyword: debouncedKeyword, page_size: 30 }),
    enabled: debouncedKeyword.length > 0,
  });
  const candidates = candidatesResponse?.items ?? [];

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
            <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{s.symbol}</span>
          </div>
        ),
      }));
  }, [debouncedKeyword, candidates]);

  return (
    // `data-testid="global-search"` 是 E2E 锚点：顶栏里"单个控件谁最宽"（P1-3 的判据）
    // 需要一个稳定的选择器。注意 `.ant-input-affix-wrapper` 是**内层输入框**，
    // 外层 `.ant-select` 才是控件本体——两者宽度现在都由调用方（`MainLayout` 的
    // 弹性容器）决定，这里不再写死像素值。
    <AutoComplete
      data-testid="global-search"
      options={options}
      onSelect={(val) => {
        navigate(`/company/${val}`);
        setKeyword("");
      }}
      onSearch={setKeyword}
      value={keyword}
      // 固定 220px：顶栏的剩余空间由**导航外壳**吃掉（导航靠右），搜索框不再弹性伸缩。
      // （P0 一度改成 `width: "100%"` 让它成为"最宽控件"，但那一版把导航收敛进了下拉；
      //   owner 要求 10 项平铺 + 导航靠右后，两者冲突，故恢复固定宽度。）
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
