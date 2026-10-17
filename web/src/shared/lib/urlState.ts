export const validJobId = (id: string) => /^[0-9a-f]{32}$/.test(id);
export function parseLocation(search: string) {
  const params = new URLSearchParams(search);
  if (params.has("job")) return { view: "job", id: params.get("job") || "" } as const;
  return { view: params.get("view") === "types" ? "types" : "upload" } as const;
}
export function navigate(href: string) {
  history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
