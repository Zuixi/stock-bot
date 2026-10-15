import { useEffect, useState } from "react";

/**
 * 订阅一条媒体查询（Phase 3 §5.1 的四档断点契约）。
 *
 * 为什么不直接用 AntD 的 `Grid.useBreakpoint()`：它在 SSR/首帧返回**空对象**，
 * 首帧会把窄屏判成宽屏（或反之）并闪一下错误形态——`MainLayout` 已经因为同一个坑
 * 改用 `window.matchMedia`（见该文件 `useViewportTier` 的注释）。这里抽出同一做法，
 * 避免第二个消费方再踩一次。
 *
 * 为什么不用 `window.innerWidth` + resize 监听：媒体查询的回调在**渲染前**给出，
 * 与 CSS 断点天然同源（改断点时不会出现"CSS 已变、JS 还是旧判断"的错位）。
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(query);
    const sync = () => setMatches(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [query]);

  return matches;
}

/** 契约 §5.1 的 `<768`（移动单列档）。图表的窄屏形态统一按它判定。 */
export function useIsNarrowViewport(): boolean {
  return useMediaQuery("(max-width: 767px)");
}
