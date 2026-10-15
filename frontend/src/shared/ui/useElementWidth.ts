import { useEffect, useRef, useState } from "react";

/**
 * 观察元素自身的**内容盒宽度**（`ResizeObserver` 封装）。
 *
 * 用途：窄屏下"图表还要不要画、画多少个点"必须由**容器实际宽度**决定，而不是视口断点。
 *
 * 为什么不用视口断点（计划 §0.1 B2 修订的落法）：同一个组件在 390px 与 1440px 下都可能
 * 只拿到 ~90px（`.global-index-card__spark` 的 `max-width: 88px`），视口断点区分不出这个
 * 差别 ⇒ 会写出"宽屏 40 点、窄屏 8 点"这种**看起来对、实际与容器无关**的规则。
 *
 * 首帧 `width === 0` 是**已知状态而非错误**：此时调用方应回落到自己的默认点数，
 * 等 `ResizeObserver` 第一次回调（本轮布局结束即触发）再收敛到实测宽度。
 */
export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 先同步读一次：`ResizeObserver` 的回调是异步的，纯等回调会让首帧画错点位。
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      // 亚像素抖动（同一布局下 88.0 → 87.98）不该触发重新下采样
      setWidth((prev) => (Math.abs(prev - w) < 1 ? prev : w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return { ref, width };
}
