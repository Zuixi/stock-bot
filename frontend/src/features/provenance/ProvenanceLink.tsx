import { useState } from "react";
import { InfoCircleOutlined } from "@ant-design/icons";
import { Button } from "antd";

import { ProvenanceDrawer } from "./ProvenanceDrawer";

interface Props {
  metricKey: string;
  asOf?: string | null;
  scopeKey?: string | null;
  /** 屏幕阅读器与 tooltip 文案；缺省用 `metricKey`。 */
  label?: string;
  size?: "small" | "middle";
}

/**
 * 一个"看这个数字怎么来的"入口（P7）。
 *
 * 刻意做成独立的小按钮而不是把数字包一层手势：行情页的数字已经承载了
 * 跳转/十字光标等交互，叠加点击区会互相抢事件。放在标题/脚注旁不改变原有布局。
 */
export function ProvenanceLink({ metricKey, asOf, scopeKey, label, size = "small" }: Props) {
  const [open, setOpen] = useState(false);
  const text = label ?? `查看 ${metricKey} 的数据溯源`;
  return (
    <>
      <Button
        type="text"
        size={size}
        aria-label={text}
        title={text}
        icon={<InfoCircleOutlined />}
        onClick={() => setOpen(true)}
        style={{ opacity: 0.55, paddingInline: 4 }}
        data-testid={`provenance-link-${metricKey}`}
      />
      <ProvenanceDrawer
        open={open}
        metricKey={metricKey}
        asOf={asOf}
        scopeKey={scopeKey}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
