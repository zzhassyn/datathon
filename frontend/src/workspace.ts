export function validateFile(file: Pick<File, 'name' | 'size'>): string | null {
  if (!/\.(mov|mp4|m4v|webm)$/i.test(file.name)) return 'Выберите видео в формате MOV, MP4, M4V или WebM.';
  if (!file.size) return 'Файл пуст. Выберите другую видеозапись.';
  if (file.size > 500 * 1024 * 1024) return 'Для этого прототипа выберите видео размером до 500 МБ.';
  return null;
}
export function timecode(value: number, precise = false): string {
  const ms = Math.max(0, Math.floor((Number.isFinite(value) ? value : 0) * 1000));
  const seconds = Math.floor(ms / 1000);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor(seconds / 60) % 60;
  const s = seconds % 60;
  return `${h ? `${String(h).padStart(2, '0')}:` : ''}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}${precise ? `.${String(ms % 1000).padStart(3, '0')}` : ''}`;
}
