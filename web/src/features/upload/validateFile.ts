import { ru } from "../../shared/i18n/ru";
export const MAX_FILE_BYTES = 100_000_000;
export function validateFiles(files: Pick<File, "name" | "size">[]) {
  if (files.length !== 1) return ru.multiple;
  const file = files[0];
  if (!/\.(mov|mp4)$/i.test(file.name)) return ru.wrongType;
  if (file.size <= 0) return ru.emptyFile;
  if (file.size > MAX_FILE_BYTES) return ru.tooLarge;
  return null;
}
