export function contextWindow(time: number, duration: number) {
  const end = Math.min(Math.max(0, duration), Math.max(0, time) + 2);
  return { start: Math.min(end, Math.max(0, time - 2)), end };
}
export type SeekRequest = { time: number; context: boolean };
