import { create } from "zustand";
import { persist } from "zustand/middleware";

interface WatchlistState {
  items: string[];
  toggle: (symbol: string) => void;
  remove: (symbol: string) => void;
  clear: () => void;
}

export const useWatchlistStore = create<WatchlistState>()(
  persist(
    (set, get) => ({
      items: [],
      toggle: (symbol) => {
        const { items } = get();
        set({
          items: items.includes(symbol)
            ? items.filter((s) => s !== symbol)
            : [...items, symbol],
        });
      },
      remove: (symbol) => set({ items: get().items.filter((s) => s !== symbol) }),
      clear: () => set({ items: [] }),
    }),
    { name: "stock-bot-watchlist" }
  )
);
