import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { message } from "antd";
import { useAuth } from "@/features/auth";
import {
  fetchWatchlists,
  addWatchlistItem,
  removeWatchlistItem,
  type Watchlist,
} from "@/shared/api/watchlist";
import { useWatchlistStore } from "./store";

export function useWatchlist() {
  const queryClient = useQueryClient();
  const { user, isAuthenticated } = useAuth();
  const localStore = useWatchlistStore();

  const {
    data: watchlists = [],
    isLoading,
    refetch,
  } = useQuery<Watchlist[]>({
    queryKey: ["watchlists", user?.id],
    queryFn: fetchWatchlists,
    enabled: Boolean(isAuthenticated && user?.id),
    staleTime: 60 * 1000,
  });

  // Extract unique symbols from server watchlists
  const serverSymbols = useMemo(() => {
    if (!isAuthenticated || !watchlists.length) return [];
    const symbols = new Set<string>();
    watchlists.forEach((wl) => {
      wl.items?.forEach((item) => symbols.add(item.symbol));
    });
    return Array.from(symbols);
  }, [isAuthenticated, watchlists]);

  const items = isAuthenticated ? serverSymbols : localStore.items;

  const addMutation = useMutation({
    mutationFn: (symbol: string) => addWatchlistItem({ symbol }),
    onSuccess: (_, symbol) => {
      queryClient.invalidateQueries({ queryKey: ["watchlists", user?.id] });
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
      message.success(`已从自选移除 ${symbol}`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "移除自选失败";
      message.error(msg);
    },
  });

  const toggle = useCallback(
    (symbol: string) => {
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
    [isAuthenticated, items, addMutation, removeMutation, localStore]
  );

  const remove = useCallback(
    (symbol: string) => {
      if (isAuthenticated) {
        removeMutation.mutate(symbol);
      } else {
        localStore.remove(symbol);
      }
    },
    [isAuthenticated, removeMutation, localStore]
  );

  const clear = useCallback(async () => {
    if (isAuthenticated) {
      for (const symbol of items) {
        try {
          await removeWatchlistItem(symbol);
        } catch {
          // ignore individual deletion errors during batch clear
        }
      }
      queryClient.invalidateQueries({ queryKey: ["watchlists", user?.id] });
      message.success("已清空自选股");
    } else {
      localStore.clear();
    }
  }, [isAuthenticated, items, queryClient, user?.id, localStore]);

  return {
    items,
    watchlists,
    isLoading: isAuthenticated ? isLoading : false,
    toggle,
    remove,
    clear,
    refetch,
    isMutating: addMutation.isPending || removeMutation.isPending,
  };
}
