// S1.5 热力图性能测量的页面内探针（Playwright addInitScript 用）。
//
// 分两路取数：
//   1. `longtask`（PerformanceObserver）——规范判据的浏览器口径：>50ms 的主线程任务；
//   2. `window.__heatmapSetOptions` / `window.__heatmapPhases`——ECharts `setOption`
//      的调用次数/耗时/叶数，与 `buildTree`/聚合阶段耗时。
//
// ## 为什么 setOption 的计时要靠「共享 prototype」而不是包装 init 的返回值
//
// `echarts-for-react` 的 `initEchartsInstance()` **丢弃** `init` 的返回值，之后另外调
// `echarts.getInstanceByDom(ele)` 取实例并在它上面 `setOption`（core.js:74-95、133-143）。
// 只包 `init` 一条记录都收不到（实测日志恒为空）。
//
// 可靠做法：在**共享的** `ECharts.prototype` 上包一次 `setOption`。用一棵离屏
// `div` 起一个探针实例、取其 prototype、包好后立即 dispose——因为包的是 prototype，
// 之后应用建的所有实例都自动带上计时，且发生在 React 首次渲染之前，首帧也能计到。
//
// 触发时机：不能直接 `import("echarts")`——那会拿到与 `echarts-for-react` 不同的
// 模块实例（实测改了量不到）。改为劫持 `window.__heatmapEchartsNS` 的赋值：
// 应用侧 dev 探针（`src/features/market/heatmapPerfProbe.ts`）会把**自己的**名称空间
// 对象写进这个属性，我们在 setter 里拿到它再建探针实例，保证是同一个模块实例。
export const INSTRUMENT_SCRIPT = `(() => {
  window.__longTasks = [];
  window.__heatmapSetOptions = [];
  window.__protoState = "waiting";

  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) window.__longTasks.push({ d: e.duration, s: e.startTime });
    }).observe({ entryTypes: ["longtask"] });
  } catch (e) { /* 不支持 longtask 时如实为空 */ }

  const countLeaves = (data) => {
    if (!Array.isArray(data)) return 0;
    let n = 0;
    for (const node of data) {
      if (node && Array.isArray(node.children)) n += node.children.length;
      else n += 1;
    }
    return n;
  };

  const patch = (ns) => {
    if (window.__protoState !== "waiting") return;
    try {
      if (!document.documentElement) { window.__protoState = "no-dom"; return; }
      const el = document.createElement("div");
      el.style.cssText = "width:40px;height:40px;position:absolute;left:-9999px;top:0";
      document.documentElement.appendChild(el);
      const probe = ns.init(el);
      const proto = Object.getPrototypeOf(probe);
      const real = proto.setOption;
      proto.setOption = function (...args) {
        const series = (args[0] && args[0].series) || [];
        const leaves = series.reduce((n, s) => n + countLeaves(s && s.data), 0);
        const t0 = performance.now();
        const ret = real.apply(this, args);
        window.__heatmapSetOptions.push({ ms: Math.round(performance.now() - t0), leaves });
        return ret;
      };
      ns.dispose(el);
      el.remove();
      window.__protoState = "patched";
    } catch (e) {
      window.__protoState = "error: " + e;
    }
  };

  let stored;
  Object.defineProperty(window, "__heatmapEchartsNS", {
    configurable: true,
    get() { return stored; },
    set(v) { stored = v; patch(v); },
  });
})();`;

/** 清空观测窗口（交互前后各调一次，得到该次交互自己的长任务/setOption）。 */
export async function resetObservations(page) {
  await page.evaluate(() => {
    window.__longTasks.length = 0;
    window.__heatmapSetOptions.length = 0;
    if (window.__heatmapPhases) window.__heatmapPhases.length = 0;
  });
}

/** 汇总一次观测窗口：长任务 + setOption 归因 + 阶段耗时。 */
export async function collectPageMetrics(page) {
  return page.evaluate(() => {
    const lt = (window.__longTasks || []).map((e) => Math.round(e.d));
    const so = window.__heatmapSetOptions || [];
    const ms = so.map((s) => s.ms);
    const leaves = so.map((s) => s.leaves);
    return {
      longTasks: lt,
      longest: lt.length ? Math.max(...lt) : 0,
      over50: lt.filter((d) => d > 50).length,
      over100: lt.filter((d) => d > 100).length,
      setOptionCount: so.length,
      setOptionMs: ms,
      setOptionLeaves: leaves,
      setOptionTotalMs: ms.reduce((a, b) => a + b, 0),
      protoState: window.__protoState,
      phases: (window.__heatmapPhases || []).map((p) => ({
        name: p.name,
        ms: Math.round(p.ms),
        leaves: p.leaves,
      })),
    };
  });
}
