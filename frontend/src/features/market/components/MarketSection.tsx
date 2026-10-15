import type { ReactNode } from "react";
import "./MarketSection.css";

/**
 * 行情中心「独立分区」壳体（P1-1 第一刀）。
 *
 * 背景：`/market` 的 5 个 Tab 是**页级**分类，一个 Tab 只讲一个视角；对标产品的行情页
 * 是**一堆各自独立的分区**，每个分区自带本地维度条 —— 一屏能同时读到多个视角。
 * 本组件把那层"分区"抽象出来，供**逐区**迁移：先落 A股全景第一行，其余分区后续按同一
 * 形状平移（不重建页面、不动页级 Tab）。
 *
 * 刻意保持最小（不做框架）：
 * - 只负责「分区标题行 + 标题右侧 extra 槽 + 本地维度条槽 + 内容槽」四件事；
 * - **不套 antd `Card`**：分区内的面板各自就是 `Card`（自带卡头与 as_of 行），
 *   再套一层会变成卡中卡、还会把面板的 `extra`（成交额 / 查看全部）挤进分区头；
 * - **不接管 as_of**：口径纪律是"每个面板给自己的数据截至"（见 `FreshnessNote`），
 *   分区层如果也印一个 as_of，就会出现两个不同来源的"数据截至"（本文件明令不做）。
 */
export interface MarketSectionProps {
  /** 分区标题（分区内**唯一**一处标题文案，面板自己的卡头标题不重复它） */
  title: string;
  /** 标题右侧**原样**内容：口径说明文字、分区级链接等（不包一层 antd `extra` 排版） */
  extra?: ReactNode;
  /**
   * 本地维度条（对标产品每个分区自带的那条）：分区级筛选/口径/视图切换放这里。
   * 分区之间**互不影响** —— 这正是"一个屏幕多个视角"的来源。
   * 纯展示的图例不该用这个槽（会把"可切换"读成"可切换"）。
   */
  dimensionStrip?: ReactNode;
  /** 分区内容（通常是 `<Row gutter={[16, 16]}>` + 若干 `<Col>`） */
  children: ReactNode;
  /** 锚点 id / 结构锚点（e2e 用它限定作用域，而非全局 `getByText`） */
  id?: string;
  testId?: string;
}

export function MarketSection({
  title,
  extra,
  dimensionStrip,
  children,
  id,
  testId,
}: MarketSectionProps) {
  return (
    <section className="market-section" id={id} data-testid={testId}>
      <header className="market-section__head">
        <h2 className="market-section__title">{title}</h2>
        {extra ? <div className="market-section__extra">{extra}</div> : null}
      </header>
      {dimensionStrip ? (
        <div className="market-section__dimensions" data-testid={testId ? `${testId}-dimensions` : undefined}>
          {dimensionStrip}
        </div>
      ) : null}
      <div className="market-section__body">{children}</div>
    </section>
  );
}
