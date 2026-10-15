import { message } from "antd";

/**
 * 前端重新部署后的「旧分块 404」恢复通道。
 *
 * 背景（controller 已定因）：Vite 产物是带哈希的懒加载分块，**任何一次重新部署都会换掉哈希**。
 * 部署前就开着的标签页内存里仍是旧 bundle，用户点进一个**尚未加载过**的路由时，浏览器去请求
 * **旧哈希** → 404 → 懒加载路由组件永不挂载 → 面板静默空白 / "Failed to fetch"，
 * 无提示、无恢复路径；用户只看到"没数据"，而 nginx 缓存头、无 Service Worker、后端与库当时都是健康的。
 *
 * 恢复策略：捕获失败信号 → 明确告知用户 → **本会话内重新加载一次**（sessionStorage 守卫，不可能成环）
 * → 同会话再失败则改为持久提示「请手动刷新」，不再自动刷新。
 *
 * 分工（本文件刻意的结构）：**决策逻辑是纯函数**（`shouldReload` / `markReloaded` /
 * `isChunkLoadFailure`，零 DOM、零全局依赖），**DOM 接线只做取事件、提示、刷新**。
 * 守卫可被验证而不是埋在事件处理器里。本仓库无前端单测运行器，故由 `e2e/chunkRecovery.spec.ts`
 * 在真实浏览器里驱动这两条路径（见文件末注释）。
 */

/** 只依赖 `getItem` / `setItem`，便于测试直接传假实现（不绑定真实 `Storage`）。 */
export interface RecoveryStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** 会话级标记：本会话是否已经因分块失效自动刷新过一次（**按 origin 隔离**，storage 天然如此）。 */
export const RELOAD_FLAG_KEY = "stockbot:chunk-reloaded";

const RELOAD_NOTICE = "页面版本已更新，正在刷新…";
const MANUAL_REFRESH_NOTICE = "页面版本仍是旧版，请手动刷新（Ctrl+F5 / Cmd+Shift+R）";

/**
 * 判定：本会话是否**还允许**自动刷新。
 *
 * 纯函数 + 默认失败方向为 false：
 *   - 标记不存在 / 为空串 / 读取抛错（隐私模式、storage 被禁）→ 仍返回 true（首次失败允许刷新一次）；
 *   - 标记已是任意非空值 → false，改为提示用户手动刷新，**永不二次自动刷新** → 不可能成环。
 * 之所以在 storage 不可用时选"允许一次"而非"禁止"：一是不会成环（每次刷新至多一次，
 * 且读不到标记时同样读不到写入，语义自洽）；二是用户能真正恢复，而不是被一条永远刷不掉的错误卡住。
 */
export function shouldReload(storage: RecoveryStorage | null | undefined): boolean {
  if (!storage) return true;
  try {
    const flag = storage.getItem(RELOAD_FLAG_KEY);
    return flag === null || flag === "";
  } catch {
    return true;
  }
}

/** 记下"本会话已自动刷新过一次"。写失败（隐私模式）静默忽略：`shouldReload` 的默认方向已保证不成环。 */
export function markReloaded(storage: RecoveryStorage | null | undefined): void {
  if (!storage) return;
  try {
    storage.setItem(RELOAD_FLAG_KEY, "1");
  } catch {
    /* 写入被拒：不阻塞恢复流程 */
  }
}

/**
 * 文本匹配"分块加载失败"这一类错误。
 *
 * 浏览器/打包器的报错文案不受契约保护，因此只做保守匹配，并且：
 *   - 在 `unhandledrejection` 兜底路径上**必须**先过这一关（本应用还有正常的网络调用在 reject，
 *     不放宽匹配就会把后端 5xx 误判成"版本更新"，无谓地刷掉用户正在填的表单）；
 *   - 在 `vite:preloadError` 路径上它只是诊断标签（事件本身就是权威信号）。
 */
const CHUNK_FAILURE_PATTERNS: readonly RegExp[] = [
  /dynamically imported module/i, // Chrome / Edge：Failed to fetch dynamically imported module
  /error loading dynamically imported module/i, // Firefox
  /importing a module script failed/i, // Safari
  /unable to preload (css|script)/i, // vite 预加载 helper（css 依赖失败）
  /(chunk|module) load failed/i,
  /loading chunk \S+ failed/i,
];

export function isChunkLoadFailure(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error ?? "");
  return CHUNK_FAILURE_PATTERNS.some((pattern) => pattern.test(text));
}

/** 探测 sessionStorage：被禁用时抛错而不是返回空对象，故必须 try/catch。 */
function browserSessionStorage(): RecoveryStorage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

let handling = false;
let manualNoticeShown = false;

/**
 * 唯一的失败出口：纯决策（`shouldReload`）→ 提示 → 首次则落标记并刷新一次。
 *
 * `handling` 防重入：一次失败可能同时触发 `vite:preloadError` 与 `unhandledrejection`
 * 两条路径（比如动态 import 失败），没有它就会弹两次提示、并可能抢先刷新。
 */
function handleChunkFailure(
  storage: RecoveryStorage | null,
  reason?: unknown,
): "reloaded" | "manual" | "ignored" {
  if (handling) return "ignored";
  handling = true;
  console.error("[chunk-recovery] 分块加载失败（多为前端已重新部署、当前标签页仍是旧版本）：", reason);

  if (shouldReload(storage)) {
    message.warning(RELOAD_NOTICE);
    markReloaded(storage);
    // jsdom/单测环境无 reload 时也不抛错
    window.location?.reload();
    return "reloaded";
  }

  if (!manualNoticeShown) {
    manualNoticeShown = true;
    // duration 0 = 常驻：此时页面可能整块空白，转瞬即逝的 toast 等于没提示
    message.error({ content: MANUAL_REFRESH_NOTICE, duration: 0 });
  }
  return "manual";
}

function onVitePreloadError(event: Event): void {
  // 关键：不 cancel 的话 vite 的 handlePreloadError 会 rethrow，错误继续升级成未捕获异常，
  // 在控制台留下与"已恢复"相矛盾的红色堆栈。
  event.preventDefault();
  const payload = (event as Event & { payload?: unknown }).payload;
  handleChunkFailure(browserSessionStorage(), payload ?? event);
}

function onUnhandledRejection(event: PromiseRejectionEvent): void {
  if (!isChunkLoadFailure(event.reason)) return;
  handleChunkFailure(browserSessionStorage(), event.reason);
}

/**
 * 注册监听。**必须在任何懒加载路由分块被请求之前调用**（`main.tsx` 首行 import，
 * 此时 React 还没挂载，路由分块都还没开始加载）。
 */
export function installChunkErrorRecovery(): void {
  if (typeof window === "undefined") return;
  window.addEventListener("vite:preloadError", onVitePreloadError);
  // 防御性兜底：vite 的 helper 在**老浏览器**下退化为 <link> 预加载，
  // 其 error 分支（package `__vitePreload` 的 modulepreload polyfill）只 reject、**不发事件**。
  // 该分支按 vite 实现只会抛 "Unable to preload CSS for ..."，被上面的文本匹配收住。
  window.addEventListener("unhandledrejection", onUnhandledRejection);
}

installChunkErrorRecovery();
