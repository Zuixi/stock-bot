import { apiDelete, apiGet, apiPost, apiPut } from "./client";

export interface WatchlistItem {
  id: string;
  watchlist_id: string;
  symbol: string;
  exchange?: string | null;
  sort_order: number;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Watchlist {
  id: string;
  user_id: string;
  name: string;
  is_default: boolean;
  items: WatchlistItem[];
  created_at: string;
  updated_at: string;
}

export interface AddWatchlistItemPayload {
  symbol: string;
  exchange?: string | null;
  notes?: string | null;
  sort_order?: number | null;
}

export function fetchWatchlists(): Promise<Watchlist[]> {
  return apiGet<Watchlist[]>("/api/v1/watchlists");
}

export function addWatchlistItem(
  payload: AddWatchlistItemPayload,
  watchlistId?: string
): Promise<WatchlistItem> {
  return apiPost<WatchlistItem>(
    "/api/v1/watchlists/items",
    payload,
    { params: watchlistId ? { watchlist_id: watchlistId } : undefined }
  );
}

export function removeWatchlistItem(
  symbol: string,
  watchlistId?: string
): Promise<{ deleted: boolean; symbol: string }> {
  return apiDelete<{ deleted: boolean; symbol: string }>(
    `/api/v1/watchlists/items/${encodeURIComponent(symbol)}`,
    { params: watchlistId ? { watchlist_id: watchlistId } : undefined }
  );
}

export function reorderWatchlistItems(
  symbols: string[],
  watchlistId?: string
): Promise<{ success: boolean }> {
  return apiPut<{ success: boolean }>(
    "/api/v1/watchlists/items/reorder",
    { symbols },
    { params: watchlistId ? { watchlist_id: watchlistId } : undefined }
  );
}
