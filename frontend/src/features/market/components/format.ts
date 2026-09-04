const DASH = "—";

export const fmtYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${(v / 1e8).toFixed(digits)}亿`;

export const fmtSignedYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e8).toFixed(digits)}亿`;

export const fmtWanYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿`;

export const fmtWanGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${v.toFixed(0)}万股`;

export const fmtYiGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿股`;

export const fmtNorthYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e4).toFixed(2)}亿`;
