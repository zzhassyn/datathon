export function timecode(value: number) {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  const tenths = Math.floor(safe * 10);
  return `${String(Math.floor(tenths / 600)).padStart(2, "0")}:${String(Math.floor(tenths / 10) % 60).padStart(2, "0")},${tenths % 10}`;
}
export const number = (n: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(n);
export const megabytes = (n: number) => number(n / 1_000_000);
