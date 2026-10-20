import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { message } from "antd";
import { useAuth } from "@/features/auth";
import {
  fetchWatchlists,
  addWatchlistItem,
  removeWatchlistItem,
  type Watchlist,
} from "@/shared/api/watchlist";
import { useWatchlistStore } from "./store";

/**
 * 自选的两个真相源：**登录后以服务端为准**，未登录用本地 `localStorage`。
 *
 * 2026-10-19 修的三处（都会表现为"加了自选，/me 里没有"）：
 *
 * 1. **登录态未知 ≠ 未登录**。会话探测期间（`isAuthReady=false`，含探测失败/429）
 *    `isAuthenticated` 还是 false —— 旧实现据此把点击写进 localStorage，之后登录态恢复，
 *    这只票**永远不会上传**，`/me` 里也就永远没有。现在写操作只在 `isAuthReady` 之后允许，
 *    未就绪时给提示而不是静默写错地方。
 * 2. **本地自选要合并上传**。未登录时加的自选必须在上线/登录后同步到账户，否则就是
 *    "本地看得见、账户里没有"的孤儿（旧实现从不合并，且登录后本地列表被直接忽略）。
 *    合并走幂等 POST（服务端 `add` 对重复 symbol 是更新而非报错），成功后清本地；
 *    失败的保留在本地，下次再试。
 * 3. **同步期间也要看得见**。合并是异步的，这段时间 `items` = 服务端 ∪ 待同步，
 *    否则刚登录的一瞬间用户会以为自选被清空了。
 */
/**
 * 本地→账户合并的**进程内去重**。
 *
 * 为什么不能放在 hook 里用 ref：一个页面里 `useWatchlist()` 有多个实例
 * （`WatchlistPanel` + `WatchlistTable` + 个股页动作区），各自的 ref 互不可见，
 * 于是同一次登录会对同一只票发 N 次 POST（幂等但会把"已同步 N 只"的提示与请求放大 N 倍）。
 * 以 userId 为键的 in-flight map 让合并全局只跑一次。
 */
const mergeInFlight = new Map<string, Promise<void>>();

/** 稳定的空数组：`data ?? []` 每次渲染都新建数组，会让依赖它的 memo/effect 每帧重跑
 * （匿名用户在个股页会因此进入 setState 循环，页面卡死 —— 2026-10-19 实测）。 */
const EMPTY_WATCHLISTS: Watchlist[] = [];

export function useWatchlist() {
  const queryClient = useQueryClient();
  const { user, isAuthenticated, isAuthReady } = useAuth();
  const localStore = useWatchlistStore();

  // 只有"确定已登录 + 探测已结束"才算账户模式；unknown 一律不写任何一方。
  const accountReady = isAuthReady && isAuthenticated && Boolean(user?.id);

  const {
    data: watchlistsData,
    isLoading,
    refetch,
  } = useQuery<Watchlist[]>({
    queryKey: ["watchlists", user?.id],
    queryFn: fetchWatchlists,
    enabled: accountReady,
    // 30s：写操作会在 commit 后主动失效服务端缓存；这里只防同一页面的重复拉取。
    staleTime: 30 * 1000,
  });
  const watchlists = watchlistsData ?? EMPTY_WATCHLISTS;

  // Extract unique symbols from server watchlists
  const serverSymbols = useMemo(() => {
    if (!accountReady || !watchlists.length) return [];
    const symbols = new Set<string>();
    watchlists.forEach((wl) => {
      wl.items?.forEach((item) => symbols.add(item.symbol));
    });
    return Array.from(symbols);
  }, [accountReady, watchlists]);

  // 正在上传到账户的本地自选（同步窗口内仍要显示）
  const [syncingSymbols, setSyncingSymbols] = useState<string[]>([]);

  useEffect(() => {
    if (!accountReady || !user?.id) {
      // 用"同引用则不动"的写法：`set([])` 每次都给新数组 ⇒ 匿名用户会无限重渲染
      setSyncingSymbols((prev) => (prev.length === 0 ? prev : []));
      return;
    }
    const local = localStore.items;
    if (local.length === 0) return;
    if (mergeInFlight.has(user.id)) return;

    const alreadyOnServer = local.filter((s) => serverSymbols.includes(s));
    if (alreadyOnServer.length > 0) {
      // 云端已有 ⇒ 本地那份是冗余的，直接清掉（不重复上传）
      localStore.setItems(local.filter((s) => !alreadyOnServer.includes(s)));
    }
    const toSync = local.filter((s) => !serverSymbols.includes(s));
    if (toSync.length === 0) return;

    setSyncingSymbols(toSync);
    const job = (async () => {
      const failed: string[] = [];
      for (const symbol of toSync) {
        try {
          await addWatchlistItem({ symbol });
        } catch {
          failed.push(symbol);
        }
      }
      setSyncingSymbols([]);
      localStore.setItems(failed);
      const synced = toSync.length - failed.length;
      if (synced > 0) {
        message.success(`已把 ${synced} 只本地自选同步到账户`);
      }
      if (failed.length > 0) {
        message.warning(`${failed.length} 只本地自选同步失败，稍后会重试`);
      }
      await queryClient.invalidateQueries({ queryKey: ["watchlists", user.id] });
    })();
    mergeInFlight.set(user.id, job);
    void job.finally(() => mergeInFlight.delete(user.id));
  }, [accountReady, localStore, serverSymbols, queryClient, user?.id]);

  const items = accountReady
    ? Array.from(new Set([...serverSymbols, ...syncingSymbols]))
    : localStore.items;

  const addMutation = useMutation({
    mutationFn: (symbol: string) => addWatchlistItem({ symbol }),
    onSuccess: (_, symbol) => {
      queryClient.invalidateQueries({ queryKey: ["watchlists", user?.id] });
      // 自选变了，今日体检的输入也变了（否则切到 triage 还是旧清单）
      queryClient.invalidateQueries({ queryKey: ["triage"] });
      message.success(`已添加 ${symbol} 到自选`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "添加自选失败";
      message.error(msg);
    },
  });

  const removeMutation = useMutation({
    mutationFn: (symbol: string) => removeWatchlistItem(symbol),
    onSuccess: (_, symbol) => {
      queryClient.invalidateQueries({ queryKey: ["watchlists", user?.id] });
      queryClient.invalidateQueries({ queryKey: ["triage"] });
      message.success(`已从自选移除 ${symbol}`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "移除自选失败";
      message.error(msg);
    },
  });

  const toggle = useCallback(
    (symbol: string) => {
      if (!isAuthReady) {
        message.info("正在确认登录状态，请稍后再试");
        return;
      }
      if (isAuthenticated) {
        if (items.includes(symbol)) {
          removeMutation.mutate(symbol);
        } else {
          addMutation.mutate(symbol);
        }
      } else {
        localStore.toggle(symbol);
      }
    },
    [isAuthReady, isAuthenticated, items, addMutation, removeMutation, localStore]
  );

  const remove = useCallback(
    (symbol: string) => {
      if (!isAuthReady) {
        message.info("正在确认登录状态，请稍后再试");
        return;
      }
      if (isAuthenticated) {
        removeMutation.mutate(symbol);
      } else {
        localStore.remove(symbol);
      }
    },
    [isAuthReady, isAuthenticated, removeMutation, localStore]
  );

  const clear = useCallback(async () => {
    if (!isAuthReady) {
      message.info("正在确认登录状态，请稍后再试");
      return;
    }
    if (isAuthenticated) {
      for (const symbol of items) {
        try {
          await removeWatchlistItem(symbol);
        } catch {
          // ignore individual deletion errors during batch clear
        }
      }
      queryClient.invalidateQueries({ queryKey: ["watchlists", user?.id] });
      queryClient.invalidateQueries({ queryKey: ["triage"] });
      message.success("已清空自选股");
    } else {
      localStore.clear();
    }
  }, [isAuthReady, isAuthenticated, items, queryClient, user?.id, localStore]);

  return {
    items,
    watchlists,
    isLoading: accountReady ? isLoading : false,
    toggle,
    remove,
    clear,
    refetch,
    isMutating: addMutation.isPending || removeMutation.isPending,
  };
}
