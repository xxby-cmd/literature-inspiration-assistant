export const cacheKey = "literature-reader-cache-v4",
  progressKey = "literature-reader-progress-v4";
export const cacheAge = 14 * 24 * 60 * 60 * 1000;
export function routeFrom(url: URL) {
  const id = url.searchParams.get("read") || "";
  if (/^[a-f0-9]{32}$/.test(id)) return "read:" + id;
  const paper = url.searchParams.get("paper") || "",
    date = url.searchParams.get("date") || "";
  const batch = url.searchParams.get("batch") || "",
    day = url.searchParams.get("day") || "",
    view = url.searchParams.get("view");
  if (/^[1-5]$/.test(paper) && /^\d{4}-\d{2}-\d{2}$/.test(date))
    return (
      "paper:" +
      date +
      ":" +
      paper +
      (/^[a-f0-9]{32}$/.test(batch) ? ":" + batch : "")
    );
  if (/^[a-f0-9]{32}$/.test(batch)) return "batch:" + batch;
  if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return "day:" + day;
  return view === "research" || view === "shelf" ? view : "today";
}
export function routeUrl(route: string) {
  if (/^read:[a-f0-9]{32}$/.test(route)) return "/?read=" + route.slice(5);
  if (/^batch:[a-f0-9]{32}$/.test(route)) return "/?batch=" + route.slice(6);
  if (/^day:\d{4}-\d{2}-\d{2}$/.test(route)) return "/?day=" + route.slice(4);
  if (route === "research" || route === "shelf") return "/?view=" + route;
  const m = /^paper:(\d{4}-\d{2}-\d{2}):([1-5])(?::([a-f0-9]{32}))?$/.exec(
    route,
  );
  return m
    ? "/?paper=" + m[2] + "&date=" + m[1] + (m[3] ? "&batch=" + m[3] : "")
    : "/";
}
export function liveEntry(entry: any, current = Date.now()) {
  if (!entry?.node || !/^[a-f0-9]{32}$/.test(entry.node.id)) return null;
  if (
    entry.node.kind === "translation" &&
    entry.node.status === "ready" &&
    (!entry.node.expires_at || entry.node.expires_at <= current)
  )
    return {
      ...entry,
      node: { ...entry.node, status: "expired", content: "", html: "" },
    };
  return entry;
}
// Persist only server-rendered non-translation articles. Translations remain in
// memory, and expiry applies even while a different article is on screen.
export function persistentEntries(
  cache: Record<string, any>,
  current = Date.now(),
) {
  const entries = Object.values(cache)
    .filter(
      (e: any) =>
        e?.node?.status === "ready" &&
        e.node.kind !== "translation" &&
        typeof e.node.html === "string" &&
        e.node.html &&
        e.savedAt > current - cacheAge,
    )
    .sort((a: any, b: any) => b.savedAt - a.savedAt)
    .slice(0, 15);
  const result: Record<string, any> = {};
  let size = 0;
  for (const e of entries) {
    const value = { ...e, node: { ...e.node, content: "" } },
      length = JSON.stringify(value).length;
    if (size + length > 800000) continue;
    size += length;
    result[e.node.id] = value;
  }
  return result;
}
