// One money formatter (mirrors Bricks web lib/money). Compact for large summary figures
// ($2.64M, $850K, $47.3K); exact for payout line items and anything financial.
export function fmtMoneyExact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return "$" + Math.round(Number(v)).toLocaleString("en-CA");
}
const trim = (n: number, d: number) => Number(n.toFixed(d)).toString();
/** $2.64M · $850K · $47.3K · $9,800 (exact below $10K). */
export function fmtMoneyCompact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v), a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${trim(a / 1e9, a >= 1e10 ? 1 : 2)}B`;
  if (a >= 1e6) return `${s}$${trim(a / 1e6, a >= 1e8 ? 0 : a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e4) return `${s}$${trim(a / 1e3, a >= 1e5 ? 0 : 1)}K`;
  return fmtMoneyExact(n);
}
