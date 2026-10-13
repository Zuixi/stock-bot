import { MODULE_INDEX } from "../moduleTable";

/**
 * 左侧粘性模块索引（**不是 Tabs**）。
 *
 * 为什么是锚点索引而不是 Tab：本页要的是"一屏之内把 10 个模块按提问顺序读完 + 能落到深链"，
 * Tab 会把模块藏起来（同一时刻只能看一个），与"深度读出台"的定位相反。
 * 索引的成员来自 `MODULE_INDEX`（与模块表同源），故"漏配一个模块"不会发生。
 *
 * 链接是纯锚点 `<a href="#...">`，不带 `→` 装饰，与页面滚动共用 `scroll-margin-top`。
 */
export function SentimentModuleIndex() {
  return (
    <nav className="sent-index" data-testid="sentiment-module-index" aria-label="情绪分析模块索引">
      {MODULE_INDEX.map((group) => (
        <div className="sent-index__group" key={group.key}>
          <p className="sent-index__label">{group.label}</p>
          <ul className="sent-index__list">
            {group.items.map((item) => (
              <li key={item.key}>
                <a className="sent-index__link" href={`#${item.anchor}`}>
                  {item.title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
