export type Marker = {
  id: string;
  time: number;
  title: string;
  index: number;
  signal?: boolean;
  provisional?: boolean;
};
export function markerPercent(time: number, duration: number) {
  return duration > 0 ? Math.max(0, Math.min(100, (time / duration) * 100)) : 0;
}
export function clusterMarkers(markers: Marker[], duration: number, width: number) {
  const threshold = Math.max(1.5, width > 0 ? (44 / width) * 100 : 1.5);
  const clusters: { position: number; items: Marker[] }[] = [];
  for (const marker of [...markers].sort((a, b) => a.time - b.time)) {
    const position = markerPercent(marker.time, duration);
    const last = clusters.at(-1);
    if (last && position - last.position < threshold) last.items.push(marker);
    else clusters.push({ position, items: [marker] });
  }
  return clusters;
}
