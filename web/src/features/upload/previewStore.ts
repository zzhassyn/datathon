let preview: { id: string; url: string } | null = null;
export function clearPreview(id?: string) {
  if (preview && (!id || preview.id === id)) {
    URL.revokeObjectURL(preview.url);
    preview = null;
  }
}
export function setPreview(id: string, file: File) {
  clearPreview();
  preview = { id, url: URL.createObjectURL(file) };
  return preview.url;
}
export function getPreview(id: string) {
  return preview?.id === id ? preview.url : null;
}
